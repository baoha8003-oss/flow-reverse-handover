"""Scoring a generated clip against the prompt that asked for it.

Ported from flowkit's `video_reviewer.py`. The parts worth keeping were its
calibration — six weighted dimensions, an error rubric with severity tiers,
and `usable_segments` — not its plumbing: its CLI spawning, its database
models and its `os.symlink` frame staging (which needs Developer Mode on
Windows, where this build runs) all stayed behind.

One vision call per clip, no Flow credits. `routes/estimate.py` counts it on
its own line for that reason.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from flowboard.services import assets
from flowboard.services import media as media_service
from flowboard.services import postprod, video_review
from flowboard.services.video_review import Issue, overall, parse_review, verdict_for
from flowboard.worker.postprod_handler import handle_postprod

ffmpeg_only = pytest.mark.skipif(
    not postprod.available(), reason="ffmpeg not available"
)


def _answer(**over) -> str:
    body = {
        "dimensions": {
            "character_consistency": 8.0, "prompt_adherence": 8.0,
            "motion_quality": 8.0, "visual_fidelity": 8.0,
            "temporal_coherence": 8.0, "composition": 8.0,
        },
        "issues": [],
        "usable_segments": [{"time_range": "0s-8s", "score": 8.0}],
    }
    body.update(over)
    return json.dumps(body)


# ── scoring ───────────────────────────────────────────────────────────


def test_the_weights_sum_to_one():
    """Otherwise the overall score is not on the same 0-10 scale as the
    dimensions it is built from, and every threshold downstream is wrong."""
    assert sum(video_review.WEIGHTS.values()) == pytest.approx(1.0)


def test_a_missing_dimension_counts_as_zero_not_as_a_pass():
    """A model that skipped a dimension did not judge it. Treating silence
    as full marks is how a broken review reads as a good clip."""
    assert overall({"character_consistency": 10.0}) == pytest.approx(2.5)


@pytest.mark.parametrize(
    "score, expected",
    [(9.5, "excellent"), (8.0, "good"), (6.5, "acceptable"),
     (5.0, "poor"), (2.0, "unusable")],
)
def test_the_verdict_bands(score, expected):
    assert verdict_for(score) == expected


def test_character_consistency_leads_the_weighting():
    """The most common failure in generated video and the one a viewer
    notices first — a face that changes mid-clip reads as broken even when
    everything else is beautiful."""
    weights = video_review.WEIGHTS
    assert weights["character_consistency"] == max(weights.values())


# ── parsing a model's answer ──────────────────────────────────────────


def test_a_clean_answer_parses():
    review = parse_review(_answer())
    assert review.score == pytest.approx(8.0)
    assert review.verdict == "good"
    assert review.usable_segments[0]["time_range"] == "0s-8s"


def test_a_fenced_answer_parses():
    """Models fence JSON despite being told not to."""
    review = parse_review(f"```json\n{_answer()}\n```")
    assert review.score == pytest.approx(8.0)


def test_an_answer_that_is_not_json_is_an_error_not_a_zero():
    """A zero would read as "the clip is terrible" when what happened is
    that the review did not run."""
    with pytest.raises(video_review.ReviewError):
        parse_review("Clip này khá ổn.")


def test_a_critical_issue_caps_character_consistency():
    """The prompt states the rule; it is enforced here rather than trusted.
    A model that finds a character morph and then scores consistency 9 has
    contradicted itself, and the finding is the more reliable half."""
    review = parse_review(_answer(issues=[
        {"severity": "CRITICAL", "time_range": "3s-5s", "description": "mặt đổi"},
    ]))
    assert review.dimensions["character_consistency"] <= 3.0
    assert review.has_critical
    # And the cap has to reach the overall score, not just the dimension.
    assert review.score < 8.0


def test_an_unknown_severity_is_treated_as_minor_not_dropped():
    review = parse_review(_answer(issues=[
        {"severity": "CATASTROPHIC", "time_range": "1s", "description": "gì đó"},
    ]))
    assert len(review.issues) == 1
    assert review.issues[0].severity == "MINOR"
    assert not review.has_critical


def test_an_issue_with_no_description_is_dropped():
    review = parse_review(_answer(issues=[
        {"severity": "HIGH", "time_range": "1s", "description": "  "},
    ]))
    assert review.issues == []


def test_scores_are_clamped_to_the_scale():
    review = parse_review(_answer(dimensions={
        "character_consistency": 47, "prompt_adherence": -3,
        "motion_quality": "tốt", "visual_fidelity": 8,
        "temporal_coherence": 8, "composition": 8,
    }))
    assert review.dimensions["character_consistency"] == 10.0
    assert review.dimensions["prompt_adherence"] == 0.0
    assert review.dimensions["motion_quality"] == 0.0


def test_the_fix_hint_names_one_lever_not_six():
    """A fix addressing every dimension at once is a rewrite. The retry loop
    needs one concrete thing to change."""
    review = parse_review(_answer(dimensions={
        "character_consistency": 8.0, "prompt_adherence": 8.0,
        "motion_quality": 2.0, "visual_fidelity": 8.0,
        "temporal_coherence": 8.0, "composition": 8.0,
    }))
    assert "giật" in review.fix_hint or "lùi" in review.fix_hint


def test_a_critical_issue_gets_its_own_hint():
    review = parse_review(_answer(issues=[
        {"severity": "CRITICAL", "time_range": "3s", "description": "đổi mặt"},
    ]))
    assert "nghiêm trọng" in review.fix_hint


# ── contact sheets ────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def clip(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("review")
    out = root / "clip.mp4"
    subprocess.run(
        [assets.ffmpeg_bin(), "-hide_banner", "-nostdin", "-y",
         "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=4",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)],
        capture_output=True, check=True,
    )
    return out


@ffmpeg_only
def test_the_sheets_tile_the_clip(clip, tmp_path):
    sheets, frames = postprod.contact_sheets(clip, tmp_path, fps=3.0)
    assert sheets and frames > 0
    assert all(s.is_file() and s.stat().st_size > 0 for s in sheets)


@ffmpeg_only
def test_a_frame_cap_is_honoured(clip, tmp_path):
    """Every frame is pixels a vision call pays for."""
    _, frames = postprod.contact_sheets(clip, tmp_path, fps=12.0, max_frames=8)
    assert frames == 8


@ffmpeg_only
def test_the_grid_leaves_no_empty_cell(clip, tmp_path):
    """The detail worth keeping from the original, where it was confirmed by
    a live review call: `tile` fills a grid it cannot complete with a solid
    colour block, and vision models read that block as a defect in the
    video. Seven frames must not be laid out 4x2 with one cell blank.

    Measured as cells-on-the-sheet, not as a particular shape: the invariant
    is that every cell holds a frame, and which gapless grid gets picked is
    the algorithm's business. Seven frames in a 4x2 sheet has only one
    gapless answer (7 is prime, so 1x7) and the naive 4x2 would leave one
    cell filled with a solid block."""
    sheets, frames = postprod.contact_sheets(
        clip, tmp_path, fps=1.75, max_frames=7, cols=4, rows=2
    )
    assert frames == 7
    cell_w, cell_h = postprod.frame_size(
        sorted((tmp_path / "frames").glob("frame-*.jpg"))[0]
    )
    width, height = postprod.frame_size(sheets[0])
    cells = (width // cell_w) * (height // cell_h)
    assert cells == frames, f"{cells} cells for {frames} frames ({width}x{height})"


@ffmpeg_only
def test_more_frames_than_one_sheet_holds_produces_several(clip, tmp_path):
    sheets, frames = postprod.contact_sheets(
        clip, tmp_path, fps=6.0, max_frames=12, cols=2, rows=2
    )
    assert frames == 12 and len(sheets) == 3


@ffmpeg_only
@pytest.mark.parametrize("bad", [{"fps": 0}, {"cols": 0}, {"rows": 0}, {"max_frames": 0}])
def test_an_impossible_grid_is_refused(clip, tmp_path, bad):
    with pytest.raises(postprod.PostProdError):
        postprod.contact_sheets(clip, tmp_path, **bad)


# ── the op and the planner ────────────────────────────────────────────


def _ops(settings: dict, prompt: str = "") -> list[dict]:
    from flowboard.services.postprod_plan import Upstream, ops_for

    node = SimpleNamespace(
        id=1, type="review_video", data={"sourceSettings": settings}
    )
    clip_node = SimpleNamespace(id=2, type="video", data={"mediaId": "clip"})
    ups = [Upstream(clip_node, "media")]
    if prompt:
        ups.append(Upstream(SimpleNamespace(id=3, type="prompt", data={"prompt": prompt}), "text"))
    return ops_for(node, ups)


def test_the_node_plans_one_review():
    ops = _ops({})
    assert [o["op"] for o in ops] == ["review"]


def test_the_upstream_prompt_is_what_the_clip_is_judged_against():
    """Scoring "prompt adherence" without the prompt is scoring nothing."""
    ops = _ops({}, prompt="một cô gái đi bộ ở Hà Nội")
    assert ops[0]["prompt"] == "một cô gái đi bộ ở Hà Nội"


def test_a_review_node_with_no_clip_plans_nothing():
    from flowboard.services.postprod_plan import ops_for

    node = SimpleNamespace(id=1, type="review_video", data={})
    assert ops_for(node, []) == []


@pytest.mark.asyncio
@ffmpeg_only
async def test_the_op_returns_a_score_and_keeps_the_clip(clip, monkeypatch):
    """The review produces no media — it must hand the clip through
    unchanged, or a review node in the middle of a chain costs the chain
    its video."""
    import flowboard.services.llm as llm_module

    async def _run(kind, prompt, *, system_prompt="", **kw):
        return _answer()

    monkeypatch.setattr(llm_module, "run_llm", _run)
    video_id = media_service.ingest_local_file(clip, kind="video")

    result, err = await handle_postprod(
        {"op": "review", "video": video_id, "prompt": "một hình test"}
    )
    assert err is None, err
    assert result["media_ids"] == [video_id]
    assert result["review"]["verdict"] == "good"
    assert result["review"]["frames"] > 0


@pytest.mark.asyncio
@ffmpeg_only
async def test_a_threshold_reports_pass_or_fail(clip, monkeypatch):
    """What the retry loop reads. Absent without a threshold, because
    "did it pass" has no answer until someone says what passing is."""
    import flowboard.services.llm as llm_module

    async def _run(kind, prompt, *, system_prompt="", **kw):
        return _answer()

    monkeypatch.setattr(llm_module, "run_llm", _run)
    video_id = media_service.ingest_local_file(clip, kind="video")

    result, _ = await handle_postprod(
        {"op": "review", "video": video_id, "threshold": 9.0}
    )
    assert result["review"]["passed"] is False

    result, _ = await handle_postprod(
        {"op": "review", "video": video_id, "threshold": 7.0}
    )
    assert result["review"]["passed"] is True

    result, _ = await handle_postprod({"op": "review", "video": video_id})
    assert result["review"]["passed"] is None


@pytest.mark.asyncio
@ffmpeg_only
async def test_a_reviewer_that_answers_nonsense_is_an_error(clip, monkeypatch):
    """Not a zero score. "The review did not run" and "the clip is
    unusable" must not look the same to the caller."""
    import flowboard.services.llm as llm_module

    async def _run(kind, prompt, *, system_prompt="", **kw):
        return "clip đẹp lắm"

    monkeypatch.setattr(llm_module, "run_llm", _run)
    video_id = media_service.ingest_local_file(clip, kind="video")

    result, err = await handle_postprod({"op": "review", "video": video_id})
    assert result == {}
    assert err is not None and err.startswith("review_failed")


