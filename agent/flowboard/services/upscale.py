"""Local RealESRGAN upscaling, replacing Flow's paid cloud upscale.

The bundled ``realesrgan-ncnn-vulkan`` binary only understands still images, so
video is handled the long way round: explode to PNG frames, upscale the frame
directory in one engine invocation, then reassemble at the source frame rate
and mux the *original* audio back in. Narrated clips are the whole point of
this build, so a video path that quietly dropped the audio track would be worse
than no upscale at all.

Both bundled models are fixed 4x networks, and the binary will happily honour
``-s 2`` against one by emitting a correctly sized image of corrupted pixels at
exit status 0, so the requested ratio is checked against the model rather than
against the binary's usage text. Land on an arbitrary size with
``target_height`` instead.

Everything runs offline against the packaged asset library resolved by
``flowboard.services.assets``. When the engine or its models are missing,
``available()`` reports False so the UI can hide the feature instead of
failing mid-render.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Optional, Sequence

from flowboard.config import STORAGE_DIR
from flowboard.services import assets

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "realesrgan-x4plus"

# The binary's own usage text: "-s scale  upscale ratio (can be 2, 3, 4)".
VALID_SCALES = (2, 3, 4)

# -s only picks a ratio on multi-scale models. Asking a fixed-ratio model for a
# different one still exits 0 and writes a correctly sized image full of
# corrupted pixels: measured against realesrgan-x4plus, -s 2 scores 4.5 dB PSNR
# and -s 3 scores 5.2 dB, where -s 4 scores 26 dB. The ratio in the model name
# is therefore the authority, not the usage text.
_MODEL_SCALE_RE = re.compile(r"x(\d+)|(\d+)x")

# Frame names must sort lexicographically for ffmpeg's image2 demuxer and stay
# stable across the engine round-trip (it reuses the input base name).
_FRAME_PATTERN = "%08d.png"

# The engine emits exactly one percentage line per finished image; that is the
# only per-frame progress signal it offers.
_PROGRESS_LINE = re.compile(r"\d+(?:\.\d+)?%")

_STDERR_TAIL_LINES = 12

# Wall-clock ceilings. Every one of these runs under run_in_threadpool on
# Starlette's bounded, app-wide threadpool, so a wedged child process costs a
# slot that nothing can reclaim short of a restart.
FFMPEG_TIMEOUT_S = 1800.0
PROBE_TIMEOUT_S = 60.0
# The engine gets a per-frame budget rather than a flat one: a 4x pass over
# thousands of frames is legitimately slow, but silence for this long per
# frame means it is stuck (a Vulkan device that never returns, say).
ENGINE_STALL_TIMEOUT_S = 300.0

# Upscaling a video explodes every frame to PNG first: a 10-minute 1080p
# source is ~18,000 files and well over 100 GB. Nothing else here checks free
# space, so the duration is the gate.
MAX_UPSCALE_DURATION_S = 300.0

ProgressFn = Callable[[int, int], None]


def models() -> list[str]:
    """Model names usable with ``-n``, derived from the bundled ``.param`` files."""
    model_dir = assets.upscale_model_dir()
    if model_dir is None:
        return []
    return sorted(p.stem for p in model_dir.glob("*.param"))


def available() -> bool:
    """True when SOME engine binary and at least one model are present.

    Two binaries ship in `UpscaleEngine/`, and this used to answer for one of
    them. On a machine where the Vulkan build is missing or refuses to start,
    upscaling reported itself unavailable while a working engine sat in the
    same folder.
    """
    return (
        assets.realesrgan_bin() is not None or assets.upscayl_bin() is not None
    ) and bool(models())


def engine_name() -> Optional[str]:
    """Which engine would run, for the log line and the result metadata.

    A fallback that nobody can see is a fallback that gets blamed on the
    model: the two produce visibly different output, and "why does this look
    different today" needs an answer better than a shrug.
    """
    if assets.realesrgan_bin() is not None:
        return "realesrgan"
    if assets.upscayl_bin() is not None:
        return "upscayl"
    return None


def model_scale(model: str) -> Optional[int]:
    """Native ratio encoded in a model name: ``realesrgan-x4plus`` -> 4."""
    match = _MODEL_SCALE_RE.search(model)
    if match is None:
        return None
    return int(match.group(1) or match.group(2))


def upscale_image(
    src: Path | str,
    dst: Path | str,
    scale: int = 4,
    model: str = DEFAULT_MODEL,
) -> Path:
    """Upscale a single image. Output format follows ``dst``'s extension."""
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise FileNotFoundError(f"upscale source not found: {src}")
    engine, model_dir = _engine()
    _validate(scale, model)
    dst.parent.mkdir(parents=True, exist_ok=True)

    staging = None if _engine_safe(src, dst) else _workdir()
    try:
        run_src, run_dst = src, dst
        if staging is not None:
            run_src = staging / f"in{src.suffix}"
            run_dst = staging / f"out{dst.suffix}"
            shutil.copyfile(src, run_src)
        tail = _run_engine(engine, model_dir, run_src, run_dst, scale=scale, model=model)
        if not run_dst.is_file():
            raise RuntimeError(_engine_failure(f"produced no output for {src.name}", tail))
        if staging is not None:
            shutil.copyfile(run_dst, dst)
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    logger.info("upscaled %s -> %s (x%d, %s)", src.name, dst.name, scale, model)
    return dst


