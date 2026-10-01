/**
 * Flowboard Bridge — Chrome Extension Background Service Worker
 *
 * Connects to the local Python agent over a WebSocket (the agent runs the WS
 * server) and runs Flow's `batchexecute` RPCs inside a signed-in
 * flow.google.com tab, minting a single-use reCAPTCHA for each one.
 *
 * There is NO token here. Google moved Flow to flow.google.com in September
 * 2026 and stopped minting the `Bearer ya29.…` this bridge used to sniff; the
 * rewritten frontend signs every call with the session cookie plus a per-page
 * `at` CSRF token that only the page can read. So the credential never leaves
 * the tab, and nothing works headless — a signed-in Flow tab must stay open.
 *
 * The other two channels (Grok, Shopee) are plain cookie fetches to
 * path-scoped endpoints, and never carry a Google credential.
 */

const AGENT_WS_URL  = 'ws://127.0.0.1:9223';
const CALLBACK_URL  = 'http://127.0.0.1:8101/api/ext/callback';

let ws               = null;

let callbackSecret   = null; // Auth secret received from agent on WS connect
let state            = 'off'; // off | idle | running
let manualDisconnect = false;
let metrics = {
  requestCount:    0,
  successCount:    0,
  failedCount:     0,
  lastError:       null,
  // Captcha counted apart from requests. A solve happens INSIDE a request,
  // so folding it into requestCount would double-count the work and hide
  // the one number that matters when generation stalls: whether the page is
  // still handing back tokens at all.
  captchaCount:    0,
  captchaFailed:   0,
  lastCaptchaAt:   null,
  lastCaptchaError: null,
};

// Both hosts. `flow.google.com` is where the app lives; the `labs.google`
// patterns are for a tab someone pinned before the move, which still
// redirects. Every tab lookup in this file goes through this list — a bridge
// that hardcodes one hostname breaks silently the day the product moves,
// which is exactly what happened here (a 206-hour-old token, while the user
// was signed in the whole time).
const flowUrls = [
  'https://labs.google/fx/tools/flow*',
  'https://labs.google/fx/*/tools/flow*',
  'https://flow.google.com/*',
];

// ─── Request Log (last 50 entries) ─────────────────────────

let requestLog = [];

function addRequestLog(entry) {
  requestLog.unshift(entry);
  if (requestLog.length > 50) requestLog.pop();
  broadcastRequestLog();
}

function updateRequestLog(id, updates) {
  const entry = requestLog.find((e) => e.id === id);
  if (entry) Object.assign(entry, updates);
  broadcastRequestLog();
}

function broadcastRequestLog() {
  chrome.runtime.sendMessage({ type: 'REQUEST_LOG_UPDATE', log: requestLog }).catch(() => {});
}

// ─── Startup ────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(init);
chrome.runtime.onStartup.addListener(init);

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'reconnect') connectToAgent();
  if (alarm.name === 'keepAlive') keepAlive();
});

async function init() {
  // Note: deliberately not restoring `userInfo` from storage. We used
  // to persist it here, but Google profile fields (name + email) are
  // PII and chrome.storage.local is plaintext + readable by other
  // extensions on the profile that hold the `storage` permission.
  const data = await chrome.storage.local.get(['metrics', 'callbackSecret']);
  if (data.metrics)        Object.assign(metrics, data.metrics);
  if (data.callbackSecret) callbackSecret = data.callbackSecret;
  connectToAgent();
  chrome.alarms.create('keepAlive', { periodInMinutes: 0.4 });
}

// ─── WebSocket to Agent ─────────────────────────────────────

