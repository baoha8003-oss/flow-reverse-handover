"""Removing the generator's watermark.

The packaged tool ships two MIT-licensed executables for this and drives them
as subprocesses; this build did not use them at all. What it had instead was
ffmpeg `delogo` (blurs a nominated rectangle) and `zoom_out_logo` (crops the
frame edge) — both faithful to the workflows' `veo_logo_method`, and both
leaving a mark rather than removing one.

The measurement that shaped this module, taken on a real Veo clip:

* the VIDEO build found `Veo-text 23x10`, cleaned 192/192 frames in 1.7s,
  and kept both the audio track and the 720x1280 frame size;
* the IMAGE build, given a frame of that same clip, reported
  `No watermark detected (3%)`.

They are not interchangeable, and the second fact is the one a future reader
is most likely to get wrong.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from flowboard.services import watermark as wm


class _Done:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def fake_tools(monkeypatch, tmp_path):
    """Pretend both executables exist, without needing them installed."""
    exe = tmp_path / "tool.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(wm, "_resolve", lambda tool: exe)
    return exe


def _src(tmp_path: Path) -> Path:
    p = tmp_path / "in.mp4"
    p.write_bytes(b"x")
    return p


# ── the clean-skip path, which is not a failure ───────────────────────


@pytest.mark.asyncio
async def test_no_watermark_found_is_not_an_error(fake_tools, monkeypatch, tmp_path):
    """A clean skip exits **1** — measured, and the reason the skip branch
    reads the tool's words instead of the exit code. Treating a missing
    output file as failure would turn "this clip was already clean" into an
    error the user has to decode."""
    async def fake_run(args, **kw):
        return _Done(1, stdout=b"[SKIP] in.mp4: No watermark detected (3%), skipped")

    monkeypatch.setattr(wm, "run_cli", fake_run)
    src = _src(tmp_path)
    result = await wm.remove_from_video(src, tmp_path / "out.mp4")
    assert result.detected is False
    assert result.path == src, "a clean file must come back as the original"
    assert "watermark" in result.note.lower()


@pytest.mark.asyncio
async def test_a_real_removal_reports_detected(fake_tools, monkeypatch, tmp_path):
    out = tmp_path / "out.mp4"

    async def fake_run(args, **kw):
        out.write_bytes(b"cleaned")
        return _Done(0, stdout=b"[OK] in.mp4 [Veo-text 23x10 192/192 frames]")

    monkeypatch.setattr(wm, "run_cli", fake_run)
    result = await wm.remove_from_video(_src(tmp_path), out)
    assert result.detected is True
    assert result.path == out


@pytest.mark.asyncio
async def test_an_empty_output_file_is_not_mistaken_for_success(
    fake_tools, monkeypatch, tmp_path
):
    """A zero-byte output is a failed run, not a cleaned clip."""
    out = tmp_path / "out.mp4"

    async def fake_run(args, **kw):
        out.write_bytes(b"")
        return _Done(1, stderr=b"encoder failed")

    monkeypatch.setattr(wm, "run_cli", fake_run)
    with pytest.raises(wm.WatermarkError):
        await wm.remove_from_video(_src(tmp_path), out)


@pytest.mark.asyncio
async def test_a_truncated_output_from_a_crash_is_not_success(
    fake_tools, monkeypatch, tmp_path
):
    """The bug this review found in its own code. The first version checked
    for the output file BEFORE the exit code, so a tool that died mid-encode
    — leaving a non-empty, half-written clip at the output path — was
    reported as a successful clean. The file plays up to the crash point,
    which makes it the most convincing wrong answer possible."""
    out = tmp_path / "out.mp4"

    async def fake_run(args, **kw):
        out.write_bytes(b"half a video, then the encoder died")
        return _Done(1, stderr=b"fatal: encoder crashed at frame 90")

    monkeypatch.setattr(wm, "run_cli", fake_run)
    with pytest.raises(wm.WatermarkError):
        await wm.remove_from_video(_src(tmp_path), out)
    assert not out.exists(), "the truncated clip was left where downstream picks it up"


@pytest.mark.asyncio
async def test_exit_zero_with_no_output_is_still_a_failure(
    fake_tools, monkeypatch, tmp_path
):
    """The packaged tool has a Vietnamese string for exactly this case —
    "báo thành công nhưng không tạo ảnh đầu ra" — so it is known to happen."""
    async def fake_run(args, **kw):
        return _Done(0, stdout=b"done")

    monkeypatch.setattr(wm, "run_cli", fake_run)
    with pytest.raises(wm.WatermarkError):
        await wm.remove_from_video(_src(tmp_path), tmp_path / "out.mp4")


# ── failures that must be legible ─────────────────────────────────────


@pytest.mark.asyncio
async def test_a_missing_tool_says_where_it_was_expected(monkeypatch, tmp_path):
    monkeypatch.setattr(wm, "_resolve", lambda tool: None)
    with pytest.raises(wm.WatermarkError) as exc:
        await wm.remove_from_video(_src(tmp_path), tmp_path / "out.mp4")
    assert "third_party" in str(exc.value)


@pytest.mark.asyncio
async def test_a_missing_input_is_caught_before_spawning(fake_tools, monkeypatch, tmp_path):
    called = False

    async def fake_run(args, **kw):
        nonlocal called
        called = True
        return _Done(0)

    monkeypatch.setattr(wm, "run_cli", fake_run)
    with pytest.raises(wm.WatermarkError):
        await wm.remove_from_video(tmp_path / "nope.mp4", tmp_path / "out.mp4")
    assert not called, "spawned a process for a file that is not there"


@pytest.mark.asyncio
async def test_a_timeout_is_reported_as_one(fake_tools, monkeypatch, tmp_path):
    async def fake_run(args, **kw):
        raise subprocess.TimeoutExpired(["tool"], 900.0)

    monkeypatch.setattr(wm, "run_cli", fake_run)
    with pytest.raises(wm.WatermarkError) as exc:
        await wm.remove_from_video(_src(tmp_path), tmp_path / "out.mp4")
    assert "timed out" in str(exc.value)


@pytest.mark.asyncio
async def test_an_unexplained_failure_quotes_the_tool(fake_tools, monkeypatch, tmp_path):
    """The tools print progress bars and per-frame debug; the reason is on
    the last line, and the first 200 characters would be the banner."""
    async def fake_run(args, **kw):
        return _Done(1, stdout=b"banner\nprogress 50%\nfatal: unsupported codec")

    monkeypatch.setattr(wm, "run_cli", fake_run)
    with pytest.raises(wm.WatermarkError) as exc:
        await wm.remove_from_video(_src(tmp_path), tmp_path / "out.mp4")
    assert "unsupported codec" in str(exc.value)


# ── the invocation ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_banner_is_suppressed(fake_tools, monkeypatch, tmp_path):
    """The tool's own help recommends this for "scripts and AI agents";
    without it an ASCII banner lands in the captured output and every error
    message starts with it."""
    seen: list[str] = []

    async def fake_run(args, **kw):
        seen.extend(args)
        (tmp_path / "out.mp4").write_bytes(b"ok")
        return _Done(0)

    monkeypatch.setattr(wm, "run_cli", fake_run)
    await wm.remove_from_video(_src(tmp_path), tmp_path / "out.mp4")
    assert "--no-banner" in seen


@pytest.mark.asyncio
async def test_a_region_is_passed_through(fake_tools, monkeypatch, tmp_path):
    """`--region` takes the tool's own syntax, which is why the packaged
    workflows' `veo_logo_*` percentages can drive it."""
    seen: list[str] = []

    async def fake_run(args, **kw):
        seen.extend(args)
        (tmp_path / "out.mp4").write_bytes(b"ok")
        return _Done(0)

    monkeypatch.setattr(wm, "run_cli", fake_run)
    await wm.remove_from_video(_src(tmp_path), tmp_path / "out.mp4", region="br:auto")
    assert seen[seen.index("--region") + 1] == "br:auto"