@pytest.mark.asyncio
@ffmpeg_only
async def test_the_prompt_reaches_the_reviewer(clip, monkeypatch):
    import flowboard.services.llm as llm_module

    seen: dict = {}

    async def _run(kind, prompt, *, system_prompt="", **kw):
        seen["system"] = system_prompt
        seen["attachments"] = kw.get("attachments")
        return _answer()

    monkeypatch.setattr(llm_module, "run_llm", _run)
    video_id = media_service.ingest_local_file(clip, kind="video")
    await handle_postprod(
        {"op": "review", "video": video_id, "prompt": "một con mèo đi bộ"}
    )
    assert "một con mèo đi bộ" in seen["system"]
    assert seen["attachments"], "the sheets have to actually be attached"


@pytest.mark.asyncio
@ffmpeg_only
async def test_the_packaged_rubric_reaches_the_reviewer(clip, monkeypatch):
    """The six dimensions this build scores on are its own invention. The
    packaged tool grades against a 100-point director's rubric and a
    25-point viral score that ship in the asset library and went unread for
    the whole life of this feature — so the reviewer was answering a
    different question from the one the original tool asks."""
    from flowboard.services import knowledge

    if not knowledge.available():
        pytest.skip("packaged skill library not installed")

    import flowboard.services.llm as llm_module

    seen: dict = {}

    async def _run(kind, prompt, *, system_prompt="", **kw):
        seen["system"] = system_prompt
        return _answer()

    monkeypatch.setattr(llm_module, "run_llm", _run)
    video_id = media_service.ingest_local_file(clip, kind="video")
    await handle_postprod({"op": "review", "video": video_id, "prompt": "x"})

    rubric = knowledge.sections_for("review_clip")
    assert rubric.text, "the rubric itself failed to load"
    # A distinctive line from the rubric, not the section marker, so the
    # test fails if the file is attached empty.
    assert "Viral Score" in seen["system"] or "Thang Điểm" in seen["system"]