function connectToAgent() {
  if (manualDisconnect) return;
  if (ws?.readyState === WebSocket.CONNECTING) return;
  if (ws?.readyState === WebSocket.OPEN) return;

  try {
    ws = new WebSocket(AGENT_WS_URL);
  } catch (e) {
    console.error('[Flowboard] WS connect error:', e);
    scheduleReconnect();
    return;
  }

  ws.onopen = () => {
    console.log('[Flowboard] Connected to agent');
    chrome.alarms.clear('reconnect');
    setState('idle');

    // No token to announce any more: Flow signs its own calls inside the tab.
    // What the agent needs to know is which build is here — without it,
    // "reload the extension" and "the extension was reloaded" are
    // indistinguishable from the agent side, which cost an afternoon of blind
    // retries. Whether that tab can actually sign anything is a separate
    // question, answered on demand by the `flow_probe` method.
    ws.send(JSON.stringify({
      type: 'extension_ready',
      version: chrome.runtime.getManifest().version,
      transport: 'batch',
    }));
  };

  ws.onmessage = async ({ data }) => {
    try {
      const msg = JSON.parse(data);

      if (msg.type === 'callback_secret') {
        callbackSecret = msg.secret;
        chrome.storage.local.set({ callbackSecret: msg.secret });
        console.log('[Flowboard] Received callback secret');
      } else if (msg.type === 'pong') {
        // keepalive response — no-op
      } else if (msg.type === 'logout') {
        // Nothing left to drop: this worker holds no Google credential. The
        // session lives in the browser's own cookie jar, so signing out is
        // something the user does on flow.google.com, not something the agent
        // can do on their behalf. Acknowledged rather than silently ignored.
        console.log('[Flowboard] logout requested — no credential is held here');
      } else if (msg.method === 'batch_rpc') {
        await handleBatchRpc(msg);
      } else if (msg.method === 'flow_probe') {
        sendToAgent({ id: msg.id, result: await runFlowProbe() });
      } else if (msg.method === 'trpc_request') {
        await handleTrpcRequest(msg);
      } else if (msg.method === 'grok_fetch') {
        await handleGrokFetch(msg);
      } else if (msg.method === 'solve_captcha') {
        // The solver already ran per request; what it never had was a way to
        // be exercised on its own. Without that, "generation is stuck" and
        // "the page stopped issuing captcha tokens" look identical from the
        // agent side, and the second one is the actionable half.
        const action = (msg.params && msg.params.action) || 'VIDEO_GENERATION';
        const started = Date.now();
        let result;
        try {
          result = await solveCaptcha(msg.id, action);
        } catch (e) {
          result = { error: e?.message || 'CAPTCHA_FAILED' };
        }
        const ok = !!(result && result.token && !result.error);
        metrics.captchaCount += 1;
        metrics.lastCaptchaAt = Date.now();
        if (ok) {
          metrics.lastCaptchaError = null;
        } else {
          metrics.captchaFailed += 1;
          metrics.lastCaptchaError = (result && result.error) || 'NO_TOKEN';
        }
        sendToAgent({
          id: msg.id,
          result: {
            ok,
            action,
            elapsedMs: Date.now() - started,
            // The token itself is never sent back or logged. What the caller
            // needs is whether one was issued and how long it took; the value
            // is a credential and belongs only in the request that uses it.
            tokenLength: ok ? String(result.token).length : 0,
            error: ok ? null : metrics.lastCaptchaError,
          },
        });
      } else if (msg.method === 'get_status') {
        sendToAgent({
          id: msg.id,
          result: {
            state,
            transport: 'batch',
            manualDisconnect,
            metrics,
          },
        });
      }
    } catch (e) {
      console.error('[Flowboard] Message error:', e);
    }
  };

  ws.onclose = () => {
    setState('off');
    if (!manualDisconnect) scheduleReconnect();
  };

  ws.onerror = (e) => {
    console.error('[Flowboard] WS error:', e);
    metrics.lastError = 'WS_ERROR';
    chrome.storage.local.set({ metrics });
  };
}

function scheduleReconnect() {
  chrome.alarms.create('reconnect', { delayInMinutes: 0.083 }); // ~5 s
}

function keepAlive() {
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'ping' }));
  } else {
    connectToAgent();
  }
}

// ─── Send to Agent ──────────────────────────────────────────

/**
 * Route a message to the agent.
 * Responses (msg.id present) go via HTTP callback — immune to WS drops.
 * Falls back to WS on HTTP failure. Non-response messages use WS directly.
 */
