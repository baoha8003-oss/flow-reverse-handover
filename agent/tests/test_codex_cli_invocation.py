"""The codex CLI call, pinned against the CLI that is actually installed.

Every dispatch through the OpenAI/Codex provider failed at argument parsing:

    codex CLI exited 2: error: unexpected argument '--output-format' found
      tip: a similar argument exists: '--output-schema'

Measured against codex-cli 0.147.0, three things were wrong at once —
`--output-format` does not exist, `-p` means `--profile` rather than
"prompt", and there is no `--system` flag at all. Each is pinned below,
because a flag that has been renamed once will be renamed again.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from flowboard.services.llm import openai as mod
from flowboard.services.llm.base import LLMError


class _Spawn:
    """Captures the argv and stdin a run would have used, and answers with a
    canned result — so the invocation is checked without a real CLI.

    Patched at `run_cli`, the seam the provider actually goes through. That
    seam is also what keeps the event loop free and makes the timeout kill
    the CLI's child process, so bypassing it in a test would leave both
    unexercised."""

    def __init__(self, *, returncode=0, stderr=b"", answer="OK", write=True):
        self.returncode = returncode
        self.stderr = stderr
        self.answer = answer
        self.write = write
        self.args: list[str] = []
        self.stdin: str = ""
        self.env: dict | None = None

    async def __call__(self, args, *, stdin_data=b"", timeout=None, env=None):
        self.args = list(args)
        self.stdin = (stdin_data or b"").decode("utf-8")
        self.env = env
        if self.write:
            # The CLI's contract for `--output-last-message`: it writes the
            # final message to the named file.
            out = args[args.index("--output-last-message") + 1]
            Path(out).write_text(self.answer, encoding="utf-8")
        return subprocess.CompletedProcess(args, self.returncode, b"", self.stderr)


@pytest.fixture
def spawn(monkeypatch):
    def install(**kwargs):
        s = _Spawn(**kwargs)
        monkeypatch.setattr(mod, "run_cli", s)
        monkeypatch.setattr(mod, "resolve_cli_binary", lambda *a, **k: "codex")
        return s

    return install


def run_cli(provider=None, **kwargs):
    import asyncio

    provider = provider or mod.OpenAIProvider()
    return asyncio.run(
        provider._run_cli(
            kwargs.pop("user_prompt", "hi"),
            kwargs.pop("system_prompt", None),
            kwargs.pop("attachments", None),
            kwargs.pop("timeout", 30.0),
        )
    )


# ── the three flags that were wrong ──────────────────────────────────────

def test_the_nonexistent_output_format_flag_is_gone(spawn):
    s = spawn()
    run_cli()
    assert "--output-format" not in s.args, "the flag that made every call fail"


def test_dash_p_is_not_used_as_a_prompt_flag(spawn):
    """In this CLI `-p` is `--profile`. Passing the prompt after it sent the
    prompt where a profile name belongs."""
    s = spawn()
    run_cli()
    assert "-p" not in s.args
    assert "--profile" not in s.args


def test_no_system_flag_is_passed_because_none_exists(spawn):
    s = spawn()
    run_cli(system_prompt="be terse")
    assert "--system" not in s.args


def test_the_system_prompt_is_folded_into_the_text_instead(spawn):
    """It has to reach the model somehow. Dropping it would silently change
    the answer."""
    s = spawn()
    run_cli(user_prompt="what colour", system_prompt="be terse")
    assert "be terse" in s.stdin
    assert "what colour" in s.stdin
    assert s.stdin.index("be terse") < s.stdin.index("what colour")


# ── the invocation that replaced them ────────────────────────────────────

def test_the_prompt_goes_over_stdin(spawn):
    """A `.cmd` shim on Windows re-parses argv and mangles newlines and
    quotes in a long prompt."""
    s = spawn()
    run_cli(user_prompt="line one\nline two")
    assert s.args[-1] == "-", "the stdin sentinel must be the final argument"
    assert s.stdin == "line one\nline two"


def test_the_answer_is_read_from_the_output_file(spawn):
    s = spawn(answer="  OK  ")
    assert run_cli() == "OK"
    assert "--output-last-message" in s.args


def test_the_run_is_confined_to_read_only(spawn):
    """A text round-trip has no business running commands on the machine."""
    s = spawn()
    run_cli()
    assert s.args[s.args.index("--sandbox") + 1] == "read-only"


def test_it_does_not_refuse_to_start_outside_a_git_repo(spawn):
    s = spawn()
    run_cli()
    assert "--skip-git-repo-check" in s.args


def test_each_call_gets_its_own_output_file(spawn):
    """Two concurrent dispatches sharing one path would race, and the loser
    would return the other's answer."""
    first = spawn(answer="one")
    run_cli()
    path_a = first.args[first.args.index("--output-last-message") + 1]
    second = spawn(answer="two")
    run_cli()
    path_b = second.args[second.args.index("--output-last-message") + 1]
    assert path_a != path_b


