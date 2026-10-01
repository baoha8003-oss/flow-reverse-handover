"""Running a CLI without freezing the agent or outliving its own timeout.

Two failures, both measured on this machine before the fix:

  * A Claude connection test took **38 seconds**, and because the provider
    called `subprocess.run` straight from a coroutine, every other request
    waited those 38 seconds. `GET /api/health` — a dict literal — timed out
    at 10s while a CLI dispatch was in flight.
  * A Codex dispatch with a 120-second timeout was still running past 300
    seconds. These CLIs are npm-installed, so on Windows the launched
    process is a `.cmd` shim that spawns `node`; killing the shim leaves
    node holding the stdout pipe, and the read never returns.

The first is why `run_cli` exists; the second is why it kills the tree.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time

import pytest

from flowboard.services.llm import cli_utils

#: A child that ignores the clock, to prove the timeout is enforced rather
#: than merely requested.
SLEEPER = "import time; time.sleep(60)"

#: A child that hands its OWN stdout to a grandchild and then exits.
#:
#: This is the npm `.cmd` shim's shape: the shim finishes quickly, node
#: keeps the pipe, and a reader waiting on that pipe never sees EOF. The
#: parent exiting is the important half — kill the shim and there is nothing
#: left to kill, yet the read still hangs.
SPAWNS_A_CHILD = (
    "import subprocess, sys; "
    "subprocess.Popen("
    "[sys.executable, '-c', 'import time; time.sleep(60)'], "
    "stdout=sys.stdout, stderr=sys.stderr)"
)


def test_a_normal_run_returns_output_and_exit_code():
    result = cli_utils.run_cli_sync(
        [sys.executable, "-c", "print('hello')"], timeout=30
    )
    assert result.returncode == 0
    assert b"hello" in result.stdout


def test_stdin_reaches_the_child():
    """The prompt travels this way because a Windows `.cmd` shim mangles
    newlines and quotes when they go through argv."""
    result = cli_utils.run_cli_sync(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
        stdin_data="line one\nline two".encode(),
        timeout=30,
    )
    # Windows translates \n to \r\n on the way out of the child's stdout;
    # what matters is that both lines arrived intact.
    assert result.stdout.decode().replace("\r\n", "\n") == "line one\nline two"


def test_a_nonzero_exit_is_reported_not_raised():
    """Same contract as `subprocess.run`: the caller decides what a failing
    exit code means."""
    result = cli_utils.run_cli_sync(
        [sys.executable, "-c", "import sys; sys.stderr.write('bad'); sys.exit(3)"],
        timeout=30,
    )
    assert result.returncode == 3
    assert b"bad" in result.stderr


# ── the timeout has to actually end the run ──────────────────────────────

def test_an_overrunning_child_is_killed_at_the_deadline():
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        cli_utils.run_cli_sync([sys.executable, "-c", SLEEPER], timeout=2)
    elapsed = time.monotonic() - started
    # Generous ceiling: the kill plus the drain, not the child's 60s.
    assert elapsed < 25, f"the timeout did not end the run (took {elapsed:.1f}s)"


def test_a_grandchild_holding_the_pipe_does_not_hold_the_run_open():
    """The production failure exactly: the npm `.cmd` shim hands its stdout
    to node and exits. Killing the shim leaves node holding the pipe, and
    the read never sees EOF — which is how a 120-second dispatch was still
    running past 300 seconds."""
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        cli_utils.run_cli_sync([sys.executable, "-c", SPAWNS_A_CHILD], timeout=2)
    elapsed = time.monotonic() - started
    assert elapsed < 25, (
        f"a surviving grandchild held the run open ({elapsed:.1f}s) — this is "
        "the bug that left a 120s dispatch running past 300s"
    )


def test_the_timeout_error_names_the_command():
    with pytest.raises(subprocess.TimeoutExpired) as exc:
        cli_utils.run_cli_sync([sys.executable, "-c", SLEEPER], timeout=1)
    assert sys.executable in list(exc.value.cmd)


# ── and it must not hold the event loop ──────────────────────────────────

def test_the_event_loop_keeps_running_during_a_dispatch():
    """The measurement that matters. While a CLI runs, other work has to
    keep being served — a 38-second ping froze the whole agent."""

    async def scenario():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.05)
                ticks += 1

        beat = asyncio.create_task(heartbeat())
        try:
            await cli_utils.run_cli(
                [sys.executable, "-c", "import time; time.sleep(1)"], timeout=30
            )
        finally:
            beat.cancel()
        return ticks

    ticks = asyncio.run(scenario())
    assert ticks > 5, (
        f"the loop advanced only {ticks} times during a 1s dispatch — it was "
        "blocked, which is what froze /api/health in production"
    )


def test_the_async_wrapper_returns_the_same_result():
    result = asyncio.run(
        cli_utils.run_cli([sys.executable, "-c", "print('via thread')"], timeout=30)
    )
    assert result.returncode == 0
    assert b"via thread" in result.stdout


def test_the_async_wrapper_propagates_the_timeout():
    with pytest.raises(subprocess.TimeoutExpired):
        asyncio.run(cli_utils.run_cli([sys.executable, "-c", SLEEPER], timeout=2))