function sendToAgent(msg) {
  if (msg.id) {
    fetch(CALLBACK_URL, {
      method:  'POST',
      headers: {
        'Content-Type':      'application/json',
        'X-Callback-Secret': callbackSecret || '',
      },
      body: JSON.stringify(msg),
    }).catch(() => {
      // HTTP failed — fall back to WS
      if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
    });
    return;
  }
  // Non-response messages (ping, status)
  if (ws?.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(msg));
  }
}

// ─── Page-context RPC runner (the batchexecute transport) ───
//
// Flow's rewritten frontend signs every call with the session cookie plus a
// per-page `at` token, and a generate also carries a SINGLE-USE reCAPTCHA. None
// of that can be replayed from this service worker, so the request has to be
// issued by the Flow page itself: mint a fresh captcha through the grecaptcha
// bridge, then run the batchexecute POST in the page's MAIN world, where
// `at` / `f.sid` / `bl` live.
//
// Ported from flowkit (https://github.com/crisng95/flowkit, MIT) v1.2.0
// commit 57b52e6, extension/background.js. The MAIN-world function must stay
// SELF-CONTAINED — it is serialised across the world boundary, so a reference
// to anything in this file comes back as NO_INJECTION_RESULT.

const CAPTCHA_SLOT = '__CAPTCHA__';
const MAX_RPC_TEXT = 32000000; // the project listing alone is past 17 MB

async function runBatchRpc(cmd) {
  const tabs = await chrome.tabs.query({ url: flowUrls });
  let candidate = tabs.find((t) => !t.discarded) || tabs[0];
  if (!candidate) {
    // No Flow tab — open one and give the app a moment to boot, otherwise
    // WIZ_global_data is not on the page yet and `at` comes back empty. Keep
    // the exact created tab id so redirects/stale tabs cannot hijack recovery.
    let opened;
    try {
      opened = await chrome.tabs.create({ url: FLOW_URL, active: false });
      await sleep(5000);
      candidate = opened?.id ? await chrome.tabs.get(opened.id).catch(() => null) : null;
    } catch (e) {
      return { error: e?.message || 'NO_FLOW_TAB' };
    }
    if (!candidate) return { error: 'NO_FLOW_TAB' };
  }
  // Chrome discards backgrounded tabs; executeScript throws on a dead one.
  const tab = await reviveTabIfNeeded(candidate);
  if (!tab) return { error: 'FLOW_TAB_DISCARDED' };

  let freq = cmd.freq;
  if (cmd.captchaAction) {
    const solved = await solveCaptcha(cmd.id, cmd.captchaAction);
    if (!solved?.token) return { error: `CAPTCHA_FAILED: ${solved?.error || 'no token'}` };
    freq = freq.split(CAPTCHA_SLOT).join(solved.token);
  }

  const [injected] = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    world: 'MAIN',
    args: [cmd.rpcid, freq, MAX_RPC_TEXT, cmd.match || null],
    func: async (rpcid, freqStr, maxText, match) => {
      const wiz = globalThis.WIZ_global_data || {};
      const at = wiz.SNlM0e;
      const sid = wiz.FdrFJe;
      const bl = wiz.cfb2h;
      if (!at) return { error: 'NO_AT_TOKEN' };
      const reqid = Math.floor(Math.random() * 900000) + 100000;
      // Match Flow's own WIZ metadata. GEM_PIX_2 (Nano Banana Pro) rejects
      // image generation when source-path is missing even though Lite may not.
      const sourcePath = location.pathname || '/';
      const hl = (document.documentElement.lang || navigator.language || 'en').split('-')[0];
      const url =
        `/_/AiSandboxAngularFrontend/data/batchexecute?rpcids=${encodeURIComponent(rpcid)}` +
        `&source-path=${encodeURIComponent(sourcePath)}` +
        `&bl=${encodeURIComponent(bl || '')}&f.sid=${encodeURIComponent(sid || '')}` +
        `&hl=${encodeURIComponent(hl)}&_reqid=${reqid}&rt=c`;
      const resp = await fetch(url, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'content-type': 'application/x-www-form-urlencoded;charset=UTF-8',
          'x-same-domain': '1',
        },
        body: new URLSearchParams({ 'f.req': freqStr, at }),
      });
      const text = await resp.text();
      // The project listing is tens of megabytes and all we ever want from it
      // is one entry. Cutting it down here keeps that payload inside the tab
      // instead of pushing it through the bridge on every poll.
      if (match) {
        const found = text.indexOf(match);   // not `at` — that is the CSRF token above
        return {
          status: resp.status,
          matched: found !== -1,
          text: found === -1 ? '' : text.slice(found, found + 800),
        };
      }
      return { status: resp.status, text: text.slice(0, maxText) };
    },
  });

  return injected?.result || { error: 'NO_INJECTION_RESULT' };
}