def height_for_scale(src: Path | str, factor: float) -> int:
    """The target height that makes ``factor`` times the source size.

    The bundled models upscale by a fixed 4x and `_validate` refuses any other
    scale — "would return silently corrupted pixels", as it puts it. So a node
    asking for x2 is not asking for a different model, it is asking for a
    different resample target, and the only way to name that target is to know
    how tall the source is.

    Rounded to an even number because `upscale_video` requires one, and H.264
    needs one.
    """
    height = _probe_height(Path(src))
    target = int(round(height * float(factor)))
    if target < 2:
        target = 2
    return target if target % 2 == 0 else target + 1


def _probe_height(src: Path) -> int:
    ffprobe = assets.ffprobe_bin()
    if ffprobe is None:
        raise RuntimeError("ffprobe not found; cannot measure the source height")
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=height", "-of", "csv=p=0", str(src),
        ],
        capture_output=True, text=True, errors="replace", timeout=PROBE_TIMEOUT_S,
    )
    text = (result.stdout or "").strip().splitlines()
    if not text or not text[0].strip().isdigit():
        raise RuntimeError(f"could not read the height of {src.name}")
    return int(text[0].strip())


def upscale_video(
    src: Path | str,
    dst: Path | str,
    scale: int = 4,
    model: str = DEFAULT_MODEL,
    target_height: Optional[int] = None,
    progress: Optional[ProgressFn] = None,
) -> Path:
    """Upscale every frame of a video, preserving fps and the original audio.

    ``target_height`` resamples after the upscale pass so callers can land on
    exactly 1080p/2160p instead of only integer multiples of the source size.
    ``progress`` is called as ``(done_frames, total_frames)``.
    """
    src, dst = Path(src), Path(dst)
    if not src.is_file():
        raise FileNotFoundError(f"upscale source not found: {src}")
    engine, model_dir = _engine()
    _validate(scale, model)
    if target_height is not None and (target_height <= 0 or target_height % 2):
        raise ValueError(f"target_height must be a positive even number, got {target_height}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    fps, has_audio, sar = _probe_video(src)
    _reject_if_too_long(src)

    workdir = _workdir()
    try:
        raw = workdir / "raw"
        big = workdir / "big"
        raw.mkdir()
        # The engine reports "invalid outputpath extension type" and exits
        # non-zero unless the output directory already exists.
        big.mkdir()

        _run_ffmpeg(
            ["-i", str(src), "-fps_mode", "passthrough", str(raw / _FRAME_PATTERN)],
            "extract frames",
        )
        total = sum(1 for _ in raw.glob("*.png"))
        if not total:
            raise RuntimeError(f"no frames extracted from {src.name}; is it a video?")

        tail = _run_engine(
            engine, model_dir, raw, big, scale=scale, model=model,
            fmt="png", total=total, progress=progress,
        )
        done = sum(1 for _ in big.glob("*.png"))
        if done != total:
            raise RuntimeError(
                _engine_failure(f"upscaled {done}/{total} frames", tail)
            )

        args = ["-framerate", fps, "-i", str(big / _FRAME_PATTERN)]
        if has_audio:
            args += ["-i", str(src)]
        filters = []
        if target_height is not None:
            # -2 keeps the aspect ratio while forcing an even width, which
            # yuv420p requires.
            filters.append(f"scale=-2:{target_height}:flags=lanczos")
        if sar:
            # PNG frames carry no pixel aspect, so an anamorphic source would
            # otherwise come back horizontally squashed.
            filters.append(f"setsar={sar}")
        if filters:
            args += ["-vf", ",".join(filters)]
        args += ["-map", "0:v:0", "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p"]
        if has_audio:
            args += ["-map", "1:a:0", "-c:a", "copy", "-shortest"]
        _run_ffmpeg(args + [str(dst)], "reassemble video")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if not dst.is_file():
        raise RuntimeError(f"upscale produced no video at {dst}")
    logger.info("upscaled video %s -> %s (%d frames, x%d)", src.name, dst.name, total, scale)
    return dst


def _engine_safe(*paths: Path) -> bool:
    """Whether the engine can be pointed straight at these paths.

    A non-ASCII component anywhere in a path kills the bundled binary with
    STATUS_STACK_BUFFER_OVERRUN (0xC0000409) before it writes anything, so such
    a job has to be staged through an ASCII directory instead.
    """
    return all(str(p).isascii() for p in paths)


def _work_root() -> Path:
    """Where frame dumps and staging copies live.

    Frame dumps run to gigabytes for even a short clip, so this stays beside
    project storage rather than on the system temp volume. Storage sitting under
    a non-ASCII path would take the engine down with it, hence the fallbacks.
    """
    candidates = (
        STORAGE_DIR / "tmp",
        Path(tempfile.gettempdir()) / "flowboard-upscale",
    )
    for root in candidates:
        if str(root).isascii():
            return root
    return Path(STORAGE_DIR.anchor) / "flowboard-upscale-tmp"


def _workdir() -> Path:
    root = _work_root()
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="upscale-", dir=root))


