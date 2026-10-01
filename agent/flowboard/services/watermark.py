"""Remove the generator's watermark from an image or a clip.

The packaged tool ships two third-party executables for this and drives them
as subprocesses. They are MIT licensed (AllenK / Kwyshell, "GeminiWatermarkTool"
v0.3.2) and sit in ``third_party/`` beside the tool; this module is the same
integration, done here.

**They are not interchangeable, and that is the first thing to know.**
Measured on 2026-09-02 against a real Veo clip and one of its frames:

* ``GeminiWatermarkTool-Video.exe`` found ``Veo-text 23x10`` and cleaned all
  192 frames in 1.7s on the GPU, keeping the audio track and the 720x1280
  frame size. The "Veo" mark is gone and the sky behind it reconstructed.
* ``gwt-mini.exe`` on the *same frame* reported ``No watermark detected (3%)``.
  It looks for a specific 48x48 / 96x96 alpha pattern — the Gemini image
  watermark — and the Veo text logo is simply not that thing.

So: video goes to the video build, stills go to the mini build, and neither
substitutes for the other.

**How this differs from what `postprod.remove_logo` does.** That runs ffmpeg's
`delogo`, which blurs a rectangle the caller nominates, and `zoom_out_logo`,
which crops the frame edge away. Both are the packaged tool's own
`veo_logo_method` and both leave a mark: a smudge, or a changed composition.
These executables detect the watermark and inpaint it. Keep both — the
workflow settings still select delogo/zoom, and this is the better path when
the caller wants the mark gone rather than covered.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from flowboard.services.llm.cli_utils import run_cli

logger = logging.getLogger(__name__)

#: Where the packaged tool keeps them, relative to the repo's sibling
#: ``TOOL`` directory. Resolved at call time, not import time, so a machine
#: without them still starts.
_VIDEO_TOOL = Path("third_party/gemini_video_watermark/GeminiWatermarkTool-Video.exe")
_IMAGE_TOOL = Path("third_party/gemini_watermark/gwt-mini.exe")

#: Roughly 100x realtime was measured (192 frames in 1.7s), but that was a
#: warm GPU on an 8-second clip. The ceiling is for a cold start on a long
#: clip with no GPU, where the same work falls back to the CPU.
VIDEO_TIMEOUT_S = 900.0
IMAGE_TIMEOUT_S = 120.0


class WatermarkError(RuntimeError):
    """The tool could not be run, or refused the file.

    Not raised when the tool ran fine and found nothing to remove — that is
    a normal outcome, reported by `Result.detected` being False.
    """


class Result:
    """What a run produced.

    ``detected`` is the distinction that matters. The tool exits cleanly and
    writes no output when it finds no watermark, and treating that as failure
    would turn "this clip was already clean" into an error the user has to
    interpret.
    """

    def __init__(self, path: Path, *, detected: bool, note: str = "") -> None:
        self.path = path
        self.detected = detected
        self.note = note

    def __repr__(self) -> str:  # pragma: no cover — debugging aid
        return f"Result(path={self.path!s}, detected={self.detected})"


def _tool_root() -> Path:
    """Where the packaged tool's ``third_party`` lives.

    The executables ship with the packaged tool rather than with this repo,
    so the path is resolved relative to it. Kept in one function because it
    is the single assumption about layout in this module.
    """
    from flowboard.services import assets

    return assets.DATA_DIR.parent


def _resolve(tool: Path) -> Optional[Path]:
    candidate = _tool_root() / tool
    return candidate if candidate.is_file() else None


def available() -> tuple[bool, bool]:
    """(video tool present, image tool present).

    Reported separately because they genuinely do different jobs — a build
    with only one of them can still do half the work, and saying "watermark
    removal unavailable" would be wrong.
    """
    return _resolve(_VIDEO_TOOL) is not None, _resolve(_IMAGE_TOOL) is not None


async def _run(
    tool: Path,
    src: Path,
    dst: Path,
    *,
    timeout: float,
    region: Optional[str] = None,
    threshold: Optional[float] = None,
) -> Result:
    exe = _resolve(tool)
    if exe is None:
        raise WatermarkError(
            f"watermark tool not found: {tool.name}. It ships with the packaged "
            f"tool under third_party/; this build expects it at {_tool_root() / tool}."
        )
    if not src.is_file():
        raise WatermarkError(f"input not found: {src}")

    args = [
        str(exe),
        # The tool's own help calls this out for "scripts and AI agents":
        # without it the ASCII banner lands in the captured output.
        "--no-banner",
        "-i", str(src),
        "-o", str(dst),
    ]
    if region:
        args += ["--region", region]
    if threshold is not None:
        args += ["--threshold", str(threshold)]

    try:
        result = await run_cli(args, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise WatermarkError(f"{tool.name} timed out after {timeout}s") from exc
    except (FileNotFoundError, PermissionError) as exc:
        raise WatermarkError(f"{tool.name} could not be started: {exc}") from exc

    stdout = result.stdout.decode(errors="replace")
    stderr = result.stderr.decode(errors="replace")
    combined = f"{stdout}\n{stderr}"

    # The exit contract, measured rather than assumed (both probes run on
    # 2026-09-02): a successful removal exits 0 with the output written; a
    # clean skip exits **1** with no output and says so in words. An earlier
    # version of this comment claimed skip exited 0 — it does not.
    if result.returncode == 0 and dst.is_file() and dst.stat().st_size > 0:
        return Result(dst, detected=True)

    if not dst.is_file() and (
        "No watermark detected" in combined or "[SKIP]" in combined
    ):
        logger.info("watermark: %s found nothing on %s", tool.name, src.name)
        return Result(
            src,
            detected=False,
            note="Không tìm thấy watermark — giữ nguyên file gốc.",
        )

    # Everything else is a failure. If the tool died mid-encode it can leave
    # a truncated file at the output path — and an earlier version of this
    # function checked for the file BEFORE the exit code, which reported
    # exactly that half-written clip as a successful clean. Remove it so
    # nothing downstream can pick it up.
    if dst.is_file():
        dst.unlink(missing_ok=True)
    raise WatermarkError(
        f"{tool.name} failed (exit {result.returncode}): {_last_line(combined)}"
    )


def _last_line(text: str) -> str:
    """The most informative line of a run, for an error message.

    The tools log progress bars and per-frame debug lines; the reason a run
    failed is at the end, and the first 200 characters would be the banner.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1][:200] if lines else "no output"