async function handleBatchRpc(msg) {
  const { id, params } = msg;
  const { rpcid, freq, captchaAction, match } = params || {};
  if (!rpcid || !freq) {
    sendToAgent({ id, status: 400, error: 'INVALID_BATCH_RPC' });
    return;
  }

  setState('running');
  const hasCaptcha = !!captchaAction;
  if (hasCaptcha) metrics.requestCount++;
  // Polls and listing lookups run constantly; only the generates are worth a
  // row in the log the popup shows.
  if (hasCaptcha) {
    addRequestLog({
      id,
      type: `RPC:${rpcid}`,
      time: new Date().toISOString(),
      status: 'processing',
      error: null,
      outputUrl: null,
      url: rpcid,
      // The envelope, truncated. NEVER the `at` token or a captcha token —
      // those live in the page and in the substituted freq respectively, and
      // the substitution happens after this line.
      payloadSummary: freq.slice(0, 200),
    });
  }

  try {
    const out = await runBatchRpc({ id, rpcid, freq, captchaAction, match });
    if (out.error) {
      if (hasCaptcha) { metrics.failedCount++; metrics.lastError = out.error; }
      if (hasCaptcha) updateRequestLog(id, { status: 'failed', error: out.error });
      sendToAgent({ id, status: 502, error: out.error });
    } else {
      if (hasCaptcha) { metrics.successCount++; metrics.lastError = null; }
      if (hasCaptcha) {
        updateRequestLog(id, {
          status: 'success',
          httpStatus: out.status,
          responseSummary: (out.text || '').slice(0, 300),
        });
      }
      sendToAgent({ id, status: out.status, data: out.text });
    }
  } catch (e) {
    const err = e?.message || 'BATCH_RPC_FAILED';
    if (hasCaptcha) { metrics.failedCount++; metrics.lastError = err; }
    if (hasCaptcha) updateRequestLog(id, { status: 'failed', error: err });
    sendToAgent({ id, status: 500, error: err });
  }

  chrome.storage.local.set({ metrics });
  setState('idle');
}

// ─── Flow tab probe ─────────────────────────────────────────────
//
// Answers the one question the agent can no longer answer for itself: is there
// a signed-in Flow tab that can sign an RPC? Before the migration the agent
// held a Bearer token and could tell by using it; now the credential never
// leaves the page, so "connected" and "able to generate" became two different
// things with no way to distinguish them.
//
// Reads PRESENCE, never values: whether `at` exists, not what it is.

