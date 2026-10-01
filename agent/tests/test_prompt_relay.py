"""Three models in a relay, each doing the part it is best at.

`prompt_ensemble` asks every provider the same question and throws two thirds
of the answers away. This splits the job instead: Claude plans, Codex writes,
the rules are checked for free, Claude verifies the meaning.

What these tests hold:

* **The free check runs before the paid one.** Mechanical faults cost
  nothing to find, and finding them first is the reason the relay is cheaper
  than it looks.
* **One revision, not a loop.** Each round is a real call; a second rewrite
  that still breaks a mechanical rule means the rule needs explaining
  better, not asking again.
* **A stage that fails degrades, never aborts.** Losing the planner should
  cost quality, not the prompt.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from flowboard.services import prompt_relay
from flowboard.services.llm.base import LLMError

BEAT = {
    "subject": "một người mẫu nữ, tóc đen dài, áo dài trắng",
    "action": "bước tới gần gương",
    "camera": "locked",
    "setting": "phòng khách buổi sáng",
    "must_include": ["gương", "áo dài trắng"],
}
GOOD = (
    "@@Mai walks toward a tall mirror in a bright morning living room, "
    "white ao dai, hair down, camera locked.\n"
    "Lời thoại: Con về rồi đây mẹ."
)


class _Script:
    """Canned answers per feature, in call order, recording what was asked."""

    def __init__(self, **by_feature):
        self.by_feature = {k: list(v) for k, v in by_feature.items()}
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, feature, prompt, *, system_prompt="", **kw):
        self.calls.append((feature, prompt))
        queue = self.by_feature.get(feature)
        if not queue:
            raise LLMError(f"no provider for {feature}")
        answer = queue.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer, {"canvas_agent": "claude", "script_writer": "openai"}.get(
            feature, "claude"
        )

    def count(self, feature: str) -> int:
        return sum(1 for f, _ in self.calls if f == feature)


def _install(monkeypatch, script: _Script) -> None:
    import flowboard.services.llm as llm_module

    monkeypatch.setattr(llm_module, "run_llm_chain", script)


def _ok_verdict() -> str:
    return json.dumps({"ok": True, "faults": []})


# ── the happy path ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_relay_produces_a_prompt(monkeypatch):
    script = _Script(
        canvas_agent=[json.dumps(BEAT), _ok_verdict()],
        script_writer=[GOOD],
    )
    _install(monkeypatch, script)
    out = await prompt_relay.compose("cô gái soi gương", cast=["Mai"], lane="lite_relaxed", seconds=8)
    assert out.prompt == GOOD
    assert out.clean
    assert out.revisions == 0


@pytest.mark.asyncio
async def test_each_stage_is_kept(monkeypatch):
    """A prompt whose reasoning you cannot see is one you cannot argue
    with — the same reason the ensemble keeps its losing drafts."""
    script = _Script(canvas_agent=[json.dumps(BEAT), _ok_verdict()], script_writer=[GOOD])
    _install(monkeypatch, script)
    out = await prompt_relay.compose("x", cast=["Mai"])
    assert [s.name for s in out.stages] == ["planner", "writer", "verifier"]
    assert out.beat_sheet["action"] == "bước tới gần gương"


@pytest.mark.asyncio
async def test_the_writer_receives_the_beat_sheet(monkeypatch):
    script = _Script(canvas_agent=[json.dumps(BEAT), _ok_verdict()], script_writer=[GOOD])
    _install(monkeypatch, script)
    await prompt_relay.compose("cô gái soi gương", cast=["Mai"])
    writer_ask = next(p for f, p in script.calls if f == "script_writer")
    assert "bước tới gần gương" in writer_ask, "the plan did not reach the writer"
    assert "@@Mai" in writer_ask, "the cast did not reach the writer"


@pytest.mark.asyncio
async def test_the_lane_ceiling_reaches_the_writer(monkeypatch):
    script = _Script(canvas_agent=[json.dumps(BEAT), _ok_verdict()], script_writer=[GOOD])
    _install(monkeypatch, script)
    await prompt_relay.compose("x", cast=["Mai"], lane="lite_relaxed", seconds=8)
    writer_ask = next(p for f, p in script.calls if f == "script_writer")
    assert "3 nhân vật" in writer_ask and "8 giây" in writer_ask


# ── the free check, and the one revision ──────────────────────────────


@pytest.mark.asyncio
async def test_a_mechanical_fault_triggers_exactly_one_rewrite(monkeypatch):
    """A tag inside spoken text is read aloud. It costs nothing to catch,
    and the writer gets told once."""
    bad = '@@Mai walks in.\nLời thoại: Chào @@Lan nhé.'
    script = _Script(
        canvas_agent=[json.dumps(BEAT), _ok_verdict(), _ok_verdict()],
        script_writer=[bad, GOOD],
    )
    _install(monkeypatch, script)
    out = await prompt_relay.compose("x", cast=["Mai", "Lan"])
    assert out.revisions == 1
    assert out.prompt == GOOD
    assert out.clean


@pytest.mark.asyncio
async def test_the_rewrite_is_told_what_was_wrong(monkeypatch):
    bad = 'Lời thoại: Chào @@Lan nhé.'
    script = _Script(
        canvas_agent=[json.dumps(BEAT), _ok_verdict(), _ok_verdict()],
        script_writer=[bad, GOOD],
    )
    _install(monkeypatch, script)
    await prompt_relay.compose("x", cast=["Mai", "Lan"])
    rewrite_ask = [p for f, p in script.calls if f == "script_writer"][1]
    assert "LỖI CẦN SỬA" in rewrite_ask
    assert "lời thoại" in rewrite_ask.lower()


@pytest.mark.asyncio
async def test_it_stops_after_one_revision(monkeypatch):
    """Each round is a real call. A second rewrite that still breaks a
    mechanical rule means the rule needs explaining better, not asking
    again — so the loop ends and the caller is told what survived."""
    bad = 'Lời thoại: Chào @@Lan nhé.'
    script = _Script(
        canvas_agent=[json.dumps(BEAT), _ok_verdict(), _ok_verdict()],
        script_writer=[bad, bad],
    )
    _install(monkeypatch, script)
    out = await prompt_relay.compose("x", cast=["Mai", "Lan"])
    assert out.revisions == 1
    assert script.count("script_writer") == 2, "more than one rewrite"
    assert not out.clean, "surviving faults must be visible, not swallowed"


@pytest.mark.asyncio
async def test_a_semantic_fault_also_triggers_a_rewrite(monkeypatch):
    """The verifier judges only what a regular expression cannot: whether
    the prompt still describes what was asked for."""
    reject = json.dumps({"ok": False, "faults": ["mất chiếc gương"]})
    script = _Script(
        canvas_agent=[json.dumps(BEAT), reject, _ok_verdict()],
        script_writer=[GOOD, GOOD],
    )
    _install(monkeypatch, script)
    out = await prompt_relay.compose("x", cast=["Mai"])
    assert out.revisions == 1
    rewrite_ask = [p for f, p in script.calls if f == "script_writer"][1]
    assert "mất chiếc gương" in rewrite_ask


# ── degrading rather than aborting ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_dead_planner_still_yields_a_prompt(monkeypatch):
    """Losing the planner should cost quality, not the prompt."""
    script = _Script(canvas_agent=[], script_writer=[GOOD])
    _install(monkeypatch, script)
    out = await prompt_relay.compose("cô gái soi gương", cast=["Mai"])
    assert out.prompt == GOOD
    planner = next(s for s in out.stages if s.name == "planner")
    assert planner.error, "the failure has to be recorded, not hidden"


@pytest.mark.asyncio
async def test_the_verifier_is_skipped_without_a_beat_sheet(monkeypatch):
    """A verifier with no reference invents faults, and an invented fault
    costs a rewrite."""
    script = _Script(canvas_agent=[], script_writer=[GOOD])
    _install(monkeypatch, script)
    out = await prompt_relay.compose("x", cast=["Mai"])
    assert out.revisions == 0
    verifier = next(s for s in out.stages if s.name == "verifier")
    assert "no beat sheet" in verifier.error


@pytest.mark.asyncio
async def test_a_dead_verifier_does_not_block_the_prompt(monkeypatch):
    """The mechanical checks already ran, and they are the ones with teeth."""
    script = _Script(canvas_agent=[json.dumps(BEAT)], script_writer=[GOOD])
    _install(monkeypatch, script)
    out = await prompt_relay.compose("x", cast=["Mai"])
    assert out.prompt == GOOD and out.revisions == 0


@pytest.mark.asyncio
async def test_a_dead_writer_is_the_one_failure_that_stops_it(monkeypatch):
    """Nothing downstream can substitute for the stage that produces the
    artefact."""
    script = _Script(canvas_agent=[json.dumps(BEAT)], script_writer=[])
    _install(monkeypatch, script)
    with pytest.raises(LLMError, match="writer could not answer"):
        await prompt_relay.compose("x", cast=["Mai"])


@pytest.mark.asyncio
async def test_a_non_json_beat_sheet_degrades(monkeypatch):
    script = _Script(canvas_agent=["not json at all"], script_writer=[GOOD])
    _install(monkeypatch, script)
    out = await prompt_relay.compose("x", cast=["Mai"])
    assert out.prompt == GOOD
    assert "not JSON" in next(s for s in out.stages if s.name == "planner").error


@pytest.mark.asyncio
async def test_an_empty_brief_raises(monkeypatch):
    _install(monkeypatch, _Script())
    with pytest.raises(LLMError, match="no brief"):
        await prompt_relay.compose("   ")


@pytest.mark.asyncio
async def test_fenced_json_is_still_read(monkeypatch):
    """Models wrap JSON in fences even when told not to."""
    script = _Script(
        canvas_agent=["```json\n" + json.dumps(BEAT) + "\n```", _ok_verdict()],
        script_writer=[GOOD],
    )
    _install(monkeypatch, script)
    out = await prompt_relay.compose("x", cast=["Mai"])
    assert out.beat_sheet["camera"] == "locked"


# ── who does what ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_planning_and_writing_go_to_different_chains(monkeypatch):
    """The point of the relay: the planning stage and the writing stage are
    routed separately, so each can prefer the model suited to it."""
    script = _Script(canvas_agent=[json.dumps(BEAT), _ok_verdict()], script_writer=[GOOD])
    _install(monkeypatch, script)
    await prompt_relay.compose("x", cast=["Mai"])
    features = [f for f, _ in script.calls]
    assert features == ["canvas_agent", "script_writer", "canvas_agent"]


# ── the HTTP surface ──────────────────────────────────────────────────


def test_the_route_returns_every_stage(client, monkeypatch):
    script = _Script(canvas_agent=[json.dumps(BEAT), _ok_verdict()], script_writer=[GOOD])
    _install(monkeypatch, script)
    body = client.post(
        "/api/prompt/relay",
        json={"brief": "cô gái soi gương", "cast": ["Mai"],
              "lane": "lite_relaxed", "seconds": 8},
    ).json()
    assert body["prompt"] == GOOD
    assert body["clean"] is True
    assert [s["name"] for s in body["stages"]] == ["planner", "writer", "verifier"]
    assert body["beatSheet"]["camera"] == "locked"


def test_the_route_reports_surviving_faults(client, monkeypatch):
    """A fault that outlived the revision round has to reach the caller —
    `clean: false` plus the finding, not a prompt that looks fine."""
    bad = 'Lời thoại: Chào @@Lan nhé.'
    script = _Script(
        canvas_agent=[json.dumps(BEAT), _ok_verdict(), _ok_verdict()],
        script_writer=[bad, bad],
    )
    _install(monkeypatch, script)
    body = client.post(
        "/api/prompt/relay",
        json={"brief": "x", "cast": ["Mai", "Lan"]},
    ).json()
    assert body["clean"] is False
    assert any(f["rule"] == "tag_in_dialogue" for f in body["findings"])


def test_the_route_surfaces_a_dead_writer_as_502(client, monkeypatch):
    _install(monkeypatch, _Script(canvas_agent=[json.dumps(BEAT)], script_writer=[]))
    resp = client.post("/api/prompt/relay", json={"brief": "x"})
    assert resp.status_code == 502


# ── the verdict the caller reads has to include the verifier's ────────


def _rejecting_verdict() -> str:
    return json.dumps({"ok": False, "faults": ["mất cái gương trong beat sheet"]})


def test_a_verifier_rejection_survives_into_the_result(monkeypatch):
    """The mechanical findings were the only thing reported, so a relay whose
    rewrite the verifier threw out came back `clean=True` with an empty list.
    That is the one answer that stops a caller from looking — and the caller
    is about to spend money on the prompt."""
    script = _Script(
        canvas_agent=[json.dumps(BEAT), _rejecting_verdict(), _rejecting_verdict()],
        script_writer=[GOOD, GOOD],
    )
    _install(monkeypatch, script)
    result = asyncio.run(prompt_relay.compose("cô gái soi gương", cast=["Mai"]))

    assert result.clean is False
    assert [f.rule for f in result.findings] == ["semantic"]
    assert "gương" in result.findings[0].message


def test_a_verifier_that_accepts_leaves_the_result_clean(monkeypatch):
    script = _Script(
        canvas_agent=[json.dumps(BEAT), _ok_verdict()], script_writer=[GOOD]
    )
    _install(monkeypatch, script)
    result = asyncio.run(prompt_relay.compose("cô gái soi gương", cast=["Mai"]))
    assert result.clean is True and result.findings == []


def test_a_verifier_that_cannot_answer_does_not_fail_the_prompt(monkeypatch):
    """A dead verifier must not invent a fault: the mechanical checks already
    ran and they are the half with teeth."""
    script = _Script(canvas_agent=[json.dumps(BEAT)], script_writer=[GOOD])
    _install(monkeypatch, script)
    result = asyncio.run(prompt_relay.compose("x", cast=["Mai"]))
    assert result.clean is True
    assert result.prompt == GOOD
