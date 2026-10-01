"""Which identity a CLI provider is signed in with.

The bug this guards against is not subtle once seen, but it hid for a whole
debugging session. Codex was signed in with an API key that OpenAI rejects
(`invalid_api_key` on `/v1/models`), and every visible signal said it was
fine: `codex --version` answered, so `is_available()` was True, so the card
read "Connected", and the card's second line said "OAuth" because that line
was a hardcoded string. The only symptom was the CLI's own
`ERROR: Reconnecting... 1/5`, which points at the network. The network was
fine.

So these tests pin two things: that the identity is read from each CLI's
own surface rather than assumed, and that an OAuth dispatch is handed an
environment with no API key in it. See the env-scrubbing section for what
that second one does and does not establish — the claim it was built on
turned out to be false, and saying so is cheaper than leaving a comment
that lies.
"""
from __future__ import annotations

import subprocess

import pytest

from flowboard.services.llm import cli_auth
from flowboard.services.llm.cli_auth import (
    APIKEY,
    NONE,
    OAUTH,
    env_without_api_keys,
    parse_claude_auth,
    parse_codex_auth,
)


# ── claude: `claude auth status --json` ───────────────────────────────

# Captured verbatim from claude-code on 2026-09-01. Kept whole rather than
# trimmed to the two fields the parser reads, so that a future change to
# the payload's shape shows up here as a real difference.
CLAUDE_OAUTH_JSON = """{
  "loggedIn": true,
  "authMethod": "claude.ai",
  "apiProvider": "firstParty",
  "email": "someone@example.com",
  "orgId": "f71c7a9c-0000-0000-0000-ecec5f146e90",
  "orgName": "Example Org",
  "subscriptionType": "max"
}"""


def test_claude_subscription_login_reads_as_oauth():
    assert parse_claude_auth(0, CLAUDE_OAUTH_JSON) == OAUTH


def test_claude_api_key_login_is_not_reported_as_oauth():
    """The distinction the whole module exists for: signed in either way
    looks identical to `--version`, and only `authMethod` separates them."""
    payload = '{"loggedIn": true, "authMethod": "apiKey"}'
    assert parse_claude_auth(0, payload) == APIKEY


def test_claude_signed_out_is_none():
    assert parse_claude_auth(0, '{"loggedIn": false}') == NONE


def test_claude_non_zero_exit_is_none():
    assert parse_claude_auth(1, CLAUDE_OAUTH_JSON) == NONE


@pytest.mark.parametrize("junk", ["", "not json", "null", "[]", "   "])
def test_claude_unparseable_output_is_none(junk):
    """`none` rather than a guess: an unreadable answer is not evidence of
    a subscription, and treating it as one would re-create the exact
    false-confidence this module removes."""
    assert parse_claude_auth(0, junk) == NONE


# ── codex: `codex login status` ───────────────────────────────────────

# Verbatim from codex-cli 0.147.0 on 2026-09-01, key fragment altered.
# This is the ONE codex wording that was actually observed, which is why
# the parser identifies it positively and infers the rest.
CODEX_APIKEY_LINE = "Logged in using an API key - sk-493b2***5564a\n"


def test_codex_api_key_login_is_detected():
    assert parse_codex_auth(0, CODEX_APIKEY_LINE) == APIKEY


def test_codex_non_zero_exit_is_none():
    assert parse_codex_auth(1, "") == NONE


def test_codex_empty_output_is_none():
    assert parse_codex_auth(0, "   \n") == NONE


@pytest.mark.parametrize(
    "line",
    ["Not logged in", "You are logged out.", "not  logged  in"],
)
def test_codex_signed_out_wording_is_none(line):
    assert parse_codex_auth(0, line) == NONE


#: Verbatim from codex-cli 0.147.0 after a real `codex login`, captured
#: 2026-09-01. This replaces an inferred string: the parser was written
#: before any ChatGPT login existed on this machine, so it identified the
#: API-key wording positively and treated everything else as OAuth rather
#: than pinning a test to a guessed sentence. The guess happened to be
#: right, which is luck, not method — this is the measured one.
CODEX_OAUTH_LINE = "Logged in using ChatGPT\n"


