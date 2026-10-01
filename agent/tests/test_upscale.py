"""End-to-end tests for the local RealESRGAN upscaler.

These drive the real binary on real media — the whole value of the module is
that the bundled engine and ffmpeg agree on frame naming, frame rate and audio
mapping, and only a real run proves that. Media is kept deliberately tiny so
the file stays fast. Everything skips cleanly when the engine is absent or
Vulkan cannot start, since a build machine may have no usable GPU.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from flowboard.services import assets, upscale


def _ffmpeg(*args: str) -> None:
    binary = assets.ffmpeg_bin()
    if binary is None:
        pytest.skip("ffmpeg not available")
    result = subprocess.run(
        [binary, "-v", "error", "-y", *args], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def _probe(path, stream: str, entries: str) -> str:
    binary = assets.ffprobe_bin()
    if binary is None:
        pytest.skip("ffprobe not available")
    result = subprocess.run(
        [
            binary, "-v", "error",
            "-select_streams", stream,
            "-show_entries", f"stream={entries}",
            "-of", "csv=p=0",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _dimensions(path) -> tuple[int, int]:
    width, height = _probe(path, "v:0", "width,height").splitlines()[0].split(",")
    return int(width), int(height)


def _duration(path) -> float:
    binary = assets.ffprobe_bin()
    result = subprocess.run(
        [binary, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return float(result.stdout.strip())


def _psnr(actual, reference) -> float:
    """Mean PSNR in dB between two same-sized images.

    Corrupted engine output still has the right dimensions, so only a pixel
    comparison can tell a real upscale from a garbage one.
    """
    binary = assets.ffmpeg_bin()
    result = subprocess.run(
        [binary, "-hide_banner", "-i", str(actual), "-i", str(reference),
         "-lavfi", "psnr", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    match = re.search(r"average:([0-9.]+|inf)", result.stderr)
    assert match, result.stderr
    return float("inf") if match.group(1) == "inf" else float(match.group(1))


def _make_image(path, size: str = "64x64") -> None:
    _ffmpeg("-f", "lavfi", "-i", f"testsrc=size={size}:duration=1:rate=1",
            "-frames:v", "1", str(path))


def _make_clip(path, size: str = "64x48", rate: int = 10, seconds: float = 0.5) -> None:
    """A short clip that really carries an audio track."""
    _ffmpeg(
        "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}",
        "-f", "lavfi", "-i", "sine=frequency=440",
        "-t", str(seconds),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(path),
    )


@pytest.fixture(scope="module")
def engine(tmp_path_factory):
    """Skip the whole module unless a real upscale actually completes."""
    if not upscale.available():
        pytest.skip("RealESRGAN engine or models not installed")
    probe_dir = tmp_path_factory.mktemp("engine-probe")
    src, dst = probe_dir / "in.png", probe_dir / "out.png"
    _make_image(src, size="32x32")
    try:
        upscale.upscale_image(src, dst, scale=4)
    except RuntimeError as exc:
        pytest.skip(f"RealESRGAN cannot run here (no Vulkan/GPU?): {exc}")
    return True


def test_models_lists_bundled_param_files():
    names = upscale.models()
    assert names == sorted(names)
    if assets.upscale_model_dir() is not None:
        assert upscale.DEFAULT_MODEL in names
        assert all(not n.endswith(".param") for n in names)


def test_available_means_every_listed_model_is_complete_on_disk():
    """``available()`` is a promise the engine can actually be invoked."""
    if not upscale.available():
        pytest.skip("RealESRGAN engine or models not installed")
    binary = assets.realesrgan_bin()
    assert binary is not None and Path(binary).is_file()
    model_dir = assets.upscale_model_dir()
    names = upscale.models()
    assert names, "available() was True with no models"
    for name in names:
        # ncnn needs both halves; a lone .param would list but never run.
        assert (model_dir / f"{name}.param").is_file()
        assert (model_dir / f"{name}.bin").is_file()


def test_model_scale_reads_the_ratio_out_of_the_name():
    assert upscale.model_scale("realesrgan-x4plus") == 4
    assert upscale.model_scale("upscayl-lite-4x") == 4
    assert upscale.model_scale("realesr-animevideov3-x2") == 2
    assert upscale.model_scale("nameless") is None


def test_every_bundled_model_declares_its_ratio():
    """A model whose ratio cannot be read would skip the corruption guard."""
    for name in upscale.models():
        assert upscale.model_scale(name) is not None, name


@pytest.mark.parametrize("scale", [2, 3])
def test_scale_the_model_cannot_do_is_rejected(tmp_path, scale):
    """The engine accepts ``-s 2`` on a 4x model and returns garbage at exit 0.

    Measured against realesrgan-x4plus, -s 2 scores 4.5 dB PSNR versus 26 dB at
    -s 4, so this has to be refused before the binary is ever started.
    """
    src = tmp_path / "in.png"
    _make_image(src)
    if not upscale.available():
        pytest.skip("RealESRGAN engine not installed")
    with pytest.raises(ValueError, match="upscales by 4x only"):
        upscale.upscale_image(src, tmp_path / "out.png", scale=scale)


def test_unknown_model_rejected_before_running(tmp_path):
    src = tmp_path / "in.png"
    _make_image(src)
    if not upscale.available():
        pytest.skip("RealESRGAN engine not installed")
    with pytest.raises(ValueError, match="unknown upscale model"):
        upscale.upscale_image(src, tmp_path / "out.png", model="not-a-model")


def test_invalid_scale_rejected(tmp_path):
    src = tmp_path / "in.png"
    _make_image(src)
    if not upscale.available():
        pytest.skip("RealESRGAN engine not installed")
    with pytest.raises(ValueError, match="scale must be one of"):
        upscale.upscale_image(src, tmp_path / "out.png", scale=5)


def test_missing_source_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        upscale.upscale_image(tmp_path / "nope.png", tmp_path / "out.png")


def test_upscale_image_quadruples_dimensions(engine, tmp_path):
    src, dst = tmp_path / "in.png", tmp_path / "out.png"
    _make_image(src, size="64x64")
    assert _dimensions(src) == (64, 64)

    result = upscale.upscale_image(src, dst, scale=4)

    assert result == dst and dst.is_file()
    assert _dimensions(dst) == (256, 256)


def test_upscale_image_output_is_not_corrupted(engine, tmp_path):
    """Right dimensions are not proof: a mis-driven engine fills them with noise."""
    src = tmp_path / "in.png"
    _make_image(src, size="128x128")
    dst = tmp_path / "out.png"
    reference = tmp_path / "ref.png"
    _ffmpeg("-i", str(src), "-vf", "scale=512:512:flags=lanczos", str(reference))

    upscale.upscale_image(src, dst, scale=4)

    # A correct 4x lands ~26 dB against a plain lanczos enlargement; corrupted
    # output of the same size measures ~4.5 dB.
    assert _psnr(dst, reference) > 15.0


def test_upscale_image_handles_a_vietnamese_path(engine, tmp_path):
    """Asset and project folders here are routinely named in Vietnamese.

    The engine aborts with STATUS_STACK_BUFFER_OVERRUN on a non-ASCII path, so
    such a job has to be staged through an ASCII directory.
    """
    folder = tmp_path / "Nhạc nền v1.2 - Chill"
    folder.mkdir()
    src, dst = folder / "cảnh quay.png", folder / "cảnh quay 4x.png"
    _make_image(src, size="32x32")

    upscale.upscale_image(src, dst, scale=4)

    assert dst.is_file()
    assert _dimensions(dst) == (128, 128)


def test_upscale_video_keeps_audio_and_enlarges(engine, tmp_path):
    src, dst = tmp_path / "clip.mp4", tmp_path / "big.mp4"
    _make_clip(src, size="64x48")
    assert _probe(src, "a", "codec_type") == "audio"

    seen: list[tuple[int, int]] = []
    result = upscale.upscale_video(src, dst, scale=4, progress=lambda d, t: seen.append((d, t)))

    assert result == dst and dst.is_file()
    assert _dimensions(dst) == (256, 192)
    assert _probe(dst, "v:0", "codec_type") == "video"
    assert _probe(dst, "a", "codec_type") == "audio", "original audio was dropped"
    # Frame rate must survive the round-trip.
    assert _probe(dst, "v:0", "r_frame_rate") == _probe(src, "v:0", "r_frame_rate")
    assert _probe(dst, "a:0", "sample_rate") == _probe(src, "a:0", "sample_rate")
    assert abs(_duration(dst) - _duration(src)) < 0.05, "output was truncated or stretched"

    assert seen, "progress callback was never invoked"
    total = seen[-1][1]
    assert total == int(_probe(src, "v:0", "nb_frames")), "progress total is not the frame count"
    assert seen[-1] == (total, total)
    assert [d for d, _ in seen] == sorted(d for d, _ in seen)
    assert {t for _, t in seen} == {total}
    # Without this the module could report a single 100% call at the end and
    # every other assertion here would still hold.
    assert len(seen) >= total, f"no per-frame progress: {len(seen)} calls for {total} frames"
    assert [d for d, _ in seen[:total]] == list(range(1, total + 1))


def test_upscale_video_target_height_downscales_after_pass(engine, tmp_path):
    src, dst = tmp_path / "clip.mp4", tmp_path / "exact.mp4"
    _make_clip(src, size="64x48")

    upscale.upscale_video(src, dst, scale=4, target_height=120)

    # 4x would land on 192 high; target_height must win and keep the 4:3 ratio.
    assert _dimensions(dst) == (160, 120)
    assert _probe(dst, "a", "codec_type") == "audio"


def test_anamorphic_display_aspect_survives(engine, tmp_path):
    """Extracted PNGs carry no pixel aspect, so it has to be put back."""
    src, dst = tmp_path / "anam.mp4", tmp_path / "anam_big.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=64x48:rate=10", "-t", "0.3",
            "-vf", "setsar=2/1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src))
    assert _probe(src, "v:0", "sample_aspect_ratio") == "2:1"

    upscale.upscale_video(src, dst, scale=4)

    assert _probe(dst, "v:0", "sample_aspect_ratio") == "2:1", "pixel aspect was flattened"
    assert _probe(dst, "v:0", "display_aspect_ratio") == _probe(
        src, "v:0", "display_aspect_ratio"
    ), "anamorphic source came back squashed"


def test_variable_frame_rate_source_keeps_its_running_time(engine, tmp_path):
    """r_frame_rate is only the base tick; rebuilding on it stretches a VFR clip."""
    src, dst = tmp_path / "vfr.mp4", tmp_path / "vfr_big.mp4"
    _ffmpeg("-f", "lavfi", "-i", "testsrc2=size=64x48:rate=30", "-t", "1.0",
            "-vf", "select='not(mod(n,3))'", "-fps_mode", "vfr",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src))
    assert _probe(src, "v:0", "r_frame_rate") != _probe(src, "v:0", "avg_frame_rate"), (
        "fixture is not actually variable rate"
    )

    upscale.upscale_video(src, dst, scale=4)

    assert abs(_duration(dst) - _duration(src)) < 0.02, "VFR clip changed duration"


def test_odd_target_height_rejected(tmp_path):
    src = tmp_path / "clip.mp4"
    _make_clip(src)
    if not upscale.available():
        pytest.skip("RealESRGAN engine not installed")
    with pytest.raises(ValueError, match="positive even number"):
        upscale.upscale_video(src, tmp_path / "out.mp4", target_height=1081)


def test_non_video_source_raises(tmp_path):
    if not upscale.available():
        pytest.skip("RealESRGAN engine not installed")
    not_a_video = tmp_path / "notes.txt"
    not_a_video.write_text("definitely not a video", encoding="utf-8")
    with pytest.raises(RuntimeError, match="ffprobe failed"):
        upscale.upscale_video(not_a_video, tmp_path / "out.mp4")


def test_video_failure_cleans_up_workdir(engine, tmp_path):
    """A failure *after* frames are on disk must still leave no temp dir behind."""
    tmp_root = upscale._work_root()
    before = set(tmp_root.glob("upscale-*")) if tmp_root.is_dir() else set()

    src = tmp_path / "clip.mp4"
    _make_clip(src, seconds=0.2)
    # A container ffmpeg cannot write fails at reassembly, i.e. once the work
    # directory is full of extracted and upscaled frames.
    with pytest.raises(RuntimeError, match="reassemble video"):
        upscale.upscale_video(src, tmp_path / "out.xyz")

    after = set(tmp_root.glob("upscale-*")) if tmp_root.is_dir() else set()
    assert after == before, "temp work directory leaked after a failure"