@pytest.mark.asyncio
async def test_no_region_means_let_the_tool_detect(fake_tools, monkeypatch, tmp_path):
    """Detection is what it is good at. Always nominating a box would throw
    that away and reproduce delogo's weakness."""
    seen: list[str] = []

    async def fake_run(args, **kw):
        seen.extend(args)
        (tmp_path / "out.mp4").write_bytes(b"ok")
        return _Done(0)

    monkeypatch.setattr(wm, "run_cli", fake_run)
    await wm.remove_from_video(_src(tmp_path), tmp_path / "out.mp4")
    assert "--region" not in seen


def test_video_and_image_use_different_executables():
    """The mistake this guards against: routing a video frame at the image
    build because both say "watermark". Measured — the image build reports
    `No watermark detected (3%)` on a Veo frame the video build cleans."""
    assert wm._VIDEO_TOOL != wm._IMAGE_TOOL
    assert "Video" in wm._VIDEO_TOOL.name


# ── the worker op keeps ids stable ────────────────────────────────────


@pytest.mark.asyncio
async def test_a_clean_clip_keeps_its_original_media_id(monkeypatch, tmp_path):
    """When nothing was removed, the caller gets back the id it already
    holds. The first version re-ingested the source, which copied the full
    clip into the cache a second time just to mint a new id for identical
    bytes — and made "nothing happened" indistinguishable from "new file"."""
    from flowboard.worker import postprod_handler as ph

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video")

    monkeypatch.setattr(
        ph.media_service, "cached_path", lambda mid: clip if mid == "orig-id" else None
    )
    minted: list[str] = []
    monkeypatch.setattr(
        ph.media_service,
        "ingest_local_file",
        lambda *a, **k: minted.append("x") or "new-id",
    )

    async def fake_remove(src, dst, region=None):
        return wm.Result(src, detected=False, note="clean")

    import flowboard.services.watermark as wmod

    monkeypatch.setattr(wmod, "remove_from_video", fake_remove)

    result, err = await ph._OPS["remove_watermark"]({"video": "orig-id"})
    assert err is None
    assert result["media_ids"] == ["orig-id"]
    assert minted == [], "re-ingested identical bytes to mint a new id"
