"""OpenAI provider — dual-mode (Codex CLI preferred · REST API fallback).

OpenAI is the only provider that supports two transports:

1. **Codex CLI** (`@openai/codex`) — preferred. Authenticates via the
   user's ChatGPT Plus/Pro OAuth, no API key needed. Same
   "use your existing subscription" benefit as Claude / Gemini CLIs.

2. **REST API** — fallback. Used when:
   - Codex CLI isn't installed, OR
   - Codex CLI is installed but the user's version is text-only AND
     this dispatch needs vision.

Vision capability of Codex CLI varies between versions. We probe
``codex --help`` once at first vision call and detect which image flag
(if any) is advertised. If none, the provider treats Codex as text-only
and routes vision requests through the API mode (assuming an API key
is configured; raises if not).

The class API contract: ``is_available()`` is True if at least one mode
is usable. ``run()`` picks the right mode automatically based on
attachment presence + cached probe results. Callers stay ignorant of
which transport ran.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import re
import subprocess
import time
from pathlib import Path
from typing import Optional

import httpx

from .base import LLMError, safe_error_message as _safe_error_message
from . import cli_auth, secrets
from .cli_utils import (
    resolve_cli_binary,
    validate_prompt_size,
    validate_attachment_paths,
    run_cli,
    CLI_PROBE_TIMEOUT,
)

logger = logging.getLogger(__name__)


_CLI_BIN = "codex"


def _unlink(path: str) -> None:
    """Delete a temp file, never raising. Cleanup must not mask the real
    failure the caller is already reporting."""
    import os

    try:
        os.unlink(path)
    except OSError:
        pass


def _auth_hint(mode: str) -> str:
    """What to add to a failed dispatch so the reader looks at auth.

    Earned the hard way. On 2026-09-01 codex was signed in with a key that
    OpenAI rejects outright (`invalid_api_key` on `/v1/models`), and its
    only visible symptom was `ERROR: Reconnecting... 1/5`. That wording
    points at the network, the network was fine, and a whole debugging
    session went the wrong way. The stderr line stays verbatim; this only
    names the identity in play so the next reader has both facts at once.
    """
    if mode == cli_auth.APIKEY:
        return (
            " — codex is signed in with an API key, not your ChatGPT plan. "
            "If that key is dead this reads as a connection error. "
            "Run `codex login` to use your subscription."
        )
    if mode == cli_auth.NONE:
        return " — codex is not signed in. Run `codex login`."
    return ""


def _last_meaningful_line(stderr: bytes) -> str:
    """The line of stderr worth showing the user.

    The codex CLI prefixes every run with a wall of `warning: Ignoring
    malformed agent role definition:` lines from the user's own
    `~/.codex/agents/*.toml`. Those are unrelated to the request, and taking
    the FIRST 400 characters — which is what this used to do — showed only
    those and buried the actual error (`ERROR: Reconnecting... 1/5`) below
    the cut.
    """
    text = stderr.decode(errors="replace")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    interesting = [ln for ln in lines if not ln.lower().startswith("warning:")]
    chosen = interesting or lines
    return " | ".join(chosen[-3:])[:400] if chosen else "no output"


_API_URL = "https://api.openai.com/v1/chat/completions"
_PROBE_TIMEOUT = 5.0
_DEFAULT_TIMEOUT = 90.0
_DEFAULT_TEXT_MODEL = "gpt-5"
_DEFAULT_VISION_MODEL = "gpt-4o"
_AVAILABILITY_TTL_S = 60.0
_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024

# Image-flag candidates ordered by likelihood. First match wins.
_IMAGE_FLAG_CANDIDATES = ("--image", "--attach", "--file", "--input")


class OpenAIProvider:
    """Conforms to ``LLMProvider``. Dual-mode dispatch."""

    name: str = "openai"
    supports_vision: bool = True  # via at least one of the two modes
    # Chat transports only. OpenAI *does* have `/v1/audio/transcriptions`,
    # but that is a different endpoint this provider does not speak, and
    # the ChatGPT OAuth token cannot reach it at all.
    supports_audio: bool = False

    def __init__(self) -> None:
        # CLI probe state (set by `_probe_cli`).
        # `cli_available` = True when binary present + version probe succeeds.
        # `cli_image_flag` = resolved flag string, or None for "text-only Codex".
        self._cli_probed: bool = False
        self._cli_available: bool = False
        self._cli_image_flag: Optional[str] = None

        # API availability cache (separate from CLI — they're independent).
        self._api_cached_at: Optional[float] = None
        self._api_value: Optional[bool] = None

        # Which identity the Codex CLI is signed in with. None = not probed.
        self._auth_mode: Optional[str] = None

        # Serialises `_probe_cli` — see the note there.
        self._probe_lock = asyncio.Lock()

    def reset_cache(self) -> None:
        """Testing hook + Settings panel rescan support."""
        self._cli_probed = False
        self._cli_available = False
        self._cli_image_flag = None
        self._api_cached_at = None
        self._api_value = None
        self._auth_mode = None

    async def auth_mode(self) -> str:
        """Which identity `codex` is signed in with: oauth / apikey / none.

        Distinct from `is_available`, which only proves the binary answers
        `--version` — it does that just as happily when the stored key is
        dead, which is how a provider that could not answer a single call
        showed up as "Connected".
        """
        await self._probe_cli()
        return self._auth_mode or cli_auth.NONE

    # ── CLI probe ────────────────────────────────────────────────────

    async def _probe_cli(self) -> None:
        """Resolve `_cli_available` + `_cli_image_flag` once per agent
        lifetime. Called lazily on the first availability check.

        Both steps go through ``run_cli``. They used to call
        ``subprocess.run`` directly from this coroutine, which pins the
        event loop — the same defect that was fixed on the dispatch path
        and missed here because a 5-second bound made it look harmless.

        Held under a lock, and the "already probed" flag is only set once
        the work is done. Setting it up front — which is what this did —
        means a request arriving mid-probe skips the wait and reads fields
        that have not been filled in yet. On a cold agent that showed up as
        OpenAI reporting `available: false, mode: none` on the first poll
        and `cli` on the next, which reads exactly like a broken install.
        """
        if self._cli_probed:
            return
        async with self._probe_lock:
            # Re-checked inside the lock: whoever held it may have finished
            # the probe while this caller was waiting for it.
            if self._cli_probed:
                return
            await self._probe_cli_locked()
            self._cli_probed = True

    async def _probe_cli_locked(self) -> None:
        """The probe body. Only ever called with `_probe_lock` held."""

        # Step 1: does the binary exist + run `--version`?
        try:
            codex_bin = resolve_cli_binary(_CLI_BIN, CLI_PROBE_TIMEOUT)
            result = await run_cli([codex_bin, "--version"], timeout=CLI_PROBE_TIMEOUT)
            self._cli_available = result.returncode == 0
        except (FileNotFoundError, PermissionError):
            self._cli_available = False
            return
        except (subprocess.TimeoutExpired, Exception):
            self._cli_available = False
            return

        if not self._cli_available:
            return

        # Step 2: which identity is codex signed in with? Resolved here,
        # with the other probes, so a dispatch never has to stop and ask —
        # `_run_cli` reads the cached answer.
        self._auth_mode = await cli_auth.detect_auth_mode(_CLI_BIN)

        # Step 3: parse `--help` for an image-attachment flag.
        try:
            codex_bin = resolve_cli_binary(_CLI_BIN, CLI_PROBE_TIMEOUT)
            result = await run_cli([codex_bin, "--help"], timeout=CLI_PROBE_TIMEOUT)
            stdout_b = result.stdout
        except (FileNotFoundError, PermissionError, subprocess.TimeoutExpired):
            return
        except Exception:
            logger.exception("openai: unexpected error during codex --help probe")
            return

        help_text = stdout_b.decode(errors="replace")
        for candidate in _IMAGE_FLAG_CANDIDATES:
            if re.search(rf"(^|\s){re.escape(candidate)}(\s|=|\b)", help_text):
                self._cli_image_flag = candidate
                logger.info("openai: codex image flag = %s", candidate)
                return
        logger.info("openai: codex --help advertises no image flag (text-only)")

    # ── API probe ────────────────────────────────────────────────────

    async def _api_available(self) -> bool:
        """True when an API key is configured. We don't ping the API
        here — `/v1/models` costs a request, and the key presence alone
        is enough for the routing decision (the actual Test endpoint
        confirms by sending a real ping)."""
        now = time.monotonic()
        if (
            self._api_value is not None
            and self._api_cached_at is not None
            and now - self._api_cached_at < _AVAILABILITY_TTL_S
        ):
            return self._api_value
        key = secrets.get_api_key("openai")
        ok = bool(key)
        self._api_value = ok
        self._api_cached_at = now
        return ok

    # ── public API ───────────────────────────────────────────────────

    async def is_available(self) -> bool:
        """True when at least one of CLI / API is usable."""
        await self._probe_cli()
        if self._cli_available:
            return True
        return await self._api_available()

    async def run(
        self,
        user_prompt: str,
        *,
        system_prompt: Optional[str] = None,
        attachments: Optional[list[str]] = None,
        timeout: float = _DEFAULT_TIMEOUT,
        model: Optional[str] = None,
    ) -> str:
        await self._probe_cli()
        api_ok = await self._api_available()

        # Mode resolution table (see plan UI Spec for the user-visible
        # version; this is its functional twin):
        #   CLI status × attachments → which mode
        #     cli_available + flag found:           CLI (any dispatch)
        #     cli_available + no flag + no attach:  CLI (text dispatch fine)
        #     cli_available + no flag + attach:     API fallback (requires key)
        #     cli_unavailable:                      API (requires key)
        if self._cli_available:
            wants_vision = bool(attachments)
            cli_supports_this = (self._cli_image_flag is not None) or not wants_vision
            if cli_supports_this:
                return await self._run_cli(
                    user_prompt, system_prompt, attachments, timeout
                )
            # Codex is text-only — fall through to API for this dispatch.
            if not api_ok:
                raise LLMError(
                    "OpenAI Codex CLI does not support vision in your version. "
                    "Either upgrade Codex CLI or configure an OpenAI API key."
                )
            return await self._run_api(
                user_prompt, system_prompt, attachments, timeout, model
            )

        # No CLI — API only.
        if not api_ok:
            raise LLMError("OpenAI is not configured (no Codex CLI, no API key)")
        return await self._run_api(
            user_prompt, system_prompt, attachments, timeout, model
        )

    @property
    def mode(self) -> str:
        """Reported by /api/llm/providers so the UI knows which row state
        to render. Returns the mode that `run()` would currently pick for
        a TEXT dispatch (vision can fall through to API even when this
        says 'cli'). Values: 'cli' / 'api' / 'none'."""
        # Probe-on-read so the property stays sync; callers that want
        # freshness should await `is_available()` first.
        if self._cli_probed and self._cli_available:
            return "cli"
        if self._api_value:
            return "api"
        return "none"

    # ── CLI dispatch ─────────────────────────────────────────────────

    async def _run_cli(
        self,
        user_prompt: str,
        system_prompt: Optional[str],
        attachments: Optional[list[str]],
        timeout: float,
    ) -> str:
        """Run one prompt through `codex exec` and return the final message.

        Written against the CLI as it actually is (measured on codex-cli
        0.147.0), not as it once was. Three things in the previous version
        were wrong enough to make every call fail at argument parsing:

        * ``--output-format json`` does not exist. The CLI offers ``--json``
          (a JSONL **event stream**) and ``-o FILE`` (the final message,
          plain text). ``-o`` is used here because its contract is one line
          of ``--help`` and needs no guessing about event shapes; the event
          stream was left alone because its schema could not be verified
          end-to-end on this machine.
        * ``-p`` is ``--profile``, not "prompt". The prompt is positional,
          or ``-`` to read stdin.
        * ``--system`` does not exist at all, so a system prompt has to be
          folded into the text.

        Stdin still carries the prompt: for a ``.cmd``-shimmed binary on
        Windows, cmd.exe re-parses argv and mangles newlines and quotes in
        long prompts. Note the CLI blocks reading stdin until EOF even when
        a positional prompt is given — ``subprocess.run(input=...)`` closes
        it, which is why this works and an interactive shell pipe hangs.
        """
        import os
        import tempfile

        # Validate inputs
        try:
            validate_prompt_size(user_prompt)
            if system_prompt:
                validate_prompt_size(system_prompt)
            validate_attachment_paths(attachments)
        except ValueError as exc:
            raise LLMError(f"Invalid input: {exc}") from exc

        codex_bin = resolve_cli_binary(_CLI_BIN, CLI_PROBE_TIMEOUT)

        # No `--system` flag exists, so the system prompt is folded in. The
        # separator is explicit rather than a bare newline so the model can
        # tell the instruction from the request.
        prompt = user_prompt
        if system_prompt:
            prompt = f"{system_prompt}\n\n---\n\n{user_prompt}"

        # A fresh file per call: two concurrent dispatches sharing one path
        # would race, and the loser would return the other's answer.
        fd, out_path = tempfile.mkstemp(prefix="codex-", suffix=".txt")
        os.close(fd)

        args: list[str] = [
            codex_bin, "exec",
            # The agent's working directory is not guaranteed to be a git
            # repo, and codex refuses to start in one that is not.
            "--skip-git-repo-check",
            # This is a text round-trip; the model has no business running
            # commands on the user's machine to answer it.
            "--sandbox", "read-only",
            "--output-last-message", out_path,
            "-",
        ]
        if attachments and self._cli_image_flag:
            for path in attachments:
                args += [self._cli_image_flag, os.path.abspath(path)]

        try:
            # Through `run_cli`, not `subprocess.run`: this is a coroutine,
            # and a blocking call here freezes every other request for the
            # length of the dispatch. It also makes the timeout real — the
            # `.cmd` shim's node child has to be killed too.
            # Read, not probed: `_probe_cli` already resolved this, and a
            # dispatch is the wrong place to go asking who we are.
            mode = self._auth_mode or cli_auth.NONE
            result = await run_cli(
                args,
                stdin_data=prompt.encode("utf-8"),
                timeout=timeout,
                # Only when OAuth is the login, so "runs on your ChatGPT
                # plan" holds by construction. Measured caveat in
                # `cli_auth.env_without_api_keys`: the stored login already
                # wins on this version, so this is belt-and-braces.
                env=(
                    cli_auth.env_without_api_keys(_CLI_BIN)
                    if mode == cli_auth.OAUTH
                    else None
                ),
            )
        except FileNotFoundError as exc:
            _unlink(out_path)
            raise LLMError("codex CLI not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            _unlink(out_path)
            raise LLMError(f"codex CLI timed out after {timeout}s") from exc
        except Exception as exc:
            _unlink(out_path)
            raise LLMError(f"codex CLI error: {exc}") from exc

        try:
            if result.returncode != 0:
                # stderr carries the real reason — surface it verbatim so
                # the user acts on evidence rather than on a wrapper's
                # guess. The hint is appended, never substituted.
                stderr = _last_meaningful_line(result.stderr)
                raise LLMError(
                    f"codex CLI exited {result.returncode}: {stderr}"
                    f"{_auth_hint(mode)}"
                )

            try:
                answer = Path(out_path).read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                raise LLMError(f"codex CLI wrote no output file: {exc}") from exc

            if not answer.strip():
                # Exit 0 with an empty answer: the run ended without the
                # model producing a message. Reporting success here would
                # hand the caller an empty string as if it were a result.
                raise LLMError(
                    "codex CLI returned an empty answer: "
                    f"{_last_meaningful_line(result.stderr)}"
                )
            return answer.strip()
        finally:
            _unlink(out_path)

    # ── API dispatch ─────────────────────────────────────────────────

    async def _run_api(
        self,
        user_prompt: str,
        system_prompt: Optional[str],
        attachments: Optional[list[str]],
        timeout: float,
        model: Optional[str],
    ) -> str:
        key = secrets.get_api_key("openai")
        if not key:
            raise LLMError("OpenAI API key not configured")

        chosen_model = model or (
            _DEFAULT_VISION_MODEL if attachments else _DEFAULT_TEXT_MODEL
        )

        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if attachments:
            content: list[dict] = [{"type": "text", "text": user_prompt}]
            for path in attachments:
                content.append(_image_url_block(path))
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": user_prompt})

        payload = {"model": chosen_model, "messages": messages}

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    _API_URL,
                    headers={
                        "authorization": f"Bearer {key}",
                        "content-type": "application/json",
                    },
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise LLMError(f"openai request timed out after {timeout}s") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"openai transport error: {exc}") from exc

        if resp.status_code != 200:
            raise LLMError(
                f"openai HTTP {resp.status_code}: {_safe_error_message(resp)}"
            )

        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMError("openai response was not JSON") from exc
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"openai response missing content: {data!r:.200}") from exc


# ── helpers ───────────────────────────────────────────────────────────

def _image_url_block(path: str) -> dict:
    p = Path(path)
    size = p.stat().st_size
    if size > _MAX_ATTACHMENT_BYTES:
        raise LLMError(
            f"attachment too large for openai: "
            f"{size // (1024 * 1024)}MB > 5MB cap"
        )
    mime = mimetypes.guess_type(path)[0] or "image/jpeg"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{b64}"},
    }