async function runFlowProbe() {
  const tabs = await chrome.tabs.query({ url: flowUrls });
  const candidate = tabs.find((t) => !t.discarded) || tabs[0];
  if (!candidate) return { flowTabPresent: false, atTokenPresent: false, error: 'NO_FLOW_TAB' };
  const tab = await reviveTabIfNeeded(candidate);
  if (!tab) return { flowTabPresent: true, atTokenPresent: false, error: 'FLOW_TAB_DISCARDED' };

  try {
    const [injected] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: 'MAIN',
      func: () => {
        const wiz = globalThis.WIZ_global_data || {};
        // Which WIZ key carries the signed-in email is not known, so report
        // whether ANY value looks like one rather than guessing a key name.
        // Only the boolean travels — never the address.
        let emailPresent = false;
        try {
          emailPresent = Object.values(wiz).some(
            (v) => typeof v === 'string' && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(v),
          );
        } catch { /* a hostile WIZ shape is not worth failing the probe over */ }
        return {
          atTokenPresent: !!wiz.SNlM0e,
          sourcePath: location.pathname || '/',
          host: location.host,
          emailPresent,
        };
      },
    });
    const result = injected?.result;
    if (!result) return { flowTabPresent: true, atTokenPresent: false, error: 'NO_INJECTION_RESULT' };
    return { flowTabPresent: true, ...result };
  } catch (e) {
    return { flowTabPresent: true, atTokenPresent: false, error: e?.message || 'PROBE_FAILED' };
  }
}







// ─── Flow tab ────────────────────────────────

let _openingFlowTab = false;

// Every RPC runs inside this tab now, so it is not an implementation detail
// of a token refresh any more — it is where the whole transport lives.
// Nothing here works headless.
const FLOW_URL = 'https://flow.google.com/';

/**
 * Open a Flow tab even when Chrome has zero windows. `chrome.tabs.create`
 * throws "No current window" in that state because it needs a window
 * context to attach to; `chrome.windows.create` spawns a fresh window
 * and tab in one call. Falls back through both paths so we recover from
 * "all-windows-closed but service-worker-still-alive" silently.
 */
async function openFlowTabResilient(active = false) {
  try {
    return await chrome.tabs.create({ url: FLOW_URL, active });
  } catch (e) {
    const msg = e?.message || '';
    if (!msg.includes('No current window')) throw e;
    console.log('[Flowboard] No Chrome window — spawning a fresh one for Flow');
    const win = await chrome.windows.create({
      url: FLOW_URL,
      focused: false,
      state: 'minimized',
    });
    return win.tabs?.[0] ?? null;
  }
}

// ─── reCAPTCHA Solving ──────────────────────────────────────

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function requestCaptchaFromTab(tabId, requestId, pageAction) {
  try {
    return await chrome.tabs.sendMessage(tabId, {
      type: 'GET_CAPTCHA',
      requestId,
      pageAction,
    });
  } catch (error) {
    const msg = error?.message || '';
    const shouldInject =
      msg.includes('Receiving end does not exist') ||
      msg.includes('Could not establish connection');
    if (!shouldInject) throw error;

    // Inject content script and retry. Both the inject + re-send can
    // throw "No current window" / "No tab with id" if the tab dies in
    // between (Chrome aggressively discards background tabs). Surface
    // those verbatim so solveCaptcha's loop can move to the next
    // candidate instead of bubbling a confusing message to the user.
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ['content.js'],
    });
    await sleep(200);
    return await chrome.tabs.sendMessage(tabId, {
      type: 'GET_CAPTCHA',
      requestId,
      pageAction,
    });
  }
}

/** Try to wake a discarded Flow tab so `sendMessage` can reach it.
 *  Chrome auto-discards backgrounded tabs to save memory; the tab still
 *  shows up in `chrome.tabs.query` but cross-context calls fail with
 *  "No current window" / "No tab with id". A reload re-hydrates it. */
async function reviveTabIfNeeded(tab) {
  if (!tab?.discarded) return tab;
  try {
    await chrome.tabs.reload(tab.id);
    await sleep(2500);
    const fresh = await chrome.tabs.get(tab.id);
    return fresh;
  } catch {
    return null;
  }
}