def test_codex_chatgpt_login_is_detected():
    assert parse_codex_auth(0, CODEX_OAUTH_LINE) == OAUTH


@pytest.mark.parametrize(
    "line",
    [
        "Logged in as someone@example.com (Plus)",
        "Authenticated with your ChatGPT account",
    ],
)
def test_other_signed_in_wordings_also_read_as_oauth(line):
    """Still inferred, and kept that way on purpose: the parser identifies
    the API-key wording positively and treats signed-in-but-not-that as
    OAuth, so a future rewording of the ChatGPT line degrades to the right
    answer instead of to "not signed in"."""
    assert parse_codex_auth(0, line) == OAUTH


def test_api_key_wording_wins_over_a_signed_in_line():
    """`Logged in using an API key` contains both signals. If the OAuth
    branch were checked first, every key login would report as OAuth."""
    assert parse_codex_auth(0, "Logged in using an API key - sk-x") == APIKEY


# ── the streams the two CLIs answer on ────────────────────────────────


class _Result:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.mark.asyncio
async def test_codex_answer_is_read_from_stderr(monkeypatch):
    """Measured, and it cost a debugging round to find: `codex login
    status` writes its line to STDERR and leaves stdout empty, while
    `claude auth status --json` does the opposite. Reading stdout for both
    reports every codex login as "not signed in"."""
    monkeypatch.setattr(cli_auth, "resolve_cli_binary", lambda *a, **k: "codex")

    async def fake_run(args, **kwargs):
        return _Result(0, stdout=b"", stderr=CODEX_APIKEY_LINE.encode())

    monkeypatch.setattr(cli_auth, "run_cli", fake_run)
    assert await cli_auth.detect_auth_mode("codex") == APIKEY


@pytest.mark.asyncio
async def test_claude_answer_is_read_from_stdout(monkeypatch):
    monkeypatch.setattr(cli_auth, "resolve_cli_binary", lambda *a, **k: "claude")

    async def fake_run(args, **kwargs):
        return _Result(0, stdout=CLAUDE_OAUTH_JSON.encode(), stderr=b"")

    monkeypatch.setattr(cli_auth, "run_cli", fake_run)
    assert await cli_auth.detect_auth_mode("claude") == OAUTH


@pytest.mark.asyncio
async def test_the_probe_uses_the_documented_subcommand(monkeypatch):
    """Pins the argv. `codex login status` is what reports identity;
    `codex --version` answers happily while signed out, which is how the
    original bug stayed invisible."""
    seen: list[list[str]] = []
    monkeypatch.setattr(cli_auth, "resolve_cli_binary", lambda *a, **k: "codex")

    async def fake_run(args, **kwargs):
        seen.append(list(args))
        return _Result(0, stderr=CODEX_APIKEY_LINE.encode())

    monkeypatch.setattr(cli_auth, "run_cli", fake_run)
    await cli_auth.detect_auth_mode("codex")
    assert seen == [["codex", "login", "status"]]


@pytest.mark.asyncio
async def test_a_probe_failure_never_propagates(monkeypatch):
    """A settings poll must not 500 because a CLI misbehaved."""
    monkeypatch.setattr(cli_auth, "resolve_cli_binary", lambda *a, **k: "codex")

    async def boom(args, **kwargs):
        raise subprocess.TimeoutExpired(["codex"], 20.0)

    monkeypatch.setattr(cli_auth, "run_cli", boom)
    assert await cli_auth.detect_auth_mode("codex") == NONE


@pytest.mark.asyncio
async def test_an_unknown_cli_is_none(monkeypatch):
    assert await cli_auth.detect_auth_mode("nothing-like-this") == NONE


# ── env scrubbing ─────────────────────────────────────────────────────


