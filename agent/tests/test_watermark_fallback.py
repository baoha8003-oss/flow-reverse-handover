"""The third chance at a watermark, from the switch down to the model.

`inpaint.py` exists because the packaged detector declines: measured on
2026-09-02 its still-image sibling answered "No watermark detected (3%)" on a
frame that plainly carried a Veo logo. The module was built, tested and never
connected — the only workflow carrying a watermark box drives an `edit_video`
node, and that node's logo pass runs `zoom_logo` or `delogo` and nothing else.
So every board in the product reached `delogo`'s smudge and stopped.

The passes are two different jobs, and the packaged tool runs both. Mined from
the binary: `WorkflowRunner._execute_edit_video` calls
`gemini_video_watermark_remover.remove_gemini_video_watermark` under the
setting `auto_remove_gemini_video_watermark`, logging "Xóa logo Gemini: ưu
tiên GPU, tự chuyển CPU nếu GPU không khả dụng" — detector first, CPU model
second. `veo_logo_method: zoom` is the other pass: a crop, of a rectangle the
workflow names. Wiring one to the other would have replaced a framing choice
with a repaint.

That "tự chuyển CPU" is why a detector that cannot RUN counts the same as one
that declines: no GPU is the case the sentence is about.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flowboard.services import postprod_plan as pp


# ── the plan: a switch of its own, ahead of everything that draws ──────


def _edit_ops(**settings) -> list[dict]:
    base = {"aspect_ratio": "PORTRAIT"}
    base.update(settings)
    node = SimpleNamespace(type="edit_video", data={"sourceSettings": base})
    media = SimpleNamespace(type="video", data={"mediaId": "m-1"})
    return pp.ops_for(node, [pp.Upstream(media, "media")], assume_ready=True)


_BOX = {
    "veo_logo_x_pct": 78.0,
    "veo_logo_y_pct": 2.0,
    "veo_logo_w_pct": 20.0,
    "veo_logo_h_pct": 10.0,
}


def test_the_detector_pass_carries_the_box_as_a_fallback():
    ops = _edit_ops(auto_remove_gemini_video_watermark=True, **_BOX)
    op = next(o for o in ops if o["op"] == "remove_watermark")
    assert op["fallbackBox"] == {
        "xPct": 78.0, "yPct": 2.0, "widthPct": 20.0, "heightPct": 10.0,
    }
    assert "region" not in op, "nominating a region throws the detection away"


def test_the_switch_being_off_means_no_pass():
    """The one packaged workflow with a box ships the switch OFF. A pass that
    ran because the box was there would re-encode every clip uninvited."""
    ops = _edit_ops(**_BOX)
    assert [o["op"] for o in ops if o["op"] == "remove_watermark"] == []


def test_no_box_is_still_a_pass():
    """Detection is what the tool is good at — it found `Veo-text 23x10` on a
    real clip. The box is only for the fallback."""
    ops = _edit_ops(auto_remove_gemini_video_watermark=True)
    op = next(o for o in ops if o["op"] == "remove_watermark")
    assert "fallbackBox" not in op


def test_it_runs_before_anything_changes_the_frame():
    """The box is percentages of the SOURCE frame, and the VEO zoom crops the
    frame. Detection after either and the percentages describe a frame that no
    longer exists."""
    ops = _edit_ops(
        auto_remove_gemini_video_watermark=True,
        enable_remove_veo_logo=True,
        enable_aspect_convert=True,
        aspect_output="9:16",
        **_BOX,
    )
    names = [o["op"] for o in ops]
    assert names[0] == "remove_watermark"
    assert names.index("remove_watermark") < names.index("zoom_logo")
    assert names.index("zoom_logo") < names.index("aspect")


def test_the_veo_logo_method_is_left_alone():
    """`zoom` crops the frame and `delogo` blurs a box — visibly different
    results the workflow picked between. Substituting a repaint for either
    would be this module deciding a composition question."""
    ops = _edit_ops(
        auto_remove_gemini_video_watermark=True, enable_remove_veo_logo=True, **_BOX
    )
    assert "zoom_logo" in [o["op"] for o in ops]


def test_a_chain_pass_is_marked_optional_and_a_whole_node_is_not():
    """Same op, two settings. Inside `edit_video` it is one pass of six and
    must not take the music and the subtitles down with it; as a node of its
    own it is the entire job, and silence would be a lie."""
    chain = _edit_ops(auto_remove_gemini_video_watermark=True, **_BOX)
    assert next(o for o in chain if o["op"] == "remove_watermark")["optional"] is True

    node = SimpleNamespace(type="remove_watermark", data={"sourceSettings": dict(_BOX)})
    media = SimpleNamespace(type="video", data={"mediaId": "m-1"})
    ops = pp.ops_for(node, [pp.Upstream(media, "media")], assume_ready=True)
    assert ops[0]["op"] == "remove_watermark"
    assert "optional" not in ops[0]
    assert ops[0]["fallbackBox"]["xPct"] == 78.0


# ── the handler: three rungs, and which failures stop the node ────────


@pytest.fixture
def op_env(monkeypatch, tmp_path):
    """The op with the detector, the model and the cache stubbed out."""
    from flowboard.worker import postprod_handler as ph

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")
    monkeypatch.setattr(
        ph.media_service, "cached_path", lambda mid: clip if mid == "m-1" else None
    )
    monkeypatch.setattr(
        ph.media_service, "ingest_local_file", lambda *a, **k: "painted-id"
    )
    monkeypatch.setattr(ph, "_new_output", lambda suffix: tmp_path / f"out{suffix}")
    return SimpleNamespace(ph=ph, clip=clip, tmp=tmp_path)


def _stub_detector(monkeypatch, *, result=None, error=None):
    import flowboard.services.watermark as wmod

    async def fake(src, dst, region=None):
        if error is not None:
            raise wmod.WatermarkError(error)
        return result

    monkeypatch.setattr(wmod, "remove_from_video", fake)


def _stub_model(monkeypatch, op_env, *, available=True, fail=None):
    from flowboard.services import inpaint

    monkeypatch.setattr(inpaint, "available", lambda: available)
    painted = op_env.tmp / "painted.mp4"

    def fake_inpaint(src, dst, box, **kw):
        if fail is not None:
            raise inpaint.InpaintError(fail)
        painted.write_bytes(b"clean")
        return painted

    monkeypatch.setattr(inpaint, "inpaint_video", fake_inpaint)


@pytest.mark.asyncio
async def test_a_detector_that_declines_hands_over_to_the_model(monkeypatch, op_env):
    import flowboard.services.watermark as wmod

    _stub_detector(
        monkeypatch,
        result=wmod.Result(op_env.clip, detected=False, note="No watermark detected"),
    )
    _stub_model(monkeypatch, op_env)

    result, err = await op_env.ph._OPS["remove_watermark"](
        {"video": "m-1", "fallbackBox": {"xPct": 78.0}}
    )
    assert err is None
    assert result["media_ids"] == ["painted-id"]
    assert "MI-GAN" in result["note"]


@pytest.mark.asyncio
async def test_a_detector_that_cannot_run_hands_over_too(monkeypatch, op_env):
    """"ưu tiên GPU, tự chuyển CPU nếu GPU không khả dụng" — the packaged
    tool's own sentence. A missing executable or a dead GPU used to fail the
    node with the CPU model sitting unused."""
    _stub_detector(monkeypatch, error="thiếu GeminiWatermarkTool-Video.exe")
    _stub_model(monkeypatch, op_env)

    result, err = await op_env.ph._OPS["remove_watermark"](
        {"video": "m-1", "fallbackBox": {"xPct": 78.0}}
    )
    assert err is None
    assert result["media_ids"] == ["painted-id"]
    assert "không chạy được" in result["note"]


@pytest.mark.asyncio
async def test_a_dead_detector_with_no_box_fails_a_node_of_its_own(monkeypatch, op_env):
    """Nothing was removed and nothing can be: the node's only job failed."""
    _stub_detector(monkeypatch, error="GPU không khả dụng")
    _stub_model(monkeypatch, op_env)

    _, err = await op_env.ph._OPS["remove_watermark"]({"video": "m-1"})
    assert err and "GPU" in err


