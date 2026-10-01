"""Bridge to the Chrome MV3 extension over WebSocket.

Ported + trimmed from flowkit (https://github.com/crisng95/flowkit).

**This client holds no credential.** Google moved Flow to `flow.google.com` in
September 2026 and stopped minting the `Bearer ya29.…` the old REST host needed;
the rewritten frontend signs every call with the session cookie plus a per-page
`at` CSRF token that only the page can read. So the agent builds request
envelopes (`flow_batch`) and the extension issues them **inside a signed-in Flow
tab**. Nothing here works headless, and "is the bridge connected" is no longer
the same question as "can we generate" — see `flow_probe`.

Control flow:
1. Extension opens WS to :9223.
2. Agent sends ``{type:"callback_secret", secret}`` immediately.
3. To call Flow, the agent calls ``flow_client.batch_rpc(rpcid, freq, …)``,
   which sends ``{id, method:"batch_rpc", params}`` over WS and awaits a future.
4. The extension mints a single-use reCAPTCHA in the page when the RPC needs
   one, runs the ``batchexecute`` POST in the page's MAIN world, and POSTs the
   response body to ``/api/ext/callback`` with ``X-Callback-Secret``.
5. That HTTP handler resolves the pending future by id.
6. WS-side inbound messages (``extension_ready``, ``pong``) update our stats.

``trpc_request`` and ``grok_fetch`` survive for Grok and Shopee — cookie-
authenticated third-party sites, path-scoped, and never given a Google
credential (there is none to give).
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from typing import Any, Optional


logger = logging.getLogger(__name__)


def _endpoint_of(url: str) -> str:
    """What failed, in a form safe to log: an rpcid, or a URL's path.

    A batch RPC arrives here as its rpcid (`ogiZ0b`, `eb1hJf`) and is kept as
    is — that IS the endpoint on this transport. A session-channel URL is
    reduced to its path, because the query can carry credentials.

    Either way the point is to make a failure actionable: `eb1hJf` failing and
    `as29s` failing are different problems that share one status code.
    """
    if not url:
        # "?" rather than "" — an empty label reads as "no field here" in a log
        # line, where "?" reads as "we do not know".
        return "?"
    if "/" not in url:
        return url
    if not url:
        return "?"
    without_query = url.split("?", 1)[0]
    return without_query.split("://", 1)[-1].split("/", 1)[-1] or without_query


def _error_detail(body: Any) -> str:
    """Google's own words for why it refused, truncated.

    Only the message and status fields are read. The body is echoed back by
    the extension and can contain the prompt and media ids that went out; a
    log line is the wrong place for either.
    """
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            parts = [str(err.get(k)) for k in ("status", "message") if err.get(k)]
            if parts:
                return " · ".join(parts)[:200]
        message = body.get("message")
        if isinstance(message, str):
            return message[:200]
    if isinstance(body, str):
        return body.strip()[:200] or "(empty body)"
    return "(no detail in body)"


#: How long to wait on one RPC before giving up. Generous because the
#: extension has to find a Flow tab, revive it if Chrome discarded it, mint a
#: captcha in the page and only then send the request.
#: Errors the extension reports when the page itself could not sign the RPC:
#: no Flow tab, a tab Chrome discarded, a tab that is signed out or still
#: booting (`NO_AT_TOKEN`), or an injection that produced no result. None of
#: these are the request's fault, and all of them are fixed in the browser.
_PAGE_UNSIGNED_CODES: tuple[str, ...] = (
    "NO_FLOW_TAB",
    "FLOW_TAB_DISCARDED",
    "NO_AT_TOKEN",
    "NO_INJECTION_RESULT",
)


class FlowClient:
    """Singleton bridge client.

    Every Flow call goes out as a `batchexecute` RPC that the extension runs
    inside a signed-in `flow.google.com` tab. There is no token here: Google's
    September 2026 migration replaced the `Bearer ya29.…` this client used to
    hold with a per-page `at` CSRF token the page alone can read. The two
    consequences worth keeping in mind:

    * **Nothing works headless.** A signed-in Flow tab must be open. Ask
      `flow_probe()` rather than guessing.
    * **The agent cannot tell whether it is authorised** until it tries. That
      used to be answerable by calling `/v1/credits` with the held token, which
      is also why the account tier is now a Settings value (`FLOW_PAYGATE_TIER`)
      rather than something this client resolves.
    """

    DEFAULT_TIMEOUT = 180.0  # seconds

    def __init__(self) -> None:
        self._ws: Optional[Any] = None
        self._pending: dict[str, asyncio.Future] = {}
        self._pending_urls: dict[str, str] = {}
        self._callback_secret: str = secrets.token_urlsafe(32)

        #: Which build of the bridge the browser is actually running. Without
        #: it, "reload the extension" and "the extension was reloaded" are
        #: indistinguishable from here — which cost an afternoon of blind
        #: retries during the migration.
        self._extension_version: Optional[str] = None
        #: What the last `flow_probe` found: whether a Flow tab is open and
        #: whether it can sign an RPC. Presence only, never values. This is the
        #: replacement for `flow_key_present` — the question is no longer "do we
        #: hold a token" but "can the page sign for us".
        self._flow_probe: Optional[dict] = None
        self._flow_probe_at: Optional[float] = None
        #: A tier resolved live before the migration, if this process saw one.
        #: Nothing sets it any more; `paygate_tier` falls through to Settings.
        self._paygate_tier: Optional[str] = None
        self._request_count = 0
        self._success_count = 0
        self._failed_count = 0
        self._last_error: Optional[str] = None
        #: Endpoint + status + redacted reason for the most recent failure.
        #: `_last_error` alone ("API_404") does not say what 404'd.
        self._last_failure: Optional[dict] = None
        #: Set when an RPC comes back saying the page could not sign it —
        #: `NO_AT_TOKEN`, `NO_FLOW_TAB`, `FLOW_TAB_DISCARDED`. The old
        #: `token_rejected` said the same thing about a credential we held;
        #: this says it about the tab, which is where the credential lives now.
        self._page_unsigned: Optional[str] = None

    # ── connection ─────────────────────────────────────────────────────────
    @property
    def connected(self) -> bool:
        return self._ws is not None

    @property
    def callback_secret(self) -> str:
        return self._callback_secret

    def set_extension(self, ws: Any) -> None:
        self._ws = ws

    def clear_extension(self) -> None:
        self._ws = None
        # What the probe found belonged to that connection's tab.
        self._flow_probe = None
        self._flow_probe_at = None
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ConnectionError("extension_disconnected"))
        self._pending.clear()
        self._pending_urls.clear()
        # A fresh connection brings its own tab, and its own verdict.
        self._page_unsigned = None

    @property
    def paygate_tier(self) -> Optional[str]:
        """The account tier, as the user set it in Settings.

        It used to be read from `/v1/credits` with the captured Bearer token.
        Since Google moved Flow to `flow.google.com` (September 2026) there is
        no Bearer and no credits endpoint, and the migrated payloads carry no
        `userPaygateTier` field at all — so nothing on the wire depends on this
        any more. It survives as a LABEL: which lanes to offer, which price to
        quote. **Never a dispatch gate** — a wrong label costs a confusing
        number on a screen, while gating on it would mean an app that cannot
        generate anything until someone fills in a dropdown.

        A live value observed earlier in the process still wins, so a session
        that resolved a real tier before the migration keeps reporting it.
        """
        if self._paygate_tier:
            return self._paygate_tier
        from flowboard.services import settings_store

        configured = settings_store.get("FLOW_PAYGATE_TIER")
        return configured if isinstance(configured, str) and configured else None

    @property
    def flow_tab(self) -> Optional[dict]:
        """What the last `flow_probe` found, or None if never asked.

        Replaces `flow_key_present` / `token_rejected` / `sku` / `credits`, all
        of which described a credential this client held. It holds none: the
        question that decides whether generation can run is whether a
        signed-in Flow tab is open, and only the page can answer it.
        """
        return self._flow_probe

    @property
    def page_unsigned(self) -> Optional[str]:
        """Why the page last refused to sign an RPC, or None.

        `NO_AT_TOKEN` (signed out / still booting), `NO_FLOW_TAB`,
        `FLOW_TAB_DISCARDED`. Surfaced on `/api/health` because otherwise a
        connected extension with an unusable tab looks perfectly healthy —
        exactly the state that hid the migration for a week.
        """
        return self._page_unsigned

    # ── inbound handling ───────────────────────────────────────────────────
    async def handle_message(self, data: dict) -> None:
        t = data.get("type")
        if t == "extension_ready":
            version = data.get("version")
            if isinstance(version, str):
                self._extension_version = version[:16]
            logger.info(
                "extension_ready v%s transport=%s",
                self._extension_version or "?",
                data.get("transport") or "?",
            )
            return
        if t == "pong":
            return
        # Inbound response (legacy path; production flow uses HTTP callback)
        req_id = data.get("id")
        if req_id and req_id in self._pending:
            self._resolve(req_id, data)

    def resolve_callback(self, data: dict) -> bool:
        """Called by the HTTP callback endpoint after validating the secret.

        Returns True if a pending future matched.
        """
        req_id = data.get("id")
        if not req_id or req_id not in self._pending:
            return False
        self._resolve(req_id, data)
        return True

    def _resolve(self, req_id: str, data: dict) -> None:
        fut = self._pending.pop(req_id, None)
        url = self._pending_urls.pop(req_id, "")
        if not fut or fut.done():
            return
        # Count as failure if (a) an explicit `error` field is set OR
        # (b) the HTTP status is a 4xx/5xx. Otherwise success.
        status = data.get("status")
        http_error = isinstance(status, int) and status >= 400
        explicit_error = bool(data.get("error"))
        if http_error or explicit_error:
            self._failed_count += 1
            msg = data.get("error") or f"API_{status}"
            self._last_error = str(msg)[:200]
            # The endpoint and what Google said about it. Both were already
            # in hand and both were being discarded: the agent chose the URL
            # and the extension forwards the response body verbatim, yet the
            # only thing kept was the three-digit code.
            self._last_failure = {
                "endpoint": _endpoint_of(url),
                "status": status,
                "detail": _error_detail(data.get("data")),
            }
            logger.warning(
                "flow: %s on %s — %s",
                msg,
                self._last_failure["endpoint"],
                self._last_failure["detail"],
            )
            fut.set_result(data)
        else:
            self._success_count += 1
            fut.set_result(data)

    # ── outbound ──────────────────────────────────────────────────────────
    async def notify(self, message: dict) -> bool:
        """Fire-and-forget WS push to the extension. Returns False when the
        extension isn't connected so callers can surface a meaningful
        diagnostic instead of silently losing the message.

        Used by the logout flow (tell extension to clear its in-memory
        token + cached userinfo) and the scan flow (ask extension to
        re-fetch userinfo when the agent has a connection but the cache
        is empty).
        """
        if not self.connected or self._ws is None:
            return False
        try:
            await self._ws.send(json.dumps(message))
            return True
        except Exception as exc:
            logger.warning("notify failed: %s", exc)
            return False

    async def _send(self, method: str, params: dict, timeout: Optional[float] = None) -> dict:
        if not self.connected:
            return {"error": "extension_disconnected"}

        req_id = str(uuid.uuid4())
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = fut
        # Remembered only so a failure can name the endpoint that failed.
        # Without it `_last_error` reads `API_404` with nothing attached, and
        # thirty of those in a row say only "something 404s" — which is
        # exactly where a debugging session went before this existed.
        # Remembered so a failure can name WHAT failed. For a batch RPC that is
        # the rpcid — `batch_rpc` alone would label every Flow failure
        # identically, which is the state this field exists to escape. Never the
        # envelope: an `f.req` holds the prompt, and a substituted one holds a
        # captcha token.
        self._pending_urls[req_id] = str(
            params.get("rpcid") or params.get("url") or method
        )
        self._request_count += 1

        payload = {"id": req_id, "method": method, "params": params}
        try:
            await self._ws.send(json.dumps(payload))
            return await asyncio.wait_for(fut, timeout=timeout or self.DEFAULT_TIMEOUT)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            self._pending_urls.pop(req_id, None)
            self._failed_count += 1
            self._last_error = "timeout"
            return {"error": "timeout"}
        except ConnectionError as exc:
            self._pending.pop(req_id, None)
            self._pending_urls.pop(req_id, None)
            self._failed_count += 1
            self._last_error = str(exc)
            return {"error": str(exc)}
        except Exception as exc:
            self._pending.pop(req_id, None)
            self._pending_urls.pop(req_id, None)
            self._failed_count += 1
            self._last_error = str(exc)
            return {"error": str(exc)}

    async def batch_rpc(
        self,
        rpcid: str,
        freq: str,
        captcha_action: Optional[str] = None,
        match: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> dict:
        """Run one batchexecute RPC inside the Flow page. Returns the raw body.

        Replaces `api_request`, which proxied a REST call from the service
        worker with a Bearer token. Neither half of that survives: the host no
        longer authenticates, and the credential it used is not minted. So the
        agent builds the envelope (`flow_batch`) and the page issues it.

        ``captcha_action`` makes the extension mint a **single-use** reCAPTCHA
        and substitute it for the `__CAPTCHA__` placeholder moments before the
        request leaves. It cannot be minted here and carried: a replayed token
        comes back `PUBLIC_ERROR_UNUSUAL_ACTIVITY`.

        ``match`` asks the extension to cut the response down to an 800-byte
        window around that string. The project listing is past 17 MB for the one
        entry we want, and the cheapest place to throw the rest away is inside
        the tab.
        """
        params: dict[str, Any] = {"rpcid": rpcid, "freq": freq}
        if captcha_action:
            params["captchaAction"] = captcha_action
        if match:
            params["match"] = match
        result = await self._send("batch_rpc", params, timeout=timeout)
        # Carry the HTTP status into the error the SDK will raise.
        #
        # The extension forwards a non-OK page fetch as `{id, status, data}` with
        # no `error` key, and `_resolve` kept the status for observability only.
        # So `_payload` found no envelope and raised "no <rpcid> envelope in
        # response" — which `classify_error` buckets as `terminal`. Consequences:
        # `MAX_CUMULATIVE_403` could never be reached from the Flow transport, so
        # the breaker guarding the one Google account could not see a Google 403;
        # and a 429 or a 502 mid-wave discarded queued work instead of retrying
        # it. Scoped to this method rather than `_resolve` because Grok and Shopee
        # read their own status codes off the raw dict.
        status = result.get("status")
        if not result.get("error") and isinstance(status, int) and status >= 400:
            result = {**result, "error": f"API_{status}"}
        error = str(result.get("error") or "")
        if any(code in error for code in _PAGE_UNSIGNED_CODES):
            self._page_unsigned = error[:200]
        elif not result.get("error"):
            self._page_unsigned = None
        return result

    async def flow_probe(self, timeout: Optional[float] = 30.0) -> dict:
        """Ask whether a signed-in Flow tab exists and can sign an RPC.

        Reads PRESENCE, never values — whether `at` is on the page, not what it
        is. This is the replacement for "do we hold a valid token": the token
        never reaches the agent any more, so the only way to know is to ask the
        page, and the only honest answer is a yes/no plus a reason.
        """
        result = await self._send("flow_probe", {}, timeout=timeout)
        probe = result.get("result") if isinstance(result, dict) else None
        if isinstance(probe, dict):
            self._flow_probe = probe
            self._flow_probe_at = time.time()
            error = probe.get("error")
            self._page_unsigned = (
                str(error)[:200] if error and not probe.get("atTokenPresent") else None
            )
            return probe
        return {"flowTabPresent": False, "atTokenPresent": False,
                "error": str(result.get("error") or "no_probe_result")[:200]}

    async def trpc_request(
        self,
        url: str,
        method: str = "POST",
        headers: Optional[dict] = None,
        body: Any = None,
        timeout: Optional[float] = 30.0,
    ) -> dict:
        """Proxy a call through the extension on the user's session cookie.

        No captcha — a plain ``credentials: 'include'`` fetch. Reaches Flow's
        own tRPC endpoints, and also Grok and Shopee, which likewise answer
        only to a signed-in session (see SESSION_CHANNELS in background.js).

        The Google Bearer token is attached by the extension for the Flow
        channel ONLY — it must never ride along to a third-party host.
        """
        return await self._send(
            "trpc_request",
            {"url": url, "method": method, "headers": headers or {}, "body": body},
            timeout=timeout,
        )

    async def grok_fetch(
        self,
        url: str,
        method: str = "POST",
        body: Any = None,
        stream: bool = False,
        timeout: Optional[float] = 300.0,
    ) -> dict:
        """Run a grok.com call inside an open grok.com tab.

        Not a plain proxy: Grok's endpoints want per-session headers only its
        running app knows, and answer with a stream of concatenated JSON. The
        extension therefore hands the request to a content script on the page
        (the same thing the packaged tool does by driving Chrome over CDP).

        Requires the user to be signed in with a grok.com tab open; without one
        the bridge answers ``GROK_NO_TAB``. Long timeout because a video
        generation genuinely runs for minutes.
        """
        return await self._send(
            "grok_fetch",
            {"url": url, "method": method, "body": body, "stream": stream},
            timeout=timeout,
        )

    async def solve_captcha(
        self, action: str = "VIDEO_GENERATION", timeout: Optional[float] = 45.0
    ) -> dict:
        """Ask the page for one reCAPTCHA token, on its own.

        The solver has always run inside every generation request, so this adds
        no capability — it adds an ANSWER. When generation stalls, "Flow is
        slow", "the token expired" and "the page has stopped issuing captcha
        tokens" are three different problems with one symptom, and only the
        third one is fixed by reloading the tab. Nothing else could tell them
        apart.

        Returns the extension's report — ``ok``, ``elapsedMs``, ``tokenLength``,
        ``error``. **Never the token**: this method exists for diagnosis, and a
        credential that travels for diagnosis is a credential in a log.
        """
        return await self._send("solve_captcha", {"action": action}, timeout=timeout)

    # ── observability ─────────────────────────────────────────────────────
    @property
    def ws_stats(self) -> dict:
        probe = self._flow_probe or {}
        return {
            "connected": self.connected,
            # Whether the browser can sign a Flow call. "Connected" on its own
            # says only that the bridge is up, which is exactly how a week of
            # 401s looked healthy — so these two travel together.
            "flow_tab_present": probe.get("flowTabPresent"),
            "at_token_present": probe.get("atTokenPresent"),
            "flow_probe_age_s": (
                int(time.time() - self._flow_probe_at)
                if self._flow_probe_at is not None
                else None
            ),
            # Why the page last refused to sign. The actionable half of a
            # stalled run: only this one is fixed by reloading the Flow tab.
            "page_unsigned": self._page_unsigned,
            "pending": len(self._pending),
            "request_count": self._request_count,
            "success_count": self._success_count,
            "failed_count": self._failed_count,
            "last_error": self._last_error,
            # Which RPC, and what came back. `last_error` says only "API_502";
            # this says which rpcid and why — a diagnosis rather than a guess.
            # It never carries the envelope: an `f.req` holds the prompt, and a
            # substituted one holds a captcha token.
            "last_failure": self._last_failure,
            "extension_version": self._extension_version,
            "transport": "batch",
        }


flow_client = FlowClient()


def get_flow_client() -> FlowClient:
    return flow_client
