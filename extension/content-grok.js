/**
 * Content script on grok.com — bridge between background.js and the MAIN-world
 * script that does the actual fetching.
 *
 * Mirrors content.js (labs.google). The split exists because a content script
 * runs in an isolated world: it can talk to the extension but not to the
 * page's own origin state, which is precisely what these calls need.
 */
(function () {
  const s = document.createElement('script');
  s.src = chrome.runtime.getURL('injected-grok.js');
  s.onload = () => s.remove();
  (document.head || document.documentElement).appendChild(s);
})();

// Generation is slow — a video request can run for minutes — so this waits
// far longer than the captcha bridge does.
const GROK_TIMEOUT_MS = 300000;

chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type !== 'GROK_FETCH') return;

  const requestId = msg.requestId;

  const handler = (e) => {
    if (e.detail?.requestId !== requestId) return;
    window.removeEventListener('GROK_FETCH_RESULT', handler);
    clearTimeout(timer);
    const { requestId: _id, ...rest } = e.detail;
    reply(rest);
  };

  const timer = setTimeout(() => {
    window.removeEventListener('GROK_FETCH_RESULT', handler);
    reply({ error: 'GROK_CONTENT_TIMEOUT' });
  }, GROK_TIMEOUT_MS);

  window.addEventListener('GROK_FETCH_RESULT', handler);
  window.dispatchEvent(
    new CustomEvent('GROK_FETCH', {
      detail: {
        requestId,
        url: msg.url,
        method: msg.method,
        body: msg.body,
        stream: msg.stream,
        sessionHeaders: msg.sessionHeaders,
      },
    }),
  );

  return true; // async reply
});
