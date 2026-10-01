let _testingCaptcha = false;
let _probing = false;
let _lastProbe = null;

/**
 * Flowboard Bridge — Popup UI
 * Polls background status every 1.5 s and renders it.
 */

let _manualDisconnect = false;

// Never a token age any more: there is no token. What matters is whether the
// Flow tab can sign an RPC, which only a probe can answer.
function describeFlowTab(probe) {
  if (!probe) return 'chưa kiểm';
  if (probe.error && !probe.atTokenPresent) {
    return probe.flowTabPresent ? `lỗi: ${probe.error}` : 'chưa mở tab Flow';
  }
  return probe.atTokenPresent ? 'đã đăng nhập ✓' : 'có tab, chưa đăng nhập';
}

function render(status) {
  if (!status) return;

  _manualDisconnect = status.manualDisconnect;

  // Status dot
  const dotEl = document.getElementById('status-dot');
  if (status.manualDisconnect || !status.connected) {
    dotEl.className = 'section-value dot-offline';
    dotEl.textContent = '○ offline';
  } else if (status.state === 'running') {
    dotEl.className = 'section-value dot-running';
    dotEl.textContent = '▶ running';
  } else {
    dotEl.className = 'section-value dot-connected';
    dotEl.textContent = '● connected';
  }

  // Flow tab — filled by the probe, not by the poll: it costs an
  // executeScript into the page, so it runs on demand rather than every 1.5 s.
  if (!_probing && _lastProbe) {
    document.getElementById('flow-tab-row').textContent = describeFlowTab(_lastProbe);
  }

  // Stats
  const m = status.metrics || {};
  document.getElementById('stats-row').textContent =
    `${m.requestCount || 0} · ✓ ${m.successCount || 0} · ✗ ${m.failedCount || 0}`;

  // Captcha. Shown next to requests rather than inside them: a solve happens
  // inside a request, so a stalled generation and a page that stopped issuing
  // tokens look identical in the request counters alone.
  const captchaEl = document.getElementById('captcha-row');
  if (!_testingCaptcha) {
    const base = `${m.captchaCount || 0} · ✗ ${m.captchaFailed || 0}`;
    captchaEl.textContent = m.lastCaptchaError ? `${base} · ${m.lastCaptchaError}` : base;
  }

  // Error
  const errSection = document.getElementById('error-section');
  const errRow     = document.getElementById('error-row');
  if (m.lastError) {
    errSection.style.display = 'flex';
    errRow.textContent = m.lastError;
  } else {
    errSection.style.display = 'none';
  }

  // Toggle button
  const btn = document.getElementById('btn-toggle');
  if (status.manualDisconnect) {
    btn.textContent = 'Reconnect';
    btn.className   = 'reconnect';
  } else {
    btn.textContent = 'Disconnect';
    btn.className   = 'disconnect';
  }
}

function fetchStatus() {
  chrome.runtime.sendMessage({ type: 'STATUS' }, (reply) => {
    if (chrome.runtime.lastError) return;
    render(reply);
  });
}

// Initial fetch + poll
fetchStatus();
setInterval(fetchStatus, 1500);

// Re-render on push from background
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === 'STATUS_PUSH') fetchStatus();
});

// Buttons
document.getElementById('btn-flow-tab').addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'OPEN_FLOW_TAB' });
});

document.getElementById('btn-check-tab').addEventListener('click', () => {
  const row = document.getElementById('flow-tab-row');
  const btn = document.getElementById('btn-check-tab');
  _probing = true;
  btn.disabled = true;
  row.textContent = 'đang kiểm…';
  chrome.runtime.sendMessage({ type: 'CHECK_FLOW_TAB' }, (probe) => {
    btn.disabled = false;
    _probing = false;
    _lastProbe = chrome.runtime.lastError ? null : probe;
    row.textContent = chrome.runtime.lastError
      ? 'không hỏi được background'
      : describeFlowTab(probe);
  });
});

// While a test is in flight the poll must not overwrite its result line.
document.getElementById('btn-test-captcha').addEventListener('click', () => {
  const btn = document.getElementById('btn-test-captcha');
  const row = document.getElementById('captcha-row');
  _testingCaptcha = true;
  btn.disabled = true;
  row.textContent = 'testing…';
  chrome.runtime.sendMessage({ type: 'TEST_CAPTCHA' }, (reply) => {
    btn.disabled = false;
    if (chrome.runtime.lastError || !reply) {
      row.textContent = 'test failed: no reply from background';
      _testingCaptcha = false;
      return;
    }
    // Token length, never the token.
    row.textContent = reply.ok
      ? `ok · ${reply.elapsedMs} ms · token ${reply.tokenLength} chars`
      : `failed · ${reply.error || 'unknown'}`;
    // Hold the result on screen long enough to read before the poll resumes.
    setTimeout(() => { _testingCaptcha = false; fetchStatus(); }, 6000);
  });
});

document.getElementById('btn-toggle').addEventListener('click', () => {
  const type = _manualDisconnect ? 'RECONNECT' : 'DISCONNECT';
  chrome.runtime.sendMessage({ type }, () => {
    if (chrome.runtime.lastError) return;
    fetchStatus();
  });
});

// The version people read has to be the version that is loaded; keeping a
// second copy in the markup is how it went stale.
document.getElementById('header-version').textContent =
  `v${chrome.runtime.getManifest().version}`;