def _engine() -> tuple[str, Path]:
    """The engine to run, preferring RealESRGAN.

    Upscayl is a fork of the same ncnn engine and takes the same flags for
    everything used here (`-i -o -s -n -m -f`) — measured against
    `upscayl-bin -h`, not assumed. It also offers a `-z` model-scale flag
    that RealESRGAN lacks; that is deliberately NOT used, so both engines
    run the model at its native ratio and any other size comes from the same
    resample path. One behaviour beats two.
    """
    model_dir = assets.upscale_model_dir()
    binary = assets.realesrgan_bin()
    if binary is None:
        binary = assets.upscayl_bin()
        if binary is not None:
            logger.warning(
                "upscale: realesrgan-ncnn-vulkan missing, falling back to "
                "upscayl-bin — output will differ visibly from the usual engine"
            )
    if binary is None or model_dir is None:
        raise RuntimeError(
            "No upscale engine available: expected realesrgan-ncnn-vulkan or "
            f"upscayl-bin, plus models/, under {assets.UPSCALE_DIR}"
        )
    return binary, model_dir


def _validate(scale: int, model: str) -> None:
    if scale not in VALID_SCALES:
        raise ValueError(f"scale must be one of {VALID_SCALES}, got {scale!r}")
    known = models()
    if model not in known:
        raise ValueError(f"unknown upscale model {model!r}; available: {known}")
    native = model_scale(model)
    if native is not None and scale != native:
        raise ValueError(
            f"model {model!r} upscales by {native}x only; scale={scale} would "
            f"return silently corrupted pixels. Upscale at {native} and resample "
            "afterwards (upscale_video does that via target_height)."
        )


def _run_engine(
    engine: str,
    model_dir: Path,
    src: Path,
    dst: Path,
    *,
    scale: int,
    model: str,
    fmt: Optional[str] = None,
    total: int = 0,
    progress: Optional[ProgressFn] = None,
) -> str:
    """Run the engine, streaming per-frame progress. Returns its stderr tail.

    A zero exit status is not proof of success — the engine exits 0 when it
    fails to decode an input — so every caller re-checks its own output.
    """
    argv = [
        engine,
        "-i", str(src),
        "-o", str(dst),
        "-s", str(scale),
        "-n", model,
        "-m", str(model_dir),
    ]
    if fmt:
        argv += ["-f", fmt]

    tail: deque[str] = deque(maxlen=_STDERR_TAIL_LINES)
    done = 0
    last_activity = time.monotonic()
    stalled = False
    stop_watchdog = threading.Event()

    def _watchdog(p: subprocess.Popen) -> None:
        """Kill an engine that has stopped emitting progress.

        ``for line in proc.stderr`` blocks forever if the child neither
        writes nor exits — a Vulkan device that never returns does exactly
        that — and this call is holding a thread from the app-wide pool.
        """
        nonlocal stalled
        while not stop_watchdog.wait(ENGINE_STALL_TIMEOUT_S / 10):
            if time.monotonic() - last_activity > ENGINE_STALL_TIMEOUT_S:
                stalled = True
                p.kill()
                return

    with subprocess.Popen(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
    ) as proc:
        watcher = threading.Thread(
            target=_watchdog, args=(proc,), name="upscale-watchdog", daemon=True
        )
        watcher.start()
        try:
            for raw_line in proc.stderr:
                last_activity = time.monotonic()
                line = raw_line.strip()
                if not line:
                    continue
                if _PROGRESS_LINE.fullmatch(line):
                    done += 1
                    if progress is not None and total:
                        progress(min(done, total), total)
                    continue
                tail.append(line)
        except BaseException:
            # A caller aborting from its progress callback must not leave the
            # engine running against a work directory that is about to vanish.
            proc.kill()
            raise
        finally:
            stop_watchdog.set()
            watcher.join(timeout=1.0)
        code = proc.wait()
    tail_text = "\n".join(tail)
    if stalled:
        raise RuntimeError(
            _engine_failure(
                f"stopped reporting progress for {ENGINE_STALL_TIMEOUT_S:.0f}s "
                "and was killed",
                tail_text,
            )
        )
    if code != 0:
        raise RuntimeError(_engine_failure(f"exited with status {code}", tail_text))
    if progress is not None and total:
        progress(total, total)
    return tail_text