async function solveCaptcha(requestId, captchaAction) {
  const tabs = await chrome.tabs.query({ url: flowUrls });

  // No Flow tab at all — spawn one (handles "no Chrome window" via the
  // resilient helper).
  if (!tabs.length) {
    try {
      await openFlowTabResilient(false);
      await sleep(3000);
    } catch (e) {
      return { error: e.message || 'NO_FLOW_TAB' };
    }
  }

  // Try each Flow tab in turn — gracefully skip dead/discarded ones
  // instead of bubbling "No current window" up to the user. Re-query
  // because we might have just spawned a new one above.
  const candidates = await chrome.tabs.query({ url: flowUrls });
  const errors = [];
  for (const tab of candidates) {
    const live = await reviveTabIfNeeded(tab);
    if (!live) continue;
    try {
      const resp = await Promise.race([
        requestCaptchaFromTab(live.id, requestId, captchaAction),
        new Promise((_, rej) => setTimeout(() => rej(new Error('CAPTCHA_TIMEOUT')), 30000)),
      ]);
      return resp;
    } catch (e) {
      const msg = e?.message || '';
      errors.push(msg);
      // Tab evaporated mid-call (window closed, tab discarded again,
      // or page navigated away). Move on to the next candidate.
      if (
        msg.includes('No current window') ||
        msg.includes('No tab with id') ||
        msg.includes('Receiving end does not exist')
      ) {
        continue;
      }
      return { error: msg };
    }
  }

  // All candidates failed — last-ditch: spawn a fresh Flow tab and try
  // it once. This handles the case where every existing Flow tab was
  // in a closed window we couldn't recover from.
  try {
    await openFlowTabResilient(false);
    await sleep(3000);
    const fresh = await chrome.tabs.query({ url: flowUrls });
    const target = fresh.find((t) => !t.discarded) || fresh[0];
    if (!target) return { error: 'NO_FLOW_TAB' };
    const resp = await Promise.race([
      requestCaptchaFromTab(target.id, requestId, captchaAction),
      new Promise((_, rej) => setTimeout(() => rej(new Error('CAPTCHA_TIMEOUT')), 30000)),
    ]);
    return resp;
  } catch (e) {
    const msg = e?.message || (errors[0] ?? 'NO_FLOW_TAB');
    return { error: msg };
  }
}

// Endpoints this bridge may call with the user's own session cookies.
//
// Each entry is a path prefix, not a domain: `grok.com/*` would let the agent
// reach account settings, and `labs.google/fx/api/trpc/` alone already covers
// mutations like account.deleteAccount that should stay server-gated.
//
// `bearer` says whether the captured Google access token may ride along.
// It must be true for Flow ONLY — sending it to grok.com or shopee.vn would
// hand a third party the credential this whole tool depends on.
// Two entries, and no `bearer` flag any more: there is no Google credential in
// this worker to attach to anything. Flow's own tRPC paths left this list
// because the migration unauthenticated them — Flow is reached through
// `batch_rpc`, which runs inside the signed-in page instead of proxying from
// here. `upload-video` went with them (see the Edit Video gap in
// plans/reports/gaps-260918-1729-flow-batch-migration.md).
const SESSION_CHANNELS = [
  // Grok generates from the logged-in grok.com session, the same way the
  // packaged tool does — it drives a real browser to reach these very paths.
  { prefix: 'https://grok.com/rest/' },
  // Shopee's own product API, which answers properly only for a signed-in
  // session. That is why scraping the public page returns nothing useful.
  { prefix: 'https://shopee.vn/api/v4/' },
];

function channelFor(url) {
  if (!url) return null;
  return SESSION_CHANNELS.find((c) => url.startsWith(c.prefix)) || null;
}