# ── the estimate ──────────────────────────────────────────────────────


def test_a_review_node_shows_on_its_own_line(client):
    """A board that reviews every clip doubles its AI spend, and that
    belongs in front of someone before the run rather than after it."""
    board = client.post("/api/boards", json={"name": "r"}).json()["id"]
    client.post("/api/nodes", json={
        "board_id": board, "type": "review_video", "x": 0, "y": 0,
        "data": {"mediaId": "x"},
    })
    body = client.get(f"/api/boards/{board}/estimate").json()
    assert body["reviewJobs"] == 1
    assert body["billableJobs"] == 0
    assert body["items"][0]["credits"] is None
    assert "chấm clip" in body["items"][0]["note"]


def test_a_severity_outside_the_three_tiers_is_reported_not_just_normalised():
    """Three things branch on the exact string — the `character_consistency`
    cap, `has_critical`, and `fix_hint` — so a model grading with its own
    vocabulary silently disables all three and the clip passes carrying a defect
    it just described. Normalised so one bad line does not cost a whole review,
    but said out loud: a rising count means the reviewer is off the rubric.
    """
    import logging

    raw = json.dumps({
        "dimensions": {k: 9.0 for k in video_review.WEIGHTS},
        "issues": [{
            "severity": "BLOCKING",
            "time_range": "2s-4s",
            "description": "Mặt nhân vật đổi hẳn",
        }],
    })
    logger = logging.getLogger("flowboard.services.video_review")
    seen: list[str] = []

    class _Catch(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    handler = _Catch(level=logging.WARNING)
    logger.addHandler(handler)
    try:
        review = video_review.parse_review(raw)
    finally:
        logger.removeHandler(handler)

    assert review.issues[0].severity == "MINOR"
    assert any("BLOCKING" in m for m in seen), seen