async def remove_from_video(
    src: Path,
    dst: Path,
    *,
    region: Optional[str] = None,
    threshold: Optional[float] = None,
    timeout: float = VIDEO_TIMEOUT_S,
) -> Result:
    """Clean a clip. Keeps the audio track and the frame size.

    ``region`` accepts the tool's own syntax — ``x,y,w,h``, ``br:mx,my,w,h``
    or ``br:auto`` — which is why the packaged workflows' ``veo_logo_*``
    percentages can drive it. Left unset, the tool detects the mark itself,
    which is what it is good at.
    """
    return await _run(
        _VIDEO_TOOL, src, dst, timeout=timeout, region=region, threshold=threshold
    )


async def remove_from_image(
    src: Path,
    dst: Path,
    *,
    region: Optional[str] = None,
    threshold: Optional[float] = None,
    timeout: float = IMAGE_TIMEOUT_S,
) -> Result:
    """Clean a still.

    Tuned for the Gemini image watermark. It will NOT find the Veo text logo
    on a video frame — measured, not assumed — so do not route frames here as
    a cheap substitute for `remove_from_video`.
    """
    return await _run(
        _IMAGE_TOOL, src, dst, timeout=timeout, region=region, threshold=threshold
    )


def copy_untouched(src: Path, dst: Path) -> Path:
    """Place the original at the output path.

    For the caller that wants one output path regardless of whether anything
    was removed. Separate from `_run` so that "nothing was detected" never
    silently looks like "cleaned".
    """
    shutil.copy2(src, dst)
    return dst
