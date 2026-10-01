"""Shared utilities for LLM CLI providers (Claude, Gemini, OpenAI Codex).

Consolidates cross-provider patterns:
- Binary path resolution (PATH + Windows npm fallback)
- Running a CLI without freezing the event loop or outliving its timeout
- Subprocess error handling
- Input validation (prompt size, attachment limits)
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
from typing import Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

# Subprocess timeouts
CLI_PROBE_TIMEOUT = 5.0
DEFAULT_SUBPROCESS_TIMEOUT = 90.0

# Input validation limits
MAX_PROMPT_BYTES = 100 * 1024  # 100 KB
MAX_ATTACHMENTS = 10

# Windows npm paths
_WINDOWS_NPM_PATHS = [
    ("APPDATA", "npm"),
    ("USERPROFILE", "AppData", "Roaming", "npm"),
    ("HOME", "AppData", "Roaming", "npm"),
]


def get_windows_npm_paths(cli_name: str) -> list[str]:
    r"""Get dynamic list of Windows npm paths for a CLI tool.

    Checks:
    1. %APPDATA%\npm\<cli_name>.cmd
    2. %USERPROFILE%\AppData\Roaming\npm\<cli_name>.cmd
    3. ~\AppData\Roaming\npm\<cli_name>.cmd (via expanduser)

    Returns list of paths to check (may be empty if no env vars set).
    """
    paths = []

    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(os.path.join(appdata, "npm", f"{cli_name}.cmd"))

    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        paths.append(
            os.path.join(userprofile, "AppData", "Roaming", "npm", f"{cli_name}.cmd")
        )

    home = os.path.expanduser("~")
    if home and home != "~":  # expanduser returns ~ if HOME not set
        paths.append(os.path.join(home, "AppData", "Roaming", "npm", f"{cli_name}.cmd"))

    return paths


def resolve_cli_binary(
    cli_name: str, timeout: float = CLI_PROBE_TIMEOUT
) -> str:
    """Resolve CLI binary path: try PATH first, then Windows npm locations.

    Args:
        cli_name: Name of the CLI tool (e.g., "claude", "gemini", "codex")
        timeout: Timeout for --version probe (seconds)

    Returns:
        Resolved binary path, or cli_name as fallback (will error later if not found)
    """
    # Try PATH first
    if cli_path := shutil.which(cli_name):
        logger.debug(f"{cli_name}: resolved from PATH: {cli_path}")
        return cli_path

    # Try Windows npm locations
    for npm_path in get_windows_npm_paths(cli_name):
        if os.path.exists(npm_path):
            try:
                result = subprocess.run(
                    [npm_path, "--version"],
                    capture_output=True,
                    timeout=timeout,
                )
                if result.returncode == 0:
                    logger.info(f"{cli_name}: resolved from npm: {npm_path}")
                    return npm_path
            except (subprocess.TimeoutExpired, Exception):
                pass

    logger.warning(
        f"{cli_name}: not found in PATH or npm locations, falling back to '{cli_name}'"
    )
    return cli_name


def _kill_tree(proc: subprocess.Popen) -> None:
    """Kill a CLI process AND its children.

    Every one of these CLIs is installed by npm, so on Windows the thing we
    launch is a ``.cmd`` shim that spawns ``node``. Killing only the shim
    leaves node running: after one wedged dispatch this machine had nine
    orphaned ``node.exe`` processes still resident.

    To be precise about what this does and does not fix — a mutation test
    settled it — killing the tree is NOT what unblocks the caller. The
    bounded drain in ``run_cli_sync`` is; see the comment there. This stops
    the orphan from outliving the request, which is a separate problem and
    a real one.
    """
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=10,
            )
        else:
            proc.kill()
    except Exception:
        logger.warning("cli: could not kill process tree for pid %s", proc.pid)
    finally:
        try:
            proc.wait(timeout=5)
        except Exception:
            pass


def run_cli_sync(
    args: Sequence[str],
    *,
    stdin_data: bytes = b"",
    timeout: float,
    env: Optional[Mapping[str, str]] = None,
) -> subprocess.CompletedProcess:
    """Run a CLI to completion, killing the whole tree if it overruns.

    Same contract as ``subprocess.run(capture_output=True, timeout=…)``,
    except that the timeout is actually enforced — see ``_kill_tree``.

    ``env=None`` inherits the agent's environment, which is what every
    caller did before this parameter existed. Pass an explicit mapping to
    take an environment variable away from the child — see
    ``cli_auth.env_without_api_keys``, which uses it so a stray
    ``ANTHROPIC_API_KEY`` cannot override an OAuth login.

    Blocking on purpose: callers inside async code must reach it through
    ``run_cli`` below, never call it directly.
    """
    proc = subprocess.Popen(
        list(args),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=None if env is None else dict(env),
    )
    try:
        out, err = proc.communicate(input=stdin_data, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        # THIS bound is what makes the timeout real. The pipe can still be
        # held by a grandchild the kill did not reach in time, and an
        # unbounded drain waits for that grandchild to exit — measured at
        # the full 60s of a test child that was meant to be cut off at 2s.
        # In production the same shape left a 120s dispatch running past
        # 300s with the whole agent unresponsive.
        try:
            out, err = proc.communicate(timeout=5)
        except Exception:
            out, err = b"", b""
        raise subprocess.TimeoutExpired(
            list(args), timeout, output=out, stderr=err
        ) from None
    return subprocess.CompletedProcess(list(args), proc.returncode, out, err)


async def run_cli(
    args: Sequence[str],
    *,
    stdin_data: bytes = b"",
    timeout: float,
    env: Optional[Mapping[str, str]] = None,
) -> subprocess.CompletedProcess:
    """``run_cli_sync`` on a worker thread.

    The CLI providers used to call ``subprocess.run`` straight from a
    coroutine, which pins the event loop for the whole dispatch: a Claude
    ping measured at 38 seconds froze every other request for 38 seconds,
    and a hung Codex call froze the agent outright.

    Every subprocess these providers start belongs here — dispatches,
    version probes and auth probes alike. The probes are individually
    short, but they run inside the same coroutines, and "short" is a
    property of a healthy machine rather than a guarantee.
    """
    return await asyncio.to_thread(
        run_cli_sync, args, stdin_data=stdin_data, timeout=timeout, env=env
    )


def validate_prompt_size(prompt: str, max_bytes: int = MAX_PROMPT_BYTES) -> None:
    """Validate prompt doesn't exceed size limit.

    Args:
        prompt: The user or system prompt
        max_bytes: Maximum allowed size in bytes

    Raises:
        ValueError: If prompt exceeds limit
    """
    if len(prompt.encode("utf-8")) > max_bytes:
        raise ValueError(
            f"Prompt exceeds {max_bytes // 1024}KB limit "
            f"({len(prompt.encode('utf-8'))} bytes)"
        )


def validate_attachment_paths(
    attachments: Optional[list[str]], max_count: int = MAX_ATTACHMENTS
) -> None:
    """Validate attachments exist and are readable.

    Args:
        attachments: List of file paths
        max_count: Maximum number of attachments allowed

    Raises:
        ValueError: If validation fails
    """
    if not attachments:
        return

    if len(attachments) > max_count:
        raise ValueError(f"Too many attachments (max {max_count}, got {len(attachments)})")

    for path in attachments:
        abs_path = os.path.abspath(path)
        if not os.path.isfile(abs_path):
            raise ValueError(f"Attachment not found: {path}")
        if not os.access(abs_path, os.R_OK):
            raise ValueError(f"Attachment not readable: {path}")


def validate_model_name(
    model: Optional[str], allowed: Optional[set[str]] = None
) -> Optional[str]:
    """Validate model name against whitelist.

    Args:
        model: Model name from user input or environment
        allowed: Set of allowed model names (if None, validation skipped)

    Returns:
        Validated model name, or None if invalid (caller should use default)

    Raises:
        ValueError: If validation is strict and model not allowed
    """
    if not model or not allowed:
        return model
    if model in allowed:
        return model
    logger.warning(f"Unknown model '{model}', not in allowed set: {allowed}")
    return None