def _reject_if_too_long(src: Path) -> None:
    """Refuse a source whose frame explosion would fill the disk.

    Every frame is written to PNG before the engine runs: a 10-minute 1080p
    clip is ~18,000 files and over 100 GB into STORAGE_DIR/tmp, and nothing
    downstream checks free space. Failing here with a usable message beats
    failing halfway through with a full disk.
    """
    ffprobe = assets.ffprobe_bin()
    if ffprobe is None:
        return  # duration unknown; the pipeline will fail on its own terms
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "csv=p=0",
                str(src),
            ],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=PROBE_TIMEOUT_S,
        )
        duration = float((result.stdout or "").strip())
    except (subprocess.TimeoutExpired, ValueError):
        return  # unreadable duration is not itself a reason to refuse
    if duration > MAX_UPSCALE_DURATION_S:
        raise ValueError(
            f"{src.name} is {duration:.0f}s long; video upscaling is capped at "
            f"{MAX_UPSCALE_DURATION_S:.0f}s because every frame is written to "
            "disk first. Cut it into shorter segments and upscale those."
        )


def _engine_failure(reason: str, tail: str) -> str:
    return f"RealESRGAN {reason}\n--- engine stderr ---\n{tail or '(empty)'}"


def _run_ffmpeg(args: Sequence[str], step: str) -> None:
    ffmpeg = assets.ffmpeg_bin()
    if ffmpeg is None:
        raise RuntimeError(f"ffmpeg not found; cannot {step}")
    try:
        result = subprocess.run(
            [ffmpeg, "-v", "error", "-y", *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=FFMPEG_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"ffmpeg timed out after {FFMPEG_TIMEOUT_S:.0f}s trying to {step}"
        ) from exc
    if result.returncode != 0:
        tail = "\n".join(result.stderr.strip().splitlines()[-_STDERR_TAIL_LINES:])
        raise RuntimeError(
            f"ffmpeg failed to {step} (status {result.returncode})\n"
            f"--- ffmpeg stderr ---\n{tail or '(empty)'}"
        )


def _usable_rate(value: str) -> bool:
    numerator, _, denominator = value.partition("/")
    try:
        return int(numerator) > 0 and int(denominator) > 0
    except ValueError:
        return False


def _probe_video(src: Path) -> tuple[str, bool, str]:
    """Return the frame rate (exact rational), audio flag and pixel aspect."""
    ffprobe = assets.ffprobe_bin()
    if ffprobe is None:
        raise RuntimeError("ffprobe not found; cannot inspect the source video")

    def _probe(*args: str) -> str:
        try:
            result = subprocess.run(
                [ffprobe, "-v", "error", *args, str(src)],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=PROBE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"ffprobe timed out after {PROBE_TIMEOUT_S:.0f}s on {src.name}"
            ) from exc
        if result.returncode != 0:
            tail = "\n".join(result.stderr.strip().splitlines()[-_STDERR_TAIL_LINES:])
            raise RuntimeError(
                f"ffprobe failed on {src.name} (status {result.returncode})\n"
                f"--- ffprobe stderr ---\n{tail or '(empty)'}"
            )
        return result.stdout.strip()

    def _stream(entry: str, select: str = "v:0") -> str:
        return _probe(
            "-select_streams", select,
            "-show_entries", f"stream={entry}",
            "-of", "csv=p=0",
        )

    # Rationals are passed through to ffmpeg verbatim so 30000/1001 stays exact.
    # avg_frame_rate is frames-over-duration, so rebuilding a variable-rate
    # source at a constant rate keeps its real running time; r_frame_rate is
    # only the base tick and would stretch such a clip (measured: a 0.933 s
    # clip came back as 1.000 s).
    fps = _stream("avg_frame_rate")
    if not _usable_rate(fps):
        fps = _stream("r_frame_rate")
    if not _usable_rate(fps):
        raise RuntimeError(f"no video stream with a usable frame rate in {src.name}")

    sar = _stream("sample_aspect_ratio").replace(":", "/")
    if sar in {"", "N/A", "0/1", "1/1"}:
        sar = ""

    has_audio = bool(_stream("codec_type", select="a"))
    return fps, has_audio, sar
