"""Subprocess wrapper around the local ``claude`` CLI.

Flowboard's planner invokes this CLI instead of calling the Anthropic API
directly. Two upsides:
- no API key management; relies on the user's existing Claude subscription
- matches Flowboard's local-only single-user philosophy

The CLI is invoked with ``--output-format json`` so we get a structured
envelope of the form ``{"type":"result","result":"<LLM text>", ...}``. The
``result`` field is the LLM's plain-text response — we return that string
and let the caller parse further (e.g. extract a fenced JSON block).
"""
from __future__ import annotations

import json
import logging
import subprocess
from typing import Optional

from .llm import cli_auth
from .llm.cli_utils import (
    resolve_cli_binary,
    run_cli,
    validate_attachment_paths,
    validate_prompt_size,
    DEFAULT_SUBPROCESS_TIMEOUT,
    CLI_PROBE_TIMEOUT,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = DEFAULT_SUBPROCESS_TIMEOUT
_CLI_BIN = "claude"

# Cached availability probe. None = not probed yet.
_available: Optional[bool] = None
# Cached auth-mode probe, with the same lifetime as `_available`.
_auth_mode: Optional[str] = None


class ClaudeCliError(RuntimeError):
    """Raised when the CLI invocation fails (non-zero exit, bad envelope, timeout)."""


async def _probe_available() -> bool:
    # Through `run_cli`, not `subprocess.run`: this is a coroutine, and a
    # blocking call here pins the event loop. Bounded at 5s it only froze
    # the agent briefly, which is exactly why it survived the round that
    # fixed the same bug on the dispatch path.
    try:
        claude_bin = resolve_cli_binary(_CLI_BIN, CLI_PROBE_TIMEOUT)
        result = await run_cli([claude_bin, "--version"], timeout=CLI_PROBE_TIMEOUT)
        if result.returncode == 0:
            logger.info("claude_cli: SUCCESS - found claude at %s", claude_bin)
            return True
        logger.warning("claude_cli: probe returned code %d", result.returncode)
        return False
    except subprocess.TimeoutExpired:
        logger.warning("claude_cli: probe timed out")
        return False
    except Exception as e:
        logger.warning("claude_cli: probe failed - %s", e)
        return False


async def is_available(force: bool = False) -> bool:
    """Cached check: is the ``claude`` CLI usable on this host?"""
    global _available
    if _available is None or force:
        _available = await _probe_available()
        logger.info("claude_cli: available=%s", _available)
    return _available


async def auth_mode(force: bool = False) -> str:
    """Which identity the CLI is signed in with: oauth / apikey / none.

    Note what this is NOT: ``is_available`` only proves the binary answers
    ``--version``, which it does just as happily when signed out. Callers
    that need "can actually dispatch" want both.
    """
    global _auth_mode
    if _auth_mode is None or force:
        if not await is_available(force=force):
            _auth_mode = cli_auth.NONE
        else:
            _auth_mode = await cli_auth.detect_auth_mode(_CLI_BIN)
    return _auth_mode


def _child_env() -> Optional[dict[str, str]]:
    """Environment for a dispatch — scrubbed only when signed in via OAuth.

    Makes "running on your subscription" true by construction rather than
    by the CLI's internal precedence rules. Measured caveat in
    ``cli_auth.env_without_api_keys``: on the version here the stored login
    already wins on its own, so this is belt-and-braces, not a live fix.
    When the key *is* the login the variable stays — stripping it there
    could remove the only credential the user has.

    Reads the cached probe rather than triggering one — an unresolved
    identity means "leave the environment alone", the same as a key login.
    """
    if _auth_mode == cli_auth.OAUTH:
        return cli_auth.env_without_api_keys(_CLI_BIN)
    return None


def reset_availability_cache() -> None:
    """Testing hook, and the Settings panel's re-check after a login."""
    global _available, _auth_mode
    _available = None
    _auth_mode = None


async def run_claude(
    user_prompt: str,
    *,
    system_prompt: Optional[str] = None,
    attachments: Optional[list[str]] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """Invoke ``claude -p PROMPT`` and return the LLM's text result.

    ``attachments``: list of absolute file paths (typically images) to feed
    the model. Embedded as ``@<path>`` tokens in the prompt — the CLI reads
    those files and forwards them as multimodal blocks. We never quote the
    path because it sits inside an argv token (no shell), and we resolve to
    absolute so a CLI cwd surprise can't break the lookup.

    For attachments to work the parent directory MUST be allow-listed via
    ``--add-dir`` AND the Read tool must be auto-approved
    (``--permission-mode bypassPermissions``); without these the CLI
    prompts the user for permission and our `-p` non-interactive call gets
    a refusal text back instead of a description.

    Raises ``ClaudeCliError`` on failure, timeout, or malformed envelope.
    The prompt is passed as a separate argv token — no shell interpolation.
    """
    import os

    # Validate inputs
    try:
        validate_prompt_size(user_prompt)
        if system_prompt:
            validate_prompt_size(system_prompt)
        validate_attachment_paths(attachments)
    except ValueError as exc:
        raise ClaudeCliError(f"Invalid input: {exc}") from exc

    full_prompt = user_prompt
    if attachments:
        # `@<path>` syntax handled by the CLI for file attachments.
        suffix = " ".join(f"@{os.path.abspath(p)}" for p in attachments)
        full_prompt = f"{user_prompt}\n\n{suffix}" if user_prompt else suffix

    # Resolve claude binary path: try PATH first, then npm locations
    claude_bin = resolve_cli_binary(_CLI_BIN, CLI_PROBE_TIMEOUT)
    # Pipe the prompt via stdin instead of `-p <prompt>` argv.
    #
    # Why: on Windows, npm-installed CLIs are ``.cmd`` shims. Python's
    # subprocess.run on a ``.cmd`` re-invokes through cmd.exe, which
    # re-parses arguments — newlines / ``"`` / ``&`` / ``|`` inside the
    # prompt get split, and the CLI ends up seeing an empty / truncated
    # ``-p`` payload. Symptom on the wire was:
    #
    #   ClaudeCliError: claude CLI returned non-JSON output:
    #   "I see the system reminders about deferred tools, available
    #    skills, and project context. No user request has been made yet
    #    — what would you like me to do?"
    #
    # i.e. claude received NO real prompt and replied conversationally
    # in plain text. Switching to stdin sidesteps cmd.exe's argv parser
    # entirely — bytes flow straight to claude's stdin. macOS / Linux
    # behaviour is unchanged (stdin works there too).
    args: list[str] = [claude_bin, "-p", "--output-format", "json"]
    if system_prompt:
        args += ["--append-system-prompt", system_prompt]
    if attachments:
        # Allow-list each attachment's parent dir so the Read tool can
        # access it, and bypass the interactive permission prompt that
        # would otherwise stall a non-interactive `-p` invocation.
        seen_dirs: set[str] = set()
        for path in attachments:
            parent = os.path.dirname(os.path.abspath(path))
            if parent and parent not in seen_dirs:
                seen_dirs.add(parent)
                args += ["--add-dir", parent]
        args += ["--permission-mode", "bypassPermissions"]

    # `run_cli` rather than `subprocess.run`: asyncio's own subprocess
    # support is unreliable on Windows, but a bare blocking call from a
    # coroutine pins the event loop for the whole dispatch — a measured
    # 38-second Claude ping froze every other request for 38 seconds. This
    # runs the same synchronous call on a worker thread, and kills the
    # `.cmd` shim's node child when the timeout fires so the timeout is
    # actually enforced.
    try:
        result = await run_cli(
            args,
            stdin_data=full_prompt.encode("utf-8"),
            timeout=timeout,
            env=_child_env(),
        )
    except FileNotFoundError as exc:
        raise ClaudeCliError("claude CLI not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ClaudeCliError(f"claude CLI timed out after {timeout}s") from exc
    except Exception as exc:
        raise ClaudeCliError(f"claude CLI error: {exc}") from exc

    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")[:400]
        raise ClaudeCliError(f"claude CLI exited {result.returncode}: {stderr}")

    stdout = result.stdout.decode(errors="replace")
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeCliError(
            f"claude CLI returned non-JSON output: {stdout[:200]}"
        ) from exc

    if not isinstance(envelope, dict):
        raise ClaudeCliError("claude CLI envelope is not an object")

    if envelope.get("is_error"):
        raise ClaudeCliError(
            f"claude CLI reported error: {envelope.get('result') or envelope.get('subtype')}"
        )

    result_text = envelope.get("result")
    if not isinstance(result_text, str):
        raise ClaudeCliError("claude CLI envelope missing string 'result' field")

    return result_text
