"""Which identity a CLI provider is signed in with — OAuth or an API key.

Separate from ``cli_utils`` on purpose: that module is about running a
process safely, this one is about *who* the process will act as. They meet
only at ``run_cli``.

This exists because the Settings panel used to report the **transport**
(``cli`` / ``api``) and hardcode the **identity** next to it — the OpenAI
card read "ChatGPT CLI · API key" as a fixed string. On 2026-09-01 that
combination hid a real failure for an entire debugging session: codex was
signed in with an API key that OpenAI rejects (``invalid_api_key``), the
CLI reported it as ``ERROR: Reconnecting... 1/5`` — a message that never
mentions authentication — and the card still read "Connected", because
``codex --version`` answers fine no matter who you are. The diagnosis went
to the network and stayed there.

So: ask each CLI who it is, through the CLI's own documented surface, and
report the answer instead of a label.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from typing import Mapping, Optional

from .cli_utils import resolve_cli_binary, run_cli, CLI_PROBE_TIMEOUT

logger = logging.getLogger(__name__)

#: Signed in against a subscription — the CLI bills the user's plan.
OAUTH = "oauth"
#: Signed in with an API key — the CLI bills a pay-per-token account.
APIKEY = "apikey"
#: Not signed in, or the CLI could not be asked.
NONE = "none"

#: Node startup on Windows is not instant and neither CLI answers from a
#: warm process, so this is looser than ``CLI_PROBE_TIMEOUT``. Measured:
#: ``claude auth status --json`` well under 1s, ``codex login status``
#: effectively instant (it reads a local file). The margin is for a cold
#: machine, not for a hung one — the result is cached, so paying it often
#: is not the risk; blocking a poll on it is.
AUTH_PROBE_TIMEOUT = 20.0

#: Environment variables that select a key-based identity for each CLI.
#: Removed from the child environment when that CLI is signed in via OAuth
#: — see ``env_without_api_keys`` for what that is and is not worth.
KEY_ENV_VARS: dict[str, tuple[str, ...]] = {
    "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "codex": ("OPENAI_API_KEY",),
}


# ── parsers (pure — the tests hit these directly) ─────────────────────


def parse_claude_auth(returncode: int, stdout: str) -> str:
    """Read ``claude auth status --json``.

    A real machine-readable contract: ``--json`` is the documented default
    of that subcommand, and the payload carries ``loggedIn`` / ``authMethod``
    / ``subscriptionType``. ``authMethod`` is the field that separates a
    subscription login from a key.
    """
    if returncode != 0:
        return NONE
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError):
        return NONE
    if not isinstance(payload, dict) or not payload.get("loggedIn"):
        return NONE
    # Observed value for a subscription login: "claude.ai". Anything else
    # while logged in is a key-shaped identity.
    return OAUTH if payload.get("authMethod") == "claude.ai" else APIKEY


def parse_codex_auth(returncode: int, stdout: str) -> str:
    """Read ``codex login status``.

    Text only — that subcommand has no ``--json``. Both wordings are now
    observed on codex-cli 0.147.0: ``Logged in using an API key - sk-…``
    and ``Logged in using ChatGPT``.

    The asymmetry is kept anyway. Only the API-key phrasing is matched
    positively; anything else that says it is signed in falls through to
    OAuth. That way a reworded ChatGPT line degrades to the right answer
    rather than to "not signed in", which is the failure that would make a
    working provider look broken.
    """
    if returncode != 0:
        return NONE
    text = (stdout or "").strip()
    if not text:
        return NONE
    if re.search(r"api[\s_-]?key", text, re.IGNORECASE):
        return APIKEY
    if re.search(r"not\s+logged\s+in|logged\s+out", text, re.IGNORECASE):
        return NONE
    return OAUTH


#: CLI name → (argv after the binary, parser, which stream carries the answer).
#:
#: The stream is per-CLI because they genuinely differ, measured on this
#: machine rather than assumed: ``claude auth status --json`` writes 258
#: bytes of JSON to **stdout** and nothing to stderr, while
#: ``codex login status`` writes its line to **stderr** and leaves stdout
#: empty. Reading stdout for both — the obvious first guess, and the one
#: this code shipped with for about ten minutes — reports every codex login
#: as "not signed in".
_PROBES: dict[str, tuple[tuple[str, ...], object, str]] = {
    "claude": (("auth", "status", "--json"), parse_claude_auth, "stdout"),
    "codex": (("login", "status"), parse_codex_auth, "stderr"),
}


# ── probe ─────────────────────────────────────────────────────────────


async def detect_auth_mode(cli_name: str) -> str:
    """Ask a CLI which identity it is signed in with.

    Never raises: a provider that cannot be asked is reported as ``NONE``,
    which is what the caller would do with an exception anyway.
    """
    probe = _PROBES.get(cli_name)
    if probe is None:
        return NONE
    argv, parser, stream = probe
    try:
        binary = resolve_cli_binary(cli_name, CLI_PROBE_TIMEOUT)
        result = await run_cli([binary, *argv], timeout=AUTH_PROBE_TIMEOUT)
    except (FileNotFoundError, PermissionError):
        return NONE
    except subprocess.TimeoutExpired:
        logger.warning("cli_auth: %s auth probe timed out", cli_name)
        return NONE
    except Exception:
        logger.warning("cli_auth: %s auth probe failed", cli_name, exc_info=True)
        return NONE

    raw = result.stdout if stream == "stdout" else result.stderr
    mode = parser(result.returncode, (raw or b"").decode(errors="replace"))
    # Only the enum is logged. `codex login status` masks its own key, but
    # the raw line is still credential-adjacent and has no business in a
    # log file.
    logger.info("cli_auth: %s auth mode = %s", cli_name, mode)
    return mode


def env_without_api_keys(
    cli_name: str, base: Optional[Mapping[str, str]] = None
) -> dict[str, str]:
    """The child environment with this CLI's key variables removed.

    Passed to ``run_cli`` when the CLI is signed in via OAuth, so that
    "signed in with your subscription" is true by construction rather than
    by the CLI's internal precedence rules.

    **What this was measured to do, which is less than it was built for.**
    The original justification was that a stray ``ANTHROPIC_API_KEY`` in
    the launching shell would silently redirect billing to a pay-per-token
    account. That was asserted, then tested, and it did not hold on the
    versions here (2026-09-01): with a deliberately invalid
    ``ANTHROPIC_API_KEY`` exported, ``claude auth status`` still reported
    ``authMethod: claude.ai`` and a real dispatch still succeeded — which
    it could not have done on a bogus key. ``codex login status`` likewise
    ignored an exported ``OPENAI_API_KEY``. So on these versions a stored
    login already wins and this function changes nothing.

    It is kept anyway, for two reasons worth more than the ~20 lines:
    auth precedence is a CLI implementation detail rather than a published
    contract, and it has changed between versions before; and the
    requirement this build is held to is that OAuth is what runs, not that
    OAuth usually happens to run. What is NOT claimed is that removing it
    would cause a misroute today — it would not.

    Never applied when the CLI *is* signed in with a key: stripping the
    variable there could remove the only credential the user has.
    """
    env = dict(os.environ if base is None else base)
    for name in KEY_ENV_VARS.get(cli_name, ()):
        env.pop(name, None)
    return env