async function handleTrpcRequest(msg) {
  const { id, params } = msg;
  const { url, method = 'POST', headers = {}, body } = params;

  const channel = channelFor(url);
  if (!channel) {
    sendToAgent({ id, error: 'INVALID_TRPC_URL' });
    return;
  }

  setState('running');
  // Session calls are silent — don't add to request log, don't bump metrics

  const fetchHeaders = { 'Content-Type': 'application/json', ...headers };
  // No Authorization header on this path, ever. It used to carry the captured
  // Google token for Flow's own tRPC endpoints; those left this bridge with the
  // migration, and the two channels that remain are third-party sites that must
  // never see a Google credential.

  // No redirect-chasing branch any more. It existed for Flow's
  // `media.getMediaUrlRedirect`, which answered a media id with a 302 to a
  // signed storage.googleapis.com url that could not be followed from an
  // extension origin. That endpoint went with the migration; the batch path
  // asks the `as29s` RPC instead and gets the url in plain JSON.
  try {
    const resp = await fetch(url, {
      method,
      headers: fetchHeaders,
      body:    body ? JSON.stringify(body) : undefined,
      credentials: 'include',
    });
    const finalUrl = resp.url && resp.url !== url ? resp.url : null;
    const contentType = resp.headers.get('content-type') || '';
    const data = contentType.includes('application/json') ? await resp.json() : null;
    sendToAgent({ id, status: resp.status, data, finalUrl, contentType });
  } catch (e) {
    console.error('[Flowboard] session request failed:', e);
    sendToAgent({ id, error: e.message || 'SESSION_FETCH_FAILED' });
  } finally {
    setState('idle');
  }
}

// ─── Grok (page-context bridge) ─────────────────────────────
//
// Grok's generation calls cannot be made from here. Its endpoints want
// per-session headers only the running app knows, and they answer with a
// stream of concatenated JSON rather than one document. So the request is
// handed to a content script on an open grok.com tab, which runs it in the
// page — the same thing the packaged tool achieves by driving Chrome over CDP.

const GROK_TAB_URLS = ['https://grok.com/*'];

// grok.com stamps its own API calls with per-session headers (a Statsig id
// among them) that the page computes at runtime. A request without them is
// answered 403 even from inside the page, which is why the packaged tool
// passes `statsigHeaders` into the script it injects — it harvested them
// first.
//
// Rather than recompute anything, watch what grok's own client sends and
// reuse it. Same trick the Google token capture above already relies on.
let grokHeaders = {};

// Never copy these: the browser attaches cookies itself, and replaying a
// stale length/encoding against a different body corrupts the request.
const GROK_HEADER_SKIP = new Set([
  'cookie',
  'content-length',
  'content-type',
  'accept-encoding',
  'host',
  'connection',
]);

chrome.webRequest.onBeforeSendHeaders.addListener(
  (details) => {
    const seen = {};
    for (const h of details.requestHeaders || []) {
      const name = h.name.toLowerCase();
      if (GROK_HEADER_SKIP.has(name)) continue;
      // The session-identifying ones are all x-* plus the app's own referer.
      if (name.startsWith('x-') || name === 'referer' || name === 'origin') {
        if (h.value) seen[name] = h.value;
      }
    }
    // x-xai-request-id is per-call; keeping it would replay one id forever.
    delete seen['x-xai-request-id'];
    if (Object.keys(seen).length) grokHeaders = seen;
  },
  { urls: ['https://grok.com/rest/*'] },
  ['requestHeaders', 'extraHeaders'],
);

