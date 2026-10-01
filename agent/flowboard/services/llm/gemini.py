"""Gemini provider — subprocess wrapper around Google's ``gemini`` CLI.

The CLI's non-interactive surface is intentionally minimal — see
``gemini --help``. Only ``-p, --prompt`` is exposed; there's no ``--system``
flag and no dedicated image-attachment flag. Both are folded into the
prompt body:

- **System prompt**: prepended as ``[System: ...]`` followed by ``\\n\\n``
  before the user prompt. The CLI passes the whole string to the model.
- **Image attachments**: inlined via ``@<absolute_path>`` tokens. The
  CLI reads the file and forwards it as a multimodal block (same
  pattern Claude CLI uses). Verified live: `gemini -p "describe @path"`
  works and returns a real description.

Output is requested as ``-o json`` so we get a structured envelope of
the form ``{"session_id": "...", "response": "<text>", "stats": {...}}``.
We extract the ``response`` field and discard the rest. Earlier the
provider returned raw stdout, which periodically picked up "Loaded
cached credentials" banners + ``Tip:`` informational lines + ANSI
escape codes that broke downstream parsers (especially the batch
auto-prompt synth that expects a JSON array reply from the model).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import subprocess
from typing import Any, Optional

import httpx

from flowboard.services import gemini_keys

from .base import LLMError
from .cli_utils import (
    resolve_cli_binary,
    validate_prompt_size,
    validate_attachment_paths,
    DEFAULT_SUBPROCESS_TIMEOUT,
    CLI_PROBE_TIMEOUT,
)

logger = logging.getLogger(__name__)

_CLI_BIN = "gemini"
_DEFAULT_TIMEOUT = DEFAULT_SUBPROCESS_TIMEOUT
_PROBE_TIMEOUT = CLI_PROBE_TIMEOUT

# Pin a stable production model. Gemini CLI v0.38.2's default Auto
# mode picks `gemini-3-flash-preview` (preview tier) which Google
# returns 429 MODEL_CAPACITY_EXHAUSTED for routinely — even when the
# user's per-model quota is fine — because preview models are
# capacity-throttled server-side. The CLI then retries with backoff,
# inflating per-call latency by 30+ seconds before the call eventually
# lands.
#
# `gemini-2.5-flash` is the stable Flash tier that the Auto (Gemini
# 2.5) group routes to. Stable tier = real production capacity, no
# preview throttling. Direct `-m gemini-2.5-flash` works on the
# CodeAssist backend in CLI v0.38.2 (verified — unlike
# `gemini-3-flash` which returns ModelNotFound).
#
# Override via FLOWBOARD_GEMINI_MODEL if you want `gemini-2.5-pro`
# (slower but better for Planner JSON quality) or any other variant.
_DEFAULT_MODEL: str | None = "gemini-2.5-flash"

# ── API mode ──────────────────────────────────────────────────────────────
# The CLI needs an interactive OAuth login, which is a wall for anyone who
# cannot complete it — and for anyone this app is handed to. An API key works
# immediately and is what the packaged tool asks for, so the provider accepts
# either. Same REST surface `tts.py` already calls, different model.
_API_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
# The API needs an explicit model; unlike the CLI there is no saved default.
#
# A floating alias, not a pinned version: a pinned `gemini-2.5-flash` now
# answers 404 "no longer available to new users", so hardcoding a version
# schedules an outage.
#
# Lite rather than full flash, measured on the same one-line prompt:
#   gemini-flash-lite-latest   1.3s   200
#   gemini-3.6-flash          12.4s   200
#   gemini-flash-latest      113.0s   503 (overloaded)
# Output quality on prompt composition was comparable, and this model is
# called once per node in the canvas — a 12s floor there reads as broken.
# Pin something heavier with FLOWBOARD_GEMINI_MODEL if planner JSON needs it.
#
# Note: `generationConfig.thinkingConfig.thinkingBudget = 0` is NOT accepted
# by these models (answers 400), so latency cannot be bought back that way.
_API_MODEL = "gemini-flash-lite-latest"
_API_TIMEOUT_S = 120.0
# One retry per extra key: a 429 on one key says nothing about the next.
_API_MAX_KEY_ATTEMPTS = 4


class GeminiProvider:
    """Conforms to ``LLMProvider`` (structural typing).

    Concurrency note: Google's CodeAssist backend (the one the CLI talks
    to) rate-limits **concurrent calls per user/session**. A second call
    fired while the first is in flight comes back with HTTP 429
    ``MODEL_CAPACITY_EXHAUSTED`` and the CLI then retries with backoff,
    inflating the second call's wall time by 30+ seconds. This is NOT
    user-quota or billing-tier related — Pro / Ultra plans hit it
    identically. It's a per-call concurrency ceiling on the model's
    shared capacity tier.

    We serialize at the provider boundary (one ``asyncio.Semaphore(1)``
    around the subprocess call) so every dispatch path — auto-prompt,
    vision, planner, test endpoint — naturally queues into one in-flight
    call at a time. Sequential calls land in ~7s each; we'd rather
    queue cleanly than race and pay the 30s+ retry penalty.

    Other providers (Claude, OpenAI Codex) don't need this — Anthropic's
    and OpenAI's backends handle parallel calls fine.
    """

    name: str = "gemini"
    supports_vision: bool = True  # Gemini Flash + Pro both have vision
    # The only provider here that takes audio: the REST path sends it as
    # `inline_data`. Subtitles and karaoke timing therefore rest on this
    # one provider, which is why the health view reports it separately.
    supports_audio: bool = True
    test_timeout_secs: float = 180.0  # Retries with backoff on 429 quota exhaustion

    def __init__(self) -> None:
        self._available: Optional[bool] = None
        # Module-level singleton in registry → one semaphore for the
        # process lifetime. Lazy-allocated on first run() because asyncio
        # Semaphore wants a running event loop in some Python versions.
        self._call_lock: Optional[asyncio.Semaphore] = None

    # ── availability ──────────────────────────────────────────────────

    async def is_available(self) -> bool:
        """True when EITHER the CLI or an API key can serve a dispatch.

        The CLI check doesn't verify auth — the user could have the binary
        installed but not signed in. The Test endpoint catches that by
        actually invoking the model.
        """
        if self._available is None:
            self._available = await self._probe_version()
            logger.info("gemini: cli available=%s", self._available)
        return self._available or gemini_keys.available()

    @property
    def mode(self) -> str:
        """Which transport ``run()`` would pick right now: 'cli' / 'api' /
        'none'. Reported by ``/api/llm/providers`` so the Settings card can
        say how this provider is authenticated. Mirrors ``run()``'s order —
        key first, then CLI."""
        if gemini_keys.available():
            return "api"
        if self._available:
            return "cli"
        return "none"

    def reset_cache(self) -> None:
        """Testing hook + Settings panel rescan support."""
        self._available = None


    async def _probe_version(self) -> bool:
        """Check gemini CLI availability using subprocess (Windows-compatible)."""
        # Use shared binary resolver which tries PATH + npm locations
        try:
            gemini_bin = resolve_cli_binary(_CLI_BIN, _PROBE_TIMEOUT)
            # In a thread, not inline: this runs during startup and during
            # every health check, and inline it stalls the event loop for as
            # long as the CLI takes to answer.
            result = await asyncio.to_thread(
                subprocess.run,
                [gemini_bin, "--version"],
                capture_output=True,
                timeout=_PROBE_TIMEOUT,
            )
            if result.returncode == 0:
                logger.info("gemini: found at %s", gemini_bin)
                return True
            logger.warning("gemini: version probe returned code %d", result.returncode)
            return False
        except subprocess.TimeoutExpired:
            logger.warning("gemini: probe timed out")
            return False
        except Exception as e:
            logger.warning("gemini: probe failed: %s", e)
            return False

    # ── dispatch ──────────────────────────────────────────────────────

    async def run(
        self,
        user_prompt: str,
        *,
        system_prompt: Optional[str] = None,
        attachments: Optional[list[str]] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> str:
        """Invoke ``gemini -p PROMPT`` and return stdout.

        System prompt + image attachments are folded into the prompt body
        because the CLI doesn't expose them as flags — see module docstring.

        The actual subprocess invocation is serialized through
        ``self._call_lock`` (Semaphore(1)) — see class docstring for the
        CodeAssist backend concurrency rationale. Time spent waiting in
        the lock counts against the caller's ``timeout`` budget; if a
        Vision call holds the lock for 7s and an Auto-Prompt is queued
        behind it with a 90s timeout, Auto-Prompt has 83s of work time
        once it acquires.
        """
        # Validate inputs
        try:
            validate_prompt_size(user_prompt)
            if system_prompt:
                validate_prompt_size(system_prompt)
            validate_attachment_paths(attachments)
        except ValueError as exc:
            raise LLMError(f"Invalid input: {exc}") from exc

        # An API key wins over the CLI when both look present.
        #
        # Measured on this machine: `gemini --version` exits 0 while every
        # real call dies with
        #   IneligibleTierError: This client is no longer supported for Gemini
        #   Code Assist for individuals … migrate to Antigravity
        # Google retired that login for individual accounts, so a passing
        # version probe says nothing about whether a dispatch will work. A key
        # is the transport that actually answers, so prefer it and keep the
        # CLI for hosts that have a working (enterprise) setup and no key.
        if gemini_keys.available():
            return await self._run_api(
                user_prompt, system_prompt, attachments, timeout
            )
        # No key: fall through to the CLI exactly as before — including
        # letting `resolve_cli_binary` raise when the binary is missing, which
        # is the error the caller already knows how to report. Probing here
        # instead would spend an extra `subprocess.run` on every dispatch.

        # Build the composite prompt: system block, user prompt, attachments.
        parts: list[str] = []
        if system_prompt:
            parts.append(f"[System: {system_prompt}]")
        parts.append(user_prompt)
        if attachments:
            parts.append(
                " ".join(f"@{os.path.abspath(p)}" for p in attachments)
            )
        full_prompt = "\n\n".join(parts)

        # Optional model pin via env var — see _DEFAULT_MODEL docstring
        # for the capacity-exhausted preview-model rationale. When unset
        # we don't pass `-m` so Gemini CLI's own `/model` setting wins.
        model = os.environ.get("FLOWBOARD_GEMINI_MODEL") or _DEFAULT_MODEL
        gemini_bin = resolve_cli_binary(_CLI_BIN, _PROBE_TIMEOUT)
        args: list[str] = [gemini_bin]
        if model:
            args += ["-m", model]
        # ``-o json`` gives us a structured envelope instead of raw text
        # mixed with banner/tip/ANSI noise. Parsed in ``_invoke_locked``.
        args += ["-o", "json", "-p", full_prompt]

        # Lazy-init the semaphore on the running loop. The wait + the
        # subprocess + communicate all live inside the lock so a second
        # caller can't slip in between proc spawn and proc.communicate.
        if self._call_lock is None:
            self._call_lock = asyncio.Semaphore(1)
        async with self._call_lock:
            return await self._invoke_locked(args, timeout=timeout)

    # ── API dispatch ─────────────────────────────────────────────────────

    async def _run_api(
        self,
        user_prompt: str,
        system_prompt: Optional[str],
        attachments: Optional[list[str]],
        timeout: float,
    ) -> str:
        """One ``generateContent`` call, rotating keys past quota errors.

        A 429 on one key says nothing about the next, so an exhausted key is
        rested and the call retried on another rather than failed outright —
        that is the entire reason the packaged tool ships a list of keys.
        """
        parts: list[dict[str, Any]] = [{"text": user_prompt}]
        for path in attachments or []:
            parts.append(_inline_media(path))
        payload: dict[str, Any] = {"contents": [{"parts": parts}]}
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        model = os.environ.get("FLOWBOARD_GEMINI_MODEL") or _API_MODEL
        url = _API_ENDPOINT.format(model=model)

        # At least two tries even with a single key: a dropped connection is
        # transient, and failing a whole dispatch on one blip is needless.
        # (Observed: one call died with an httpx error carrying no message at
        # all, and the identical request succeeded immediately after.)
        attempts = max(2, min(_API_MAX_KEY_ATTEMPTS, gemini_keys.count() or 1))
        last_error = "Gemini API call failed"
        for _attempt in range(attempts):
            key = gemini_keys.next_key()
            if not key:
                raise LLMError("No usable Gemini API key is configured.")
            try:
                async with httpx.AsyncClient(
                    timeout=min(timeout, _API_TIMEOUT_S)
                ) as client:
                    resp = await client.post(
                        url, json=payload, headers={"x-goog-api-key": key}
                    )
            except httpx.HTTPError as exc:
                # Several httpx errors stringify to nothing, which turned this
                # into a bare "request failed:" that said less than silence.
                # The class name is the only reliable signal, so carry it.
                # httpx also puts the request — and any header it was handed —
                # into its own message, hence the redact.
                last_error = gemini_keys.redact(
                    f"{type(exc).__name__}: {exc}".rstrip(": "), key
                )
                continue

            if resp.status_code == 200:
                return _text_from_response(resp.json())
            if resp.status_code == 429:
                # This key is out of quota — rest it and try another.
                gemini_keys.mark_exhausted(key)
                last_error = _api_error_message(resp)
                continue
            if resp.status_code == 503:
                # The MODEL is overloaded, which says nothing about the key.
                # Resting a perfectly good key here would shrink the pool for
                # a problem on Google's side.
                last_error = _api_error_message(resp)
                continue
            raise LLMError(gemini_keys.redact(_api_error_message(resp), key))

        raise LLMError(f"Gemini API failed after {attempts} tries. {last_error}")

    async def _invoke_locked(
        self, args: list[str], *, timeout: float
    ) -> str:
        """Subprocess invocation using subprocess.run (Windows-compatible).

        Assumed to be holding ``_call_lock``. `subprocess.run` rather than
        asyncio's own subprocess support, which has real problems on Windows —
        but handed to a thread rather than run inline. Inline it blocked the
        event loop for the whole call: the extension bridge, the worker queue
        and every HTTP handler share that loop, and a slow Gemini answer froze
        all of them for up to `timeout`, which on the chain path is minutes."""
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                args,
                capture_output=True,
                timeout=timeout,
                text=False,  # Keep as bytes for .decode() below
            )
        except FileNotFoundError as exc:
            raise LLMError("gemini CLI not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"gemini CLI timed out after {timeout}s (likely quota exhaustion or network issue)") from exc
        except Exception as exc:
            raise LLMError(f"gemini CLI error: {exc}") from exc

        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")[:400]
            # Check for quota exhaustion error
            if "429" in stderr or "exhausted" in stderr.lower() or "quota" in stderr.lower():
                raise LLMError(f"Gemini quota exhausted: {stderr}")
            raise LLMError(f"gemini CLI exited {result.returncode}: {stderr}")

        stdout = result.stdout.decode(errors="replace").strip()
        # ``-o json`` envelope shape:
        #   {"session_id": "...", "response": "<model text>", "stats": {...}}
        # Extract ``response`` and discard everything else. Banners /
        # ``Tip:`` lines / ANSI codes that older raw-text mode mixed in
        # never reach the caller now because they're outside the JSON.
        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"gemini CLI returned non-JSON output: {stdout[:200]}"
            ) from exc
        if not isinstance(envelope, dict):
            raise LLMError("gemini CLI envelope is not an object")
        response = envelope.get("response")
        if not isinstance(response, str):
            raise LLMError("gemini CLI envelope missing string 'response' field")
        return response.strip()


# ── API helpers ───────────────────────────────────────────────────────────


def _inline_media(path: str) -> dict[str, Any]:
    """Read an attachment into an ``inline_data`` block.

    The CLI takes ``@/path`` tokens and reads the file itself; the REST API
    has no filesystem, so the bytes travel base64-encoded in the request.

    Audio is allowed as well as images: Gemini transcribes it, which is what
    the automatic-subtitle path needs. Everything else is refused here rather
    than sent and rejected by the API with a less obvious message.
    """
    mime, _ = mimetypes.guess_type(path)
    mime = mime or ""
    if not (mime.startswith("image/") or mime.startswith("audio/")):
        raise LLMError(
            f"Attachment is not an image or audio file: {os.path.basename(path)}"
        )
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        raise LLMError(f"Could not read attachment {os.path.basename(path)}") from exc
    return {
        "inline_data": {
            "mime_type": mime,
            "data": base64.b64encode(raw).decode("ascii"),
        }
    }


def _text_from_response(body: Any) -> str:
    """Join the text parts of the first candidate.

    A response can legitimately carry several parts, and taking only the
    first silently truncates a long answer — which then fails downstream as
    a JSON parse error rather than as the truncation it is.
    """
    if not isinstance(body, dict):
        raise LLMError("Gemini API returned an unexpected payload")
    candidates = body.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        # A prompt blocked by safety filters comes back with no candidates
        # and a promptFeedback block explaining why.
        feedback = body.get("promptFeedback")
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise LLMError(
                f"Gemini refused the prompt: {feedback.get('blockReason')}"
            )
        raise LLMError("Gemini API returned no candidates")
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
    parts = content.get("parts") if isinstance(content, dict) else None
    if not isinstance(parts, list):
        raise LLMError("Gemini API candidate carried no content")
    text = "".join(
        p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)
    ).strip()
    if not text:
        raise LLMError("Gemini API returned an empty response")
    return text


def _api_error_message(resp: httpx.Response) -> str:
    """The API's own explanation, without echoing the whole body."""
    try:
        detail = resp.json().get("error", {}).get("message")
    except Exception:
        detail = None
    return f"Gemini API {resp.status_code}: {detail or resp.reason_phrase}"
