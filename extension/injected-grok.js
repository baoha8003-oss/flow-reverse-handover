/**
 * Injected into the MAIN world on grok.com.
 *
 * Why the page and not the service worker: grok.com's own client sends
 * per-session headers (statsig / conversation ids) that only the running app
 * knows, and its generation endpoints answer with a STREAM of concatenated
 * JSON objects rather than one document. A background fetch has neither the
 * headers nor a natural way to read that stream.
 *
 * The packaged tool solves this by driving a real Chrome over CDP and
 * evaluating JavaScript inside the page. This is the same idea with the
 * extension's own machinery: run in the page, reuse its origin and cookies,
 * and hand the parsed result back over a CustomEvent.
 */

// Path-scoped, as in the background worker. `/api/auth/session` is grok's own
// "who am I" call — the packaged tool uses it the same way — and it is the
// only thing that distinguishes "not signed in" from "signed in but the
// request is missing a header".
const GROK_ALLOWED = ['https://grok.com/rest/', 'https://grok.com/api/auth/session'];

window.addEventListener('GROK_FETCH', async ({ detail }) => {
  const {
    requestId,
    url,
    method = 'POST',
    body,
    stream,
    sessionHeaders,
  } = detail || {};
  const reply = (payload) =>
    window.dispatchEvent(
      new CustomEvent('GROK_FETCH_RESULT', { detail: { requestId, ...payload } }),
    );

  // Same path-scoped rule the background worker enforces. Being in the page
  // does not make it acceptable to reach account endpoints.
  if (typeof url !== 'string' || !GROK_ALLOWED.some((p) => url.startsWith(p))) {
    reply({ error: 'GROK_URL_NOT_ALLOWED' });
    return;
  }

  try {
    const res = await fetch(url, {
      method,
      headers: {
        'content-type': 'application/json',
        'x-api-source': 'pc',
        // Whatever grok's own client last sent (a Statsig id among them).
        // Without these the API answers 403 even from inside the page.
        ...(sessionHeaders || {}),
        // Per-call, so it is generated here rather than replayed.
        'x-xai-request-id': crypto.randomUUID(),
      },
      body: body ? JSON.stringify(body) : undefined,
      credentials: 'include',
    });

    // On a failure, hand back the body instead of trying to stream-parse it:
    // an error page is HTML or a single JSON object, and the brace scanner
    // below would return nothing, leaving the caller with a bare status code.
    if (!res.ok) {
      const text = await res.text();
      let data = null;
      try {
        data = JSON.parse(text);
      } catch (e) {
        data = null;
      }
      reply({ status: res.status, data, text: data ? null : text.slice(0, 2000) });
      return;
    }

    if (!stream) {
      const text = await res.text();
      let data = null;
      try {
        data = JSON.parse(text);
      } catch (e) {
        data = null;
      }
      reply({ status: res.status, data, text: data ? null : text.slice(0, 4000) });
      return;
    }

    // Streaming reply: objects arrive back to back with no separator, so
    // split on brace depth rather than on newlines — the same approach the
    // packaged tool's injected script takes.
    const objects = [];
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let start = -1;
      let depth = 0;
      let inString = false;
      let escape = false;
      let consumed = 0;
      for (let i = 0; i < buffer.length; i++) {
        const ch = buffer[i];
        if (start === -1) {
          if (ch === '{') {
            start = i;
            depth = 1;
            inString = false;
            escape = false;
          }
          continue;
        }
        if (inString) {
          if (escape) escape = false;
          else if (ch === '\\') escape = true;
          else if (ch === '"') inString = false;
          continue;
        }
        if (ch === '"') inString = true;
        else if (ch === '{') depth++;
        else if (ch === '}') {
          depth--;
          if (depth === 0) {
            try {
              objects.push(JSON.parse(buffer.slice(start, i + 1)));
            } catch (e) {
              /* a truncated object simply isn't ready yet */
            }
            start = -1;
            consumed = i + 1;
          }
        }
      }
      buffer = buffer.slice(consumed);
    }
    reply({ status: res.status, objects });
  } catch (e) {
    reply({ error: (e && e.message) || 'GROK_FETCH_FAILED' });
  }
});

window.dispatchEvent(new CustomEvent('GROK_BRIDGE_READY'));
