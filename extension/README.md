# Flowboard Bridge (Chrome MV3)

The bridge that lets the local agent reach Google Flow. It no longer proxies requests to Flow on the
agent's behalf — since Google's September 2026 migration it **runs them inside your signed-in Flow tab**,
because that is the only place they can be signed.

## Install

1. Open `chrome://extensions`.
2. Enable **Developer mode** (top-right toggle).
3. Click **Load unpacked** and select this folder.
4. Open <https://flow.google.com/>, sign in, open a project, and **leave the tab open**.

Step 4 is not optional. Every Flow call runs in that tab.

## How it works

- The service worker connects to `ws://127.0.0.1:9223` automatically when the agent is running.
- The agent sends a `batch_rpc` command: an rpcid plus a `batchexecute` envelope. The extension mints a
  reCAPTCHA token in the Flow tab, patches it into the envelope where the agent left a `__CAPTCHA__`
  placeholder, then runs the POST **inside the tab** via
  `chrome.scripting.executeScript({world: 'MAIN'})`, so the page's own cookie and its per-page `at` CSRF
  token sign it.
- `flow_probe` answers one question: can that tab sign a call right now. It reports presence only —
  whether the page carries its signing token, never the token itself.
- Responses go back over HTTP POST to `http://127.0.0.1:8101/api/ext/callback` with an
  `X-Callback-Secret` header (the secret is supplied by the agent on connect). WS is the fallback.
- A keepalive `ping` goes out every ~24 s; a disconnect triggers a reconnect in ~5 s.
- `trpc_request` still exists, for Grok and Shopee only. Those are third-party sites reached with their
  own session cookies, and that path **never** attaches a Google credential.

### No credential ever reaches the agent

There is nothing left to capture. Google stopped issuing the `Authorization: Bearer ya29.*` token the old
REST API needed; the page signs its own calls instead. So the extension holds no Google credential, the
agent holds no Google credential, and there is no headless mode — that is structural, not a setting.

One consequence worth knowing: the agent cannot tell you who is signed in. It reports whether the tab can
sign a call, which is the thing that actually decides whether a generation will run.

### The captcha token is single-use

Sending one twice gets `PUBLIC_ERROR_UNUSUAL_ACTIVITY`. Minting is serialised in `injected.js`
(`captchaMintTail`, waiting up to 22 s for `grecaptcha`), because two concurrent mints mean one of them
is certainly replaced.

### The injected function has to be self-contained

The function handed to `executeScript({world: 'MAIN'})` runs in the page, not in the worker, so it cannot
close over anything. A reference to an outer variable makes Chrome answer `NO_INJECTION_RESULT` —
commonly on the first RPC after the agent starts, which is why the agent retries it with a counter rather
than treating it as a failure.

## Content script + injected script

`content.js` runs at `document_start` on the Flow tab. It injects `injected.js` into the MAIN world so
that script can reach `window.grecaptcha.enterprise`. The two talk over `CustomEvent`
(`GET_CAPTCHA` / `CAPTCHA_RESULT`).

## Popup

Click the extension icon for connection status, the **Flow tab** row, request counters, a captcha test
button, and a button to open Flow. The header reads the build from `chrome.runtime.getManifest()` rather
than a hardcoded string — "which build is loaded" is exactly the question one forgotten reload makes
unanswerable.

The old **Token / Refresh token** row is gone. There is no token to refresh, and what it showed was worse
than nothing: it read "token 206h" while the user was signed in and using Flow normally, and every call
was failing.

## Permissions, and why each one is still here

| Permission | Why |
| --- | --- |
| `scripting` + the `flow.google.com` host | running the RPC inside the signed-in tab; there is no other way to sign it |
| `webRequest` | Grok's session header reader. Nothing to do with Flow any more — the Bearer listener is gone |
| `cookies` for `grok.com` / `shopee.vn` | the two third-party session channels, allowlisted **by path** |
| `labs.google/*` | that host still redirects to `flow.google.com`, so an old pinned tab keeps working |

Removed with the migration: the `aisandbox-pa.googleapis.com` and `*.googleapis.com` hosts, the
`declarativeNetRequest` permission and its `rules.json` (which only rewrote Referer and Origin for
aisandbox), the Bearer-capture listener, the throwaway-tab token refresh, and the redirect-chasing
listener that existed because Flow's tRPC media endpoint answered 302 — the `as29s` RPC returns the url
inline now.

## Out of scope (this version)

- Side panel
- Video upload (`abra_edit` / Edit Video) — needs an RPC nobody has captured on this transport; see
  `docs/flow-capture.md`

See `docs/PLAN.md` for planned additions.