async function handleGrokFetch(msg) {
  const { id, params } = msg;
  const { url, method = 'POST', body, stream = false } = params || {};

  const tabs = await chrome.tabs.query({ url: GROK_TAB_URLS });
  if (!tabs.length) {
    // Not an error worth retrying — the user has to be signed in and present.
    sendToAgent({ id, error: 'GROK_NO_TAB' });
    return;
  }

  setState('running');
  try {
    const result = await chrome.tabs.sendMessage(tabs[0].id, {
      type: 'GROK_FETCH',
      requestId: `grok-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      url,
      method,
      body,
      stream,
      // Whatever grok's own client last sent. Empty until the page has made
      // one API call of its own, which is why a fresh tab can still 403.
      sessionHeaders: grokHeaders,
    });
    if (!result) {
      sendToAgent({ id, error: 'GROK_NO_RESPONSE' });
      return;
    }
    sendToAgent({ id, ...result });
  } catch (e) {
    // Usually means the content script isn't in that tab yet (it loads at
    // document_idle) or the tab was discarded.
    sendToAgent({ id, error: (e && e.message) || 'GROK_BRIDGE_UNREACHABLE' });
  } finally {
    setState('idle');
  }
}

// ─── State & Badge ──────────────────────────────────────────

function setState(newState) {
  state = newState;
  const badges = { idle: '●', running: '▶', off: '○' };
  const colors  = { idle: '#22c55e', running: '#f5b301', off: '#6b7280' };
  chrome.action.setBadgeText({ text: badges[newState] || '' });
  chrome.action.setBadgeBackgroundColor({ color: colors[newState] || '#000' });
  broadcastStatus();
}

function broadcastStatus() {
  chrome.runtime.sendMessage({ type: 'STATUS_PUSH' }).catch(() => {});
}

// ─── Popup Message Handlers ─────────────────────────────────

chrome.runtime.onMessage.addListener((msg, _, reply) => {
  if (msg.type === 'STATUS') {
    reply({
      connected:       ws?.readyState === WebSocket.OPEN,
      transport:       'batch',
      manualDisconnect,
      metrics: {
        requestCount: metrics.requestCount,
        successCount: metrics.successCount,
        failedCount:  metrics.failedCount,
        lastError:    metrics.lastError,
        captchaCount:  metrics.captchaCount,
        captchaFailed: metrics.captchaFailed,
        lastCaptchaError: metrics.lastCaptchaError,
      },
      state,
    });
    return true;
  }

  if (msg.type === 'TEST_CAPTCHA') {
    // Runs the same solver a real generation uses, on the same Flow tab, and
    // reports only whether a token came back. Answers the question the popup
    // could not answer before: is the page still issuing captcha tokens, or is
    // generation stalling for some other reason entirely?
    (async () => {
      const action = msg.action || 'VIDEO_GENERATION';
      const started = Date.now();
      let result;
      try {
        result = await solveCaptcha(`test-${started}`, action);
      } catch (e) {
        result = { error: e?.message || 'CAPTCHA_FAILED' };
      }
      const ok = !!(result && result.token && !result.error);
      metrics.captchaCount += 1;
      metrics.lastCaptchaAt = Date.now();
      if (ok) {
        metrics.lastCaptchaError = null;
      } else {
        metrics.captchaFailed += 1;
        metrics.lastCaptchaError = (result && result.error) || 'NO_TOKEN';
      }
      chrome.storage.local.set({ metrics });
      // The token never leaves this function: its length says the solver
      // worked, and the value is a credential.
      reply({
        ok,
        action,
        elapsedMs: Date.now() - started,
        tokenLength: ok ? String(result.token).length : 0,
        error: ok ? null : metrics.lastCaptchaError,
      });
    })();
    return true;  // async reply
  }

  if (msg.type === 'DISCONNECT') {
    manualDisconnect = true;
    ws?.close();
    reply({ ok: true });
    return true;
  }

  if (msg.type === 'RECONNECT') {
    manualDisconnect = false;
    connectToAgent();
    reply({ ok: true });
    return true;
  }

  if (msg.type === 'REQUEST_LOG') {
    reply({ log: requestLog });
    return true;
  }

  if (msg.type === 'OPEN_FLOW_TAB') {
    chrome.tabs.query({
      url: flowUrls,
    }).then(async (tabs) => {
      try {
        if (tabs.length) {
          await chrome.tabs.update(tabs[0].id, { active: true });
          reply({ ok: true, tabId: tabs[0].id });
        } else {
          // User-initiated → focus the new window so they can see it.
          const tab = await openFlowTabResilient(true);
          reply({ ok: true, tabId: tab?.id });
        }
      } catch (e) {
        reply({ error: e.message });
      }
    }).catch((e) => reply({ error: e.message }));
    return true;
  }

  if (msg.type === 'CHECK_FLOW_TAB') {
    // Replaces "Refresh token". There is no token to refresh; what someone
    // staring at a stalled run needs to know is whether a Flow tab is open and
    // able to sign an RPC at all. Reads presence, never values.
    runFlowProbe()
      .then((r) => reply(r))
      .catch((e) => reply({ error: e?.message || 'PROBE_FAILED' }));
    return true;
  }

  return true;
});

console.log('[Flowboard] Extension loaded');