@pytest.mark.asyncio
async def test_a_dead_detector_in_a_chain_keeps_the_other_passes(monkeypatch, op_env):
    """The `no_speech` lesson, one op over: this pass failing used to be the
    whole `edit_video` node failing, discarding the aspect conversion, the
    music bed and the subtitles of a clip that is perfectly intact."""
    _stub_detector(monkeypatch, error="GPU không khả dụng")
    _stub_model(monkeypatch, op_env, available=False)

    result, err = await op_env.ph._OPS["remove_watermark"](
        {"video": "m-1", "optional": True}
    )
    assert err is None
    assert result["media_ids"] == ["m-1"], "the clip has to travel on"
    assert "Không xoá được watermark" in result["note"]


@pytest.mark.asyncio
async def test_a_failing_model_falls_through_rather_than_failing(monkeypatch, op_env):
    """A CPU run that dies on a long clip must not cost the node. `delogo`
    was never worse than this."""
    import flowboard.services.watermark as wmod

    _stub_detector(
        monkeypatch, result=wmod.Result(op_env.clip, detected=False, note="clean")
    )
    _stub_model(monkeypatch, op_env, fail="clip có 5000 khung hình")

    result, err = await op_env.ph._OPS["remove_watermark"](
        {"video": "m-1", "fallbackBox": {"xPct": 78.0}}
    )
    assert err is None
    assert result["media_ids"] == ["m-1"]


@pytest.mark.asyncio
async def test_a_successful_detection_never_reaches_the_model(monkeypatch, op_env):
    """It is the better tool — it rebuilt the sky behind a `Veo-text 23x10`
    in 1.7s on the GPU. The fallback is a fallback."""
    import flowboard.services.watermark as wmod

    cleaned = op_env.tmp / "cleaned.mp4"
    cleaned.write_bytes(b"clean")
    _stub_detector(monkeypatch, result=wmod.Result(cleaned, detected=True, note="ok"))

    ran: list[str] = []
    from flowboard.services import inpaint

    monkeypatch.setattr(inpaint, "available", lambda: ran.append("asked") or True)

    result, err = await op_env.ph._OPS["remove_watermark"](
        {"video": "m-1", "fallbackBox": {"xPct": 78.0}}
    )
    assert err is None and ran == []
    assert result["media_ids"] == ["painted-id"]