def test_the_output_file_is_cleaned_up(spawn):
    s = spawn()
    run_cli()
    out = s.args[s.args.index("--output-last-message") + 1]
    assert not Path(out).exists(), "a temp file was left behind"


def test_attachments_are_passed_with_the_image_flag(spawn, tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x00")
    provider = mod.OpenAIProvider()
    provider._cli_image_flag = "--image"
    s = spawn()
    run_cli(provider=provider, attachments=[str(img)])
    assert "--image" in s.args


# ── failures must say what actually happened ─────────────────────────────

def test_a_connectivity_failure_is_reported_verbatim(spawn):
    """The real reason this provider does not work on this machine reads
    `ERROR: Reconnecting... 1/5`. The user can act on that; they cannot act
    on a wrapper's paraphrase."""
    spawn(
        returncode=1,
        stderr=b"warning: Ignoring malformed agent role definition: x.toml\n"
               b"ERROR: Reconnecting... 1/5\nERROR: Reconnecting... 2/5\n",
        write=False,
    )
    with pytest.raises(LLMError) as exc:
        run_cli()
    assert "Reconnecting" in str(exc.value)


def test_the_agent_role_warnings_do_not_bury_the_error(spawn):
    """The CLI prefixes every run with a wall of warnings about the user's
    own `~/.codex/agents/*.toml`. Taking the first 400 characters showed
    only those and cut the real error off the bottom."""
    noise = b"".join(
        b"warning: Ignoring malformed agent role definition: agent role file at "
        b"C:\\Users\\x\\.codex\\agents\\role%d.toml must define instructions\n" % i
        for i in range(12)
    )
    spawn(returncode=1, stderr=noise + b"ERROR: the real problem\n", write=False)
    with pytest.raises(LLMError) as exc:
        run_cli()
    assert "the real problem" in str(exc.value)


def test_an_empty_answer_is_a_failure_not_a_result(spawn):
    """Exit 0 with nothing written means the run ended without the model
    saying anything. Returning "" would hand the caller a blank prompt as
    if it were an answer."""
    spawn(answer="   ")
    with pytest.raises(LLMError) as exc:
        run_cli()
    assert "empty" in str(exc.value).lower()


def test_a_missing_output_file_is_a_failure(spawn):
    spawn(write=False)
    with pytest.raises(LLMError):
        run_cli()


def test_a_timeout_names_the_budget(spawn, monkeypatch):
    async def boom(*a, **k):
        raise subprocess.TimeoutExpired("codex", 30.0)

    monkeypatch.setattr(mod, "resolve_cli_binary", lambda *a, **k: "codex")
    monkeypatch.setattr(mod, "run_cli", boom)
    with pytest.raises(LLMError) as exc:
        run_cli(timeout=30.0)
    assert "30" in str(exc.value)


# ── the stderr picker on its own ─────────────────────────────────────────

def test_warnings_survive_when_there_is_nothing_else():
    """Better to show the warnings than to show nothing at all."""
    out = mod._last_meaningful_line(b"warning: a\nwarning: b\n")
    assert "warning: a" in out


def test_no_stderr_at_all_still_produces_a_message():
    assert mod._last_meaningful_line(b"") == "no output"
