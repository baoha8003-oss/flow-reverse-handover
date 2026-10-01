"""Long narration, synthesised a segment at a time and rejoined.

`tts.synthesize` sends the whole script in one request, which is fine for a
caption and wrong for a ten-minute story: one call, one timeout, one failure,
and the entire narration is lost.

The packaged tool solves this by splitting the text, synthesising each piece,
retrying pieces individually and merging with a pause between them — keeping
a ``segments.json`` manifest so a single bad segment can be redone without
paying for the other forty. That structure is the good idea and it is what
this module reproduces.

**What is deliberately NOT reproduced.** The tool's own segment worker
(`_clone_segment_with_retry`) drives a third-party voice-cloning service:
upload a reference sample, `POST /jobs`, poll for `COMPLETED`, fetch
`outputUrl` — over `curl_cffi`, a browser-impersonating HTTP client. That is
the same class of thing as the audio-scraping extension already set aside for
a decision, and it is not built here. The segmentation machinery is engine-
agnostic, so it runs on the Gemini voice this app already has a legitimate
key for, and gains nothing from where the audio came from.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

#: Characters per request. Chosen to sit well inside any one call rather than
#: to be maximal: the whole point is that a failure costs one segment.
MAX_SEGMENT_CHARS = 1200

#: Gap between segments. Synthesisers end a part on the last syllable rather
#: than on a breath, so joining with none runs the sentences together.
DEFAULT_PAUSE_S = 0.35

#: How many times one segment is retried before the run gives up on it. The
#: other segments still finish — a manifest with one hole beats no audio.
MAX_ATTEMPTS = 3

#: Sentence enders, including the Vietnamese-heavy usage of `…`.
#: The closing-punctuation class is not decoration. A sentence that ends inside
#: quotation marks — ``Anh nói: "Tôi không biết."`` — puts the period BEFORE the
#: quote, so a lookbehind on the terminator alone never matched and no split
#: happened. On a dialogue-heavy script, which is most of what this app
#: narrates, that meant no segmentation at all: one request, one timeout, the
#: whole story lost — the exact failure this module exists to prevent.
_CLOSERS = "\"'”’»)]"
_SENTENCE_END = re.compile(
    r"(?<=[.!?…][" + re.escape(_CLOSERS) + r"])\s+"
    r"|(?<=[.!?…])\s+"
    r"|\n+"
)


class NarrationError(RuntimeError):
    pass


@dataclass
class Segment:
    index: int
    text: str
    status: str = "pending"       # pending | done | failed
    media_id: Optional[str] = None
    path: Optional[str] = None
    attempts: int = 0
    error: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "text": self.text,
            "status": self.status,
            "mediaId": self.media_id,
            "path": self.path,
            "attempts": self.attempts,
            "error": self.error,
        }


@dataclass
class Manifest:
    """What was asked for, what came back, and what still has not.

    Written next to the output so a later run can redo one segment. Without
    it "regenerate segment 12" would mean re-synthesising the whole script.

    `voice` and `engine` are part of "what was asked for", and they are here
    because a partial re-read SPLICES new audio into old. Text drifting between
    the two reads is the obvious way that goes wrong; the voice changing is the
    same failure through a different door — segment 7 in a different voice, in
    the middle of a finished narration — and nothing could detect it while the
    manifest did not record one. `None` on a record written before this existed,
    which the caller reports as "cannot tell" rather than refusing outright.
    """

    segments: list[Segment] = field(default_factory=list)
    output: Optional[str] = None
    pause_s: float = DEFAULT_PAUSE_S
    voice: Optional[str] = None
    engine: Optional[str] = None

    @property
    def failed(self) -> list[Segment]:
        return [s for s in self.segments if s.status != "done"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "segments": [s.as_dict() for s in self.segments],
            "output": self.output,
            "pauseSeconds": self.pause_s,
            "failedCount": len(self.failed),
            "voice": self.voice,
            "engine": self.engine,
        }

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.as_dict(), ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        return path

    @classmethod
    def read(cls, path: Path) -> "Manifest":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise NarrationError(f"không đọc được manifest: {exc}") from exc
        segments = [
            Segment(
                index=int(entry.get("index", i)),
                text=str(entry.get("text") or ""),
                status=str(entry.get("status") or "pending"),
                media_id=entry.get("mediaId"),
                path=entry.get("path"),
                attempts=int(entry.get("attempts") or 0),
                error=entry.get("error"),
            )
            for i, entry in enumerate(raw.get("segments") or [])
        ]
        return cls(
            segments=segments,
            output=raw.get("output"),
            pause_s=float(raw.get("pauseSeconds") or DEFAULT_PAUSE_S),
            # Absent on a record written before the manifest carried these. Read
            # as None rather than as a default: "read by Kore" and "nobody wrote
            # it down" must not be the same answer when the next step is deciding
            # whether a re-read would change voices mid-narration.
            voice=raw.get("voice") or None,
            engine=raw.get("engine") or None,
        )


def split_text(text: str, *, max_chars: int = MAX_SEGMENT_CHARS) -> list[str]:
    """Break a script into synthesisable pieces at sentence boundaries.

    Never mid-sentence. A synthesiser reads each piece as a complete
    utterance, so cutting between "the man walked" and "into the room" puts
    a falling intonation and a breath in the middle of the thought — audible
    in a way a slightly uneven segment length is not.

    A single sentence longer than the limit is left whole rather than
    chopped: an over-long request is a failure this module handles, and a
    mangled sentence is one it would have caused.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return []

    sentences = [s.strip() for s in _SENTENCE_END.split(cleaned) if s.strip()]
    if not sentences:
        return []

    out: list[str] = []
    buffer = ""
    for sentence in sentences:
        if not buffer:
            buffer = sentence
        elif len(buffer) + 1 + len(sentence) <= max_chars:
            buffer = f"{buffer} {sentence}"
        else:
            out.append(buffer)
            buffer = sentence
    if buffer:
        out.append(buffer)
    return out