def test_an_env_key_cannot_override_a_claude_oauth_login():
    """The mapping handed to an OAuth dispatch carries no key variables.

    Scope note, because the original justification for this did not
    survive contact with the CLIs. It claimed a stray `ANTHROPIC_API_KEY`
    would outrank the stored subscription login; measured on 2026-09-01,
    it does not — `claude auth status` still reported `claude.ai` with a
    bogus key exported, and a live dispatch still succeeded, which a bogus
    key could not have done. Same for `codex login status` and
    `OPENAI_API_KEY`. So this pins the mapping and the wiring, and makes
    no claim about what the CLIs would do without it."""
    env = env_without_api_keys(
        "claude",
        {"ANTHROPIC_API_KEY": "sk-x", "ANTHROPIC_AUTH_TOKEN": "t", "PATH": "/bin"},
    )
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert env["PATH"] == "/bin"


def test_an_env_key_cannot_override_a_codex_oauth_login():
    env = env_without_api_keys("codex", {"OPENAI_API_KEY": "sk-x", "PATH": "/bin"})
    assert "OPENAI_API_KEY" not in env
    assert env["PATH"] == "/bin"


def test_scrubbing_one_cli_leaves_the_other_cli_alone():
    """Each CLI owns its own variables. Scrubbing everything would break a
    user who signs into one with OAuth and the other with a key."""
    env = env_without_api_keys(
        "codex", {"OPENAI_API_KEY": "a", "ANTHROPIC_API_KEY": "b"}
    )
    assert "OPENAI_API_KEY" not in env
    assert env["ANTHROPIC_API_KEY"] == "b"


def test_scrubbing_does_not_mutate_the_environment_it_was_given():
    base = {"OPENAI_API_KEY": "sk-x"}
    env_without_api_keys("codex", base)
    assert base == {"OPENAI_API_KEY": "sk-x"}


def test_an_absent_variable_is_not_an_error():
    assert env_without_api_keys("codex", {"PATH": "/bin"}) == {"PATH": "/bin"}


# ── the wiring: does a DISPATCH actually use the scrubbed env? ─────────
#
# The tests above prove `env_without_api_keys` computes the right mapping.
# They said nothing about whether either provider passes it, and that gap
# was real: deleting the `env=` argument from both dispatch sites left the
# whole suite green at 1114 passed. A helper nobody calls protects nobody,
# so these cross the boundary instead of stopping at it.


@pytest.mark.asyncio
async def test_a_claude_dispatch_on_oauth_strips_the_key_from_the_child(
    monkeypatch,
):
    from flowboard.services import claude_cli

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-reach-the-cli")
    monkeypatch.setattr(claude_cli, "_auth_mode", OAUTH)
    monkeypatch.setattr(claude_cli, "resolve_cli_binary", lambda *a, **k: "claude")

    seen: dict = {}

    async def fake_run(args, *, stdin_data=b"", timeout=None, env=None):
        seen["env"] = env
        return _Result(0, stdout=b'{"result": "ok"}')

    monkeypatch.setattr(claude_cli, "run_cli", fake_run)
    await claude_cli.run_claude("hi")

    assert seen["env"] is not None, "dispatch inherited the environment wholesale"
    assert "ANTHROPIC_API_KEY" not in seen["env"]


@pytest.mark.asyncio
async def test_a_claude_dispatch_on_an_api_key_login_leaves_the_env_alone(
    monkeypatch,
):
    """The other half of the rule. Scrubbing unconditionally would delete
    the only credential a key-only user has."""
    from flowboard.services import claude_cli

    monkeypatch.setattr(claude_cli, "_auth_mode", APIKEY)
    monkeypatch.setattr(claude_cli, "resolve_cli_binary", lambda *a, **k: "claude")

    seen: dict = {}

    async def fake_run(args, *, stdin_data=b"", timeout=None, env=None):
        seen["env"] = env
        return _Result(0, stdout=b'{"result": "ok"}')

    monkeypatch.setattr(claude_cli, "run_cli", fake_run)
    await claude_cli.run_claude("hi")

    assert seen["env"] is None


