"""Paint a watermark out of a frame with MI-GAN, on the CPU.

The last resort in the watermark chain. `GeminiWatermarkTool-Video.exe` runs
first and is much better at this: it *detects* the mark and cleans a whole
clip in seconds on the GPU. But it can decline — measured on 2026-09-02, its
still-image sibling answered "No watermark detected (3%)" on a frame that
plainly carried a Veo logo — and until now that left only `delogo`, which
blurs a rectangle and leaves a visible smudge.

`migan_pipeline_v2.onnx` ships beside that executable and its NOTICE says
exactly what it is for: loaded with ONNX Runtime's CPU provider "only as a
fallback when the adaptive alpha/background cleanup is unavailable and the
video engine skipped removal". This module is that fallback, done here.

**Everything below was measured against the model, not assumed.**

* IO contract: ``image`` uint8 ``[N,3,H,W]`` and ``mask`` uint8 ``[N,1,H,W]``
  in, ``result`` uint8 ``[N,3,H,W]`` out.
* **Mask polarity: 0 marks the hole, 255 marks keep.** Tested by masking a
  red square on a blue field both ways — with 0 inside, the square was
  erased (channel mean 255 → 12) and the background survived untouched; with
  255 inside, the square survived and the *background* was repainted. This
  was the one convention that could not be read off the file, so it was run.
* **Cost is ~700 ms a frame no matter the input size** (measured at 128²,
  256², 512² and 720x1280). The pipeline resizes to its native 512 inside,
  so a bigger input buys nothing and costs the same.
* **Pixels outside the mask are NOT preserved** — up to 24 levels of drift
  across a whole 720x1280 frame. That is the finding that shapes this
  module: handing over the entire frame would quietly degrade every pixel to
  clean a logo in one corner.

So the work is done on a **tile** cropped around the mark, and only the hole
plus a few pixels of cross-fade around it are written back. Everything
further out stays bit-identical, which no whole-frame call could promise. A
box too big for one tile is split into a grid of pieces, each painted into
the output of the last.

Optional by construction: `onnxruntime` and `numpy` are not dependencies of
this app — `routes/upload` sniffs PNG and JPEG headers by hand rather than
pull in Pillow — so this module reports itself unavailable when they are
absent and nothing else changes.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from flowboard.services import assets

logger = logging.getLogger(__name__)

#: Model tile. The pipeline resizes internally, so this is about how much
#: context the mark gets rather than about speed — a 512 tile around a small
#: logo keeps far more real resolution on it than a whole frame squeezed to
#: the same 512.
TILE = 512

#: The most hole painted in one pass. Half the tile, so the tile centred on a
#: piece always keeps real picture around it — the model fills a hole from
#: what surrounds it, and a hole filling its own tile has nothing to fill
#: from. A box wider or taller than this is split into a grid of pieces.
MAX_HOLE = TILE // 2

#: Pixels the hole is grown by before painting. **Measured**: the model
#: leaves roughly two pixels of the mask's own border where it was, so a mark
#: that reaches the edge of the nominated box came back as a bright rim —
#: 224/255 of the original red in the probe, against 9 once the hole is grown
#: by 3-4 px. Identical at 4, 6 and 8, so 4 with a margin and no more; every
#: pixel here is real picture being repainted for nothing.
GROW = 4

#: Pixels of cross-fade in a ring OUTSIDE the painted hole. The hole itself is
#: replaced whole: a ramp inside it would keep the outer pixels of the box as
#: they were, and those are the pixels the mark's own edge sits on.
FEATHER = 6

#: Model runs, not frames: a box needing a 2x3 grid costs six of them per
#: frame. At the measured ~0.7s each, 1200 is about fourteen minutes — past
#: that the honest answer is "this needs the GPU tool", not a progress bar.
MAX_PASSES = 1200


class InpaintError(RuntimeError):
    pass


@dataclass(frozen=True)
class Box:
    """The watermark rectangle in pixels, clamped to the frame."""

    x: int
    y: int
    w: int
    h: int

    @classmethod
    def from_pct(
        cls, *, x_pct: float, y_pct: float, w_pct: float, h_pct: float,
        width: int, height: int,
    ) -> "Box":
        """The packaged tool stores the box as percentages of the frame.

        Pixels cannot be computed where those settings live — the frame size
        is not known until the file is probed — so the conversion happens
        here, next to the thing that needs pixels.
        """
        x = int(round(width * x_pct / 100.0))
        y = int(round(height * y_pct / 100.0))
        w = int(round(width * w_pct / 100.0))
        h = int(round(height * h_pct / 100.0))
        x = max(0, min(x, width - 1))
        y = max(0, min(y, height - 1))
        w = max(1, min(w, width - x))
        h = max(1, min(h, height - y))
        return cls(x, y, w, h)


def model_path() -> Optional[Path]:
    """The bundled MI-GAN pipeline, or None.

    It ships inside the video tool's own folder, so it is present exactly
    when that tool is — which is why this is a fallback for the tool
    DECLINING, not for the tool being missing.
    """
    candidate = (
        assets.DATA_DIR.parent
        / "third_party"
        / "gemini_video_watermark"
        / "migan_pipeline_v2.onnx"
    )
    return candidate if candidate.is_file() else None


def runtime_available() -> bool:
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def available() -> bool:
    """Both the model and the runtime. Either alone can do nothing."""
    return model_path() is not None and runtime_available()


def unavailable_reason() -> Optional[str]:
    """Why it cannot run, for a message someone can act on."""
    if model_path() is None:
        return "thiếu migan_pipeline_v2.onnx trong third_party/gemini_video_watermark"
    if not runtime_available():
        return (
            "thiếu onnxruntime/numpy — cài thêm: pip install 'flowboard[inpaint]'"
        )
    return None


_session = None


def _load():
    """One session, reused. Building it is the slow part, not the inference."""
    global _session
    if _session is not None:
        return _session
    problem = unavailable_reason()
    if problem:
        raise InpaintError(problem)
    import onnxruntime as ort

    _session = ort.InferenceSession(
        str(model_path()), providers=["CPUExecutionProvider"]
    )
    return _session


def _tile_bounds(box: Box, width: int, height: int) -> tuple[int, int, int, int]:
    """A TILE-sized window centred on the box, kept inside the frame.

    Clamped rather than padded: a tile that runs off the edge would be fed
    black pixels the model would then treat as real context, and the fill
    would lean towards them.
    """
    size = min(TILE, width, height)
    cx = box.x + box.w // 2
    cy = box.y + box.h // 2
    x0 = max(0, min(cx - size // 2, width - size))
    y0 = max(0, min(cy - size // 2, height - size))
    return x0, y0, size, size


def _pieces(box: Box) -> list[Box]:
    """The box, split into holes small enough to be paintable.

    A single tile can only clean what fits in it with room to spare: the
    write-back was clamped to the tile, so a box wider than 512 px came back
    **partly painted and reported clean**. A 4K frame reaches that easily —
    the packaged workflow's box is 20% of the width, which is 768 px there.

    Split evenly rather than in MAX_HOLE steps with a remainder: a 1-px-wide
    last column would be given a tile of its own and painted from context the
    neighbouring piece has already changed.
    """
    cols = max(1, -(-box.w // MAX_HOLE))
    rows = max(1, -(-box.h // MAX_HOLE))
    if cols == 1 and rows == 1:
        return [box]
    out: list[Box] = []
    for r in range(rows):
        y0 = box.y + box.h * r // rows
        y1 = box.y + box.h * (r + 1) // rows
        for c in range(cols):
            x0 = box.x + box.w * c // cols
            x1 = box.x + box.w * (c + 1) // cols
            out.append(Box(x0, y0, x1 - x0, y1 - y0))
    return out


def clean_frame(frame, box: Box):
    """One RGB frame with the box painted out. ``frame`` is HxWx3 uint8.

    Returns a new array; the input is not modified.

    Pieces are painted in turn **into each other's output**, so a piece is
    filled from neighbours that have already been cleaned rather than from
    the half of the mark that is still there.
    """
    out = frame
    for piece in _pieces(box):
        out = _paint_piece(out, piece, box)
    return out if out is not frame else frame.copy()


def _hole_in_tile(box: Box, x0: int, y0: int, tw: int, th: int):
    """``box`` in tile coordinates, grown by GROW and clipped to the tile."""
    bx = max(0, box.x - x0 - GROW)
    by = max(0, box.y - y0 - GROW)
    bw = min(tw, box.x - x0 + box.w + GROW) - bx
    bh = min(th, box.y - y0 + box.h + GROW) - by
    return bx, by, bw, bh


def _paint_piece(frame, piece: Box, whole: Box):
    """One model run for one piece, pasted into a copy of ``frame``."""
    import numpy as np

    session = _load()
    height, width = frame.shape[:2]
    x0, y0, tw, th = _tile_bounds(piece, width, height)
    tile = frame[y0:y0 + th, x0:x0 + tw]

    # 0 marks the hole. Measured, not assumed — see the module docstring.
    # Two boxes, not one. What is MASKED is every part of the whole mark that
    # falls in this tile: the model fills a hole from what surrounds it, and
    # for a wide mark the rest of the mark is what surrounds this piece —
    # measured, left visible it got copied straight back in and the fill came
    # out the colour of the mark. What is WRITTEN BACK is only this piece.
    #
    # Both are grown by GROW, because the model leaves the last couple of
    # pixels of a hole where they were and a tight box puts the mark's own
    # edge there.
    mask = np.full((1, 1, th, tw), 255, dtype=np.uint8)
    mx, my, mw, mh = _hole_in_tile(whole, x0, y0, tw, th)
    bx, by, bw, bh = _hole_in_tile(piece, x0, y0, tw, th)
    if bw <= 0 or bh <= 0 or mw <= 0 or mh <= 0:
        return frame.copy()
    mask[0, 0, my:my + mh, mx:mx + mw] = 0

    chw = np.ascontiguousarray(tile.transpose(2, 0, 1))[None]
    painted = session.run(["result"], {"image": chw, "mask": mask})[0]
    painted = painted[0].transpose(1, 2, 0)

    # The hole is written back WHOLE, and the cross-fade sits in a ring
    # outside it. Everything past that ring stays bit-identical — the model
    # perturbs what it is given by up to 24 levels, and there is no reason to
    # pay that away from the mark.
    pad = (
        min(FEATHER, by),
        min(FEATHER, th - (by + bh)),
        min(FEATHER, bx),
        min(FEATHER, tw - (bx + bw)),
    )
    ry0, ry1 = by - pad[0], by + bh + pad[1]
    rx0, rx1 = bx - pad[2], bx + bw + pad[3]

    out = frame.copy()
    src = painted[ry0:ry1, rx0:rx1].astype(np.float32)
    dst = out[y0 + ry0:y0 + ry1, x0 + rx0:x0 + rx1].astype(np.float32)
    alpha = _blend_alpha(bh, bw, pad, np)
    out[y0 + ry0:y0 + ry1, x0 + rx0:x0 + rx1] = (
        src * alpha + dst * (1.0 - alpha)
    ).round().astype(np.uint8)
    return out


def _blend_alpha(hole_h: int, hole_w: int, pad, np):
    """1 across the hole, ramping to nearly 0 across the ring around it.

    The ramp is OUTSIDE the hole on purpose. Inside — which is where it was
    — the outermost pixels of the box kept most of their original value, and
    those are precisely the pixels the watermark's own edge sits on: the mark
    came back as a faint outline of itself, which is the artefact this module
    exists to beat.

    ``pad`` is (top, bottom, left, right): how much ring actually fits inside
    the tile. A hole against the tile edge has none on that side, and then
    there is nothing to ramp across — the paste is flush there, which is
    right, because the model's own output continues past it.
    """
    top, bottom, left, right = pad
    ys = np.ones(top + hole_h + bottom, dtype=np.float32)
    xs = np.ones(left + hole_w + right, dtype=np.float32)
    if top:
        ys[:top] = np.arange(1, top + 1, dtype=np.float32) / (top + 1)
    if bottom:
        ys[-bottom:] = np.arange(bottom, 0, -1, dtype=np.float32) / (bottom + 1)
    if left:
        xs[:left] = np.arange(1, left + 1, dtype=np.float32) / (left + 1)
    if right:
        xs[-right:] = np.arange(right, 0, -1, dtype=np.float32) / (right + 1)
    return (ys[:, None] * xs[None, :])[:, :, None]


def inpaint_video(
    src: Path,
    dst: Path,
    box_pct: dict,
    *,
    max_passes: int = MAX_PASSES,
) -> Path:
    """Paint the watermark out of every frame of a clip.

    Frames move as raw ``rgb24`` through ffmpeg pipes rather than as PNGs on
    disk: it is faster, it needs no image codec, and it keeps this module
    free of the image dependencies the rest of the app deliberately avoids.
    Audio is copied from the source, so a clip does not come back silent.
    """
    import numpy as np

    from flowboard.services import postprod

    problem = unavailable_reason()
    if problem:
        raise InpaintError(problem)

    width, height = _frame_size(src)
    box = Box.from_pct(
        x_pct=float(box_pct.get("xPct", 0.0)),
        y_pct=float(box_pct.get("yPct", 0.0)),
        w_pct=float(box_pct.get("widthPct", 0.0)),
        h_pct=float(box_pct.get("heightPct", 0.0)),
        width=width,
        height=height,
    )

    # A wide box costs one model run per piece PER FRAME, so the ceiling has
    # to be counted in runs. Counted in frames it let a 2x3 grid through at
    # six times the time it quoted.
    total = _frame_count(src)
    tiles = len(_pieces(box))
    passes = total * tiles
    if passes > max_passes:
        grid = f" x {tiles} ô" if tiles > 1 else ""
        raise InpaintError(
            f"clip có {total} khung hình{grid} = {passes} lượt vẽ, vượt trần "
            f"{max_passes} cho đường CPU (~{passes * 0.7 / 60:.0f} phút). "
            f"Dùng engine GPU cho clip dài."
        )

    ffmpeg = postprod._ffmpeg()
    frame_bytes = width * height * 3
    reader = subprocess.Popen(
        [ffmpeg, "-nostdin", "-i", str(src), "-f", "rawvideo",
         "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    writer = subprocess.Popen(
        [ffmpeg, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{width}x{height}", "-r", str(_frame_rate(src)), "-i", "-",
         "-i", str(src), "-map", "0:v", "-map", "1:a?", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", str(dst)],
        stdin=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )

    done = 0
    try:
        while True:
            raw = reader.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
            writer.stdin.write(clean_frame(frame, box).tobytes())
            done += 1
    finally:
        if writer.stdin:
            writer.stdin.close()
        reader.stdout.close()
        reader.wait(timeout=30)
        writer.wait(timeout=600)

    if done == 0:
        raise InpaintError("không đọc được khung hình nào từ clip")
    if not dst.exists() or dst.stat().st_size == 0:
        raise InpaintError("ffmpeg không ghi ra được video")
    logger.info("inpaint: %d frame(s) cleaned into %s", done, dst.name)
    return dst


def _probe(src: Path, *entries: str) -> list[str]:
    from flowboard.services import postprod

    proc = postprod._run(
        [
            postprod._ffprobe(), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=" + ",".join(entries),
            "-of", "default=nw=1:nk=1", str(src),
        ],
        what=f"probe {src.name}",
    )
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def _frame_size(src: Path) -> tuple[int, int]:
    values = _probe(src, "width", "height")
    if len(values) < 2:
        raise InpaintError(f"không đọc được kích thước khung hình của {src.name}")
    return int(values[0]), int(values[1])


def _frame_rate(src: Path) -> float:
    values = _probe(src, "r_frame_rate")
    if not values:
        return 30.0
    num, _, den = values[0].partition("/")
    try:
        rate = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 30.0
    return rate if rate > 0 else 30.0


def _frame_count(src: Path) -> int:
    """Frames, from the duration and rate rather than by decoding.

    `nb_frames` is absent or wrong on plenty of containers, and counting by
    decoding would double the work of the thing that is already the slow
    path.
    """
    from flowboard.services import postprod

    duration = postprod.probe_duration(src)
    return max(1, int(round(duration * _frame_rate(src))))