def plan(text: str, *, max_chars: int = MAX_SEGMENT_CHARS,
         pause_s: float = DEFAULT_PAUSE_S) -> Manifest:
    """The segments a script will become, before anything is synthesised."""
    pieces = split_text(text, max_chars=max_chars)
    if not pieces:
        raise NarrationError("không có nội dung để đọc")
    return Manifest(
        segments=[Segment(index=i, text=t) for i, t in enumerate(pieces)],
        pause_s=pause_s,
    )


def synthesize_manifest(
    manifest: Manifest,
    synth: Callable[[str], Path],
    *,
    only: Optional[set[int]] = None,
    max_attempts: int = MAX_ATTEMPTS,
    on_progress: Optional[Callable[["Manifest"], None]] = None,
) -> Manifest:
    """Fill in the segments, retrying each on its own.

    ``synth`` takes one segment's text and returns the audio file — injected
    rather than imported so the caller decides the engine, and so this can be
    tested without a key or a network.

    ``only`` re-runs a subset, which is what "regenerate segment 12" means.
    Segments already done are left alone: they cost money and time, and
    redoing them to fix a neighbour is the waste the manifest exists to
    prevent.

    A segment that fails every attempt is recorded and the run CONTINUES. A
    narration with one hole and a manifest saying which is far more useful
    than an exception and nothing.

    ``on_progress`` is called with the manifest after every segment settles,
    which is how the record survives a failure. Called at the end instead, it
    was never written on the one run that needed it: the caller cannot merge a
    manifest with holes, so it returned an error and the finished segments
    became temporary files with nothing pointing at them.
    """
    for segment in manifest.segments:
        if only is not None and segment.index not in only:
            continue
        if only is None and segment.status == "done":
            continue

        segment.error = None
        for attempt in range(1, max_attempts + 1):
            segment.attempts = attempt
            try:
                produced = synth(segment.text)
            except Exception as exc:
                segment.error = str(exc)[:200]
                logger.warning(
                    "narration: segment %d attempt %d failed — %s",
                    segment.index, attempt, segment.error,
                )
                continue
            segment.path = str(produced)
            segment.status = "done"
            segment.error = None
            break
        else:
            segment.status = "failed"
            logger.warning(
                "narration: segment %d gave up after %d attempt(s)",
                segment.index, max_attempts,
            )
        if on_progress is not None:
            on_progress(manifest)
    return manifest


def merge(manifest: Manifest, dst: Path) -> Path:
    """Join the finished segments in order, with the pause between them.

    Refuses a manifest with holes. Merging around a failed segment would
    produce a narration that is missing a sentence and sounds complete —
    the listener has no way to tell, and neither does the board.
    """
    from flowboard.services import postprod

    if not manifest.segments:
        raise NarrationError("manifest không có đoạn nào")
    missing = manifest.failed
    if missing:
        raise NarrationError(
            "còn "
            + str(len(missing))
            + " đoạn chưa đọc được ("
            + ", ".join(str(s.index) for s in missing[:5])
            + ") — ghép bây giờ sẽ ra bản thiếu câu mà nghe vẫn như đủ"
        )

    parts = [Path(s.path) for s in sorted(manifest.segments, key=lambda s: s.index) if s.path]
    if not parts:
        raise NarrationError("không có file đoạn nào để ghép")
    out = postprod.concat_audio(parts, dst, pause_s=manifest.pause_s)
    manifest.output = str(out)
    return out