@pytest.mark.asyncio
async def test_a_codex_dispatch_on_oauth_strips_the_key_from_the_child(
    monkeypatch, tmp_path
):
    from flowboard.services.llm import openai as mod

    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-reach-the-cli")
    provider = mod.OpenAIProvider()
    provider._auth_mode = OAUTH
    monkeypatch.setattr(mod, "resolve_cli_binary", lambda *a, **k: "codex")

    seen: dict = {}

    async def fake_run(args, *, stdin_data=b"", timeout=None, env=None):
        seen["env"] = env
        out = args[args.index("--output-last-message") + 1]
        __import__("pathlib").Path(out).write_text("ok", encoding="utf-8")
        return _Result(0)

    monkeypatch.setattr(mod, "run_cli", fake_run)
    await provider._run_cli("hi", None, None, 30.0)

    assert seen["env"] is not None, "dispatch inherited the environment wholesale"
    assert "OPENAI_API_KEY" not in seen["env"]


@pytest.mark.asyncio
async def test_a_codex_dispatch_on_an_api_key_login_leaves_the_env_alone(
    monkeypatch,
):
    from flowboard.services.llm import openai as mod

    provider = mod.OpenAIProvider()
    provider._auth_mode = APIKEY
    monkeypatch.setattr(mod, "resolve_cli_binary", lambda *a, **k: "codex")

    seen: dict = {}

    async def fake_run(args, *, stdin_data=b"", timeout=None, env=None):
        seen["env"] = env
        out = args[args.index("--output-last-message") + 1]
        __import__("pathlib").Path(out).write_text("ok", encoding="utf-8")
        return _Result(0)

    monkeypatch.setattr(mod, "run_cli", fake_run)
    await provider._run_cli("hi", None, None, 30.0)

    assert seen["env"] is None


# ── the cold-start race ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_concurrent_polls_during_a_cold_probe_agree(monkeypatch):
    """Observed live, not imagined: the first `/api/llm/providers` after an
    agent restart reported OpenAI as `available: false, mode: none`, the
    next reported `cli`. The probe marked itself done before its first
    await, so a poll arriving mid-probe skipped the wait and read fields
    nobody had filled in yet — which renders as "Setup needed" for a
    provider that is fine.
    """
    import asyncio

    from flowboard.services.llm import openai as mod

    provider = mod.OpenAIProvider()
    monkeypatch.setattr(mod, "resolve_cli_binary", lambda *a, **k: "codex")
    monkeypatch.setattr(
        "flowboard.services.llm.cli_auth.resolve_cli_binary", lambda *a, **k: "codex"
    )

    async def slow_run(args, **kwargs):
        # A real probe is three subprocesses; the yield is what lets a
        # second caller in, exactly as the event loop does under load.
        await asyncio.sleep(0.01)
        argv = list(args)
        if "--version" in argv:
            return _Result(0, stdout=b"codex 1.0\n")
        if argv[-2:] == ["login", "status"]:
            return _Result(0, stderr=CODEX_APIKEY_LINE.encode())
        return _Result(0, stdout=b"  --image PATH\n")

    monkeypatch.setattr(mod, "run_cli", slow_run)
    monkeypatch.setattr("flowboard.services.llm.cli_auth.run_cli", slow_run)

    first, second = await asyncio.gather(
        provider.is_available(), provider.is_available()
    )
    assert first is True and second is True, (
        "a poll that arrived during the probe saw an unprobed provider"
    )
    assert provider.mode == "cli"


# ── the failure message that sent the last diagnosis the wrong way ────


def test_a_failed_dispatch_on_an_api_key_login_names_auth():
    """`ERROR: Reconnecting... 1/5` points at the network. When the real
    cause is a rejected key, the reader needs the identity beside it."""
    from flowboard.services.llm.openai import _auth_hint

    hint = _auth_hint(APIKEY)
    assert "codex login" in hint
    assert "API key" in hint


def test_a_failed_dispatch_on_oauth_adds_no_auth_noise():
    """An OAuth login failing is not an auth problem, and saying so would
    send the next reader down the same wrong path in reverse."""
    from flowboard.services.llm.openai import _auth_hint

    assert _auth_hint(OAUTH) == ""
