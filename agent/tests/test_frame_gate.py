"""Checking the still before spending a video credit on it.

Image-to-video starts from a frame. A clip built from the wrong frame is
wrong too, and the clip is the expensive half — so the rejection is worth
moving one step earlier.

**It does not regenerate the image.** Images are billable as well
(`estimate.BILLABLE_TYPES` is `{"image", "video"}`), so an automatic retry
would spend credits nobody approved, which is the rule the review loop
follows too. The gate stops the video dispatch and hands the decision back;
that already saves the expensive credit.
"""
from __future__ import annotations

import json

import pytest

from flowboard.services import frame_gate
from flowboard.services.frame_gate import GateError, parse_verdict


def _verdict(ok=True, score=8.0, reasons=None, fix="") -> str:
    return json.dumps({
        "ok": ok, "score": score,
        "reasons": reasons if reasons is not None else ["khớp brief"],
        "fix": fix,
    })


# ── reading the answer ────────────────────────────────────────────────


def test_a_pass_parses():
    got = parse_verdict(_verdict())
    assert got.ok and got.score == 8.0 and got.reasons == ["khớp brief"]


def test_a_rejection_carries_its_reasons():
    got = parse_verdict(_verdict(ok=False, score=3.0, reasons=["sai nhân vật", "mờ"]))
    assert not got.ok
    assert got.reasons == ["sai nhân vật", "mờ"]


def test_a_fenced_answer_parses():
    assert parse_verdict(f"```json\n{_verdict()}\n```").ok


@pytest.mark.parametrize("raw", ["ảnh này ổn", "", "[1,2,3]", '{"score": 8}'])
def test_an_unreadable_answer_raises_rather_than_guessing(raw):
    """Defaulting to `ok` would wave anything through on a bad day;
    defaulting to `not ok` would block generation whenever the checker
    hiccuped. The caller has to be able to tell "the frame failed" from
    "the check failed"."""
    with pytest.raises(GateError):
        parse_verdict(raw)


def test_the_score_is_clamped_to_the_scale():
    assert parse_verdict(_verdict(score=99)).score == 10.0
    assert parse_verdict(_verdict(score=-4)).score == 0.0
    assert parse_verdict('{"ok": true, "score": "tốt"}').score == 0.0


def test_blank_reasons_are_dropped():
    assert parse_verdict(_verdict(reasons=["", "  ", "thật"])).reasons == ["thật"]


# ── the call ──────────────────────────────────────────────────────────


@pytest.fixture
def an_image(tmp_path):
    p = tmp_path / "frame.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n")
    return p


@pytest.mark.asyncio
async def test_the_brief_and_the_image_both_reach_the_checker(an_image, monkeypatch):
    import flowboard.services.llm as llm_module

    seen: dict = {}

    async def _run(kind, prompt, *, system_prompt="", **kw):
        seen["system"] = system_prompt
        seen["attachments"] = kw.get("attachments")
        return _verdict()

    monkeypatch.setattr(llm_module, "run_llm", _run)
    got = await frame_gate.check_frame(an_image, brief="một cô gái áo vàng")

    assert got.ok
    assert "một cô gái áo vàng" in seen["system"]
    assert seen["attachments"] == [str(an_image)]


@pytest.mark.asyncio
async def test_a_missing_file_is_an_error_not_a_rejection(tmp_path):
    with pytest.raises(GateError):
        await frame_gate.check_frame(tmp_path / "nope.png", brief="gì đó")


@pytest.mark.asyncio
async def test_no_brief_means_nothing_to_check_against(an_image):
    """Checking a frame against an empty brief is checking it against
    nothing, and would answer yes to anything."""
    with pytest.raises(GateError):
        await frame_gate.check_frame(an_image, brief="   ")


# ── the executor's decision ───────────────────────────────────────────


def test_the_gate_is_off_unless_asked_for():
    """On by default it would spend a vision call before every i2v
    dispatch on every board."""
    from types import SimpleNamespace

    from flowboard.services.pipeline_executor import _frame_gate_wanted

    assert not _frame_gate_wanted(SimpleNamespace(type="video", data={}))
    assert not _frame_gate_wanted(
        SimpleNamespace(type="video", data={"sourceSettings": {"frame_gate": False}})
    )
    assert _frame_gate_wanted(
        SimpleNamespace(type="video", data={"sourceSettings": {"frame_gate": True}})
    )


@pytest.mark.asyncio
async def test_a_rejected_frame_stops_the_dispatch(monkeypatch, an_image):
    from flowboard.services import media as media_service
    from flowboard.services.pipeline_executor import _frame_gate_block

    media_id = media_service.ingest_local_file(an_image, kind="image")

    import flowboard.services.llm as llm_module

    async def _run(kind, prompt, *, system_prompt="", **kw):
        return _verdict(ok=False, reasons=["sai nhân vật"])

    monkeypatch.setattr(llm_module, "run_llm", _run)
    blocked = await _frame_gate_block(
        {"start_media_id": media_id}, "một cô gái áo vàng"
    )
    assert blocked is not None and "sai nhân vật" in blocked


@pytest.mark.asyncio
async def test_a_checker_that_is_down_does_not_hold_the_pipeline(
    monkeypatch, an_image
):
    """The gate exists to save a credit, not to make generation depend on a
    third service staying up."""
    from flowboard.services import media as media_service
    from flowboard.services.pipeline_executor import _frame_gate_block

    media_id = media_service.ingest_local_file(an_image, kind="image")

    import flowboard.services.llm as llm_module
    from flowboard.services.llm.base import LLMError

    async def _run(kind, prompt, *, system_prompt="", **kw):
        raise LLMError("no vision provider")

    monkeypatch.setattr(llm_module, "run_llm", _run)
    assert await _frame_gate_block({"start_media_id": media_id}, "brief") is None


@pytest.mark.asyncio
async def test_a_frame_that_passes_lets_the_dispatch_through(monkeypatch, an_image):
    from flowboard.services import media as media_service
    from flowboard.services.pipeline_executor import _frame_gate_block

    media_id = media_service.ingest_local_file(an_image, kind="image")

    import flowboard.services.llm as llm_module

    async def _run(kind, prompt, *, system_prompt="", **kw):
        return _verdict(ok=True)

    monkeypatch.setattr(llm_module, "run_llm", _run)
    assert await _frame_gate_block({"start_media_id": media_id}, "brief") is None


@pytest.mark.asyncio
async def test_an_uncached_frame_is_not_a_block():
    from flowboard.services.pipeline_executor import _frame_gate_block

    assert await _frame_gate_block({"start_media_id": "not-a-real-id"}, "brief") is None
