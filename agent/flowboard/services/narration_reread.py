"""Reading one narration segment again, without re-buying the rest.

A long script is narrated segment by segment, each retried on its own, and the
manifest beside the output records which segments were paid for. That was built
so a single failed segment would not cost a re-read of the whole thing.

**It never worked.** The manifest was keyed on the request id of the run that
wrote it, every re-run of a node mints a new request row, and `postprod` errors
classify terminal so the worker never retries in-row either. So the record was
written and never read: the one situation it existed for — forty segments landed,
one failed — still cost forty segments to fix. This module is the missing half.

Two rules shape the design.

**The caller names a node, never a script.** `POST /api/requests` and the routes
around it are unauthenticated localhost endpoints, by explicit decision. An
endpoint that accepted text and a voice would be a paid text-to-speech call
anyone on this machine could make. So the script, the voice, the engine and the
pause all come back out of `postprod_plan.ops_for` — the same function the run
uses — and the request the caller gets is the one the board describes.

**A partial read splices new audio into old, so drift is refused.** The worker
holds those guards (text compared segment by segment, not by count; a recorded
voice that no longer matches; an index the manifest does not have). What this
module adds is the preview: which segments would be read, which are kept, and
what that costs — said before the button is pressed rather than after.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from sqlmodel import Session, select

from flowboard.db.models import Board, Node, Request
from flowboard.services import narration

logger = logging.getLogger(__name__)


class RereadError(RuntimeError):
    """Cannot plan a re-read. Carries the status a route should answer with."""

    def __init__(self, message: str, *, status: int = 409) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class SegmentView:
    index: int
    status: str
    chars: int
    preview: str
    attempts: int
    error: Optional[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "status": self.status,
            "chars": self.chars,
            "preview": self.preview,
            "attempts": self.attempts,
            "error": self.error,
        }


@dataclass
class Reread:
    """A planned re-read: what to dispatch, and what it costs to say so."""

    params: dict[str, Any] = field(default_factory=dict)
    segments: list[SegmentView] = field(default_factory=list)
    read: list[int] = field(default_factory=list)
    read_chars: int = 0
    kept: int = 0
    kept_chars: int = 0
    engine: Optional[str] = None
    voice: Optional[str] = None
    #: False when the manifest predates the field. The re-read is allowed — a
    #: wall in front of every older narration is worse — but the UI says so.
    voice_known: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "segments": [s.as_dict() for s in self.segments],
            "read": list(self.read),
            "readChars": self.read_chars,
            "kept": self.kept,
            "keptChars": self.kept_chars,
            "engine": self.engine,
            "voice": self.voice,
            "voiceKnown": self.voice_known,
        }


#: Where a narration's record lives, given the request that owns it. Mirrors
#: `postprod_handler._narration_manifest_path`; imported from there rather than
#: re-derived so the two cannot name different files.
def _manifest_path(request_id: int) -> Optional[Path]:
    from flowboard.worker.postprod_handler import _narration_manifest_path

    return _narration_manifest_path({"__request_id": request_id})


def _narrate_op(s: Session, node: Node) -> dict[str, Any]:
    """The narrate step this node would run, exactly as a run would build it."""
    from flowboard.services import postprod_plan
    from flowboard.services.pipeline_executor import _upstream_with_ports
    from flowboard.db.models import Edge

    edges = list(s.exec(select(Edge).where(Edge.board_id == node.board_id)))
    nodes = list(s.exec(select(Node).where(Node.board_id == node.board_id)))
    by_id = {n.id: n for n in nodes}
    upstream = _upstream_with_ports(node.id, edges, by_id)
    for op in postprod_plan.ops_for(node, upstream):
        if op.get("op") == "narrate":
            return dict(op)
    raise RereadError(
        "Node này không có bước đọc thuyết minh nào — kiểm lại kịch bản và giọng.",
        status=409,
    )


def _previous_narrate(s: Session, node_id: int) -> Request:
    """The newest narrate request for this node, whatever it ended as.

    A failed one is the normal case: that is what leaves holes. Ordered newest
    first, because a node read twice should re-read against its latest attempt.
    """
    rows = list(s.exec(
        select(Request)
        .where(Request.node_id == node_id)
        .where(Request.type == "postprod")
        .order_by(Request.id.desc())  # type: ignore[attr-defined]
    ))
    for row in rows:
        if isinstance(row.params, dict) and row.params.get("op") == "narrate":
            return row
    raise RereadError(
        "Node này chưa đọc lần nào, nên không có gì để đọc lại.", status=409
    )


def plan_reread(s: Session, board_id: int, node_id: int) -> Reread:
    """Plan a re-read of every segment that has not landed.

    Deliberately not "any segments the caller picks". The failed set is the case
    that exists — the error string already names it — and a picker would let a
    user re-buy a segment that is already paid for. If cherry-picking is ever
    wanted, it belongs behind a control that says what it costs.
    """
    if s.get(Board, board_id) is None:
        raise RereadError(f"không có board {board_id}", status=404)
    node = s.get(Node, node_id)
    if node is None or node.board_id != board_id:
        raise RereadError(f"không có node {node_id} trên board này", status=404)
    if node.type != "create_voice":
        raise RereadError(
            f"chỉ node tạo giọng đọc mới đọc lại theo đoạn được (node này là "
            f"“{node.type}”)",
            status=409,
        )

    op = _narrate_op(s, node)
    previous = _previous_narrate(s, node_id)
    # A chain of re-reads keeps pointing at the FIRST run's record, so the paid
    # segments stay findable however many times this is pressed.
    origin = previous.params.get("manifestRequestId")
    if isinstance(origin, bool) or not isinstance(origin, int):
        origin = previous.id
    path = _manifest_path(origin)
    if path is None or not path.exists():
        raise RereadError(
            "Lần đọc trước không để lại bản ghi theo đoạn — phải đọc lại toàn bộ.",
            status=409,
        )
    try:
        manifest = narration.Manifest.read(path)
    except narration.NarrationError as exc:
        raise RereadError(f"không đọc được bản ghi: {exc}", status=409) from None

    # The script as it stands now, segmented the same way. Compared here so the
    # preview can refuse before the user presses anything; the worker compares
    # again, because it is the one that would do the splicing.
    text = str(op.get("text") or "").strip()
    if not text:
        raise RereadError("Node này không còn kịch bản nào.", status=409)
    planned = narration.plan(text)
    if [x.text for x in planned.segments] != [x.text for x in manifest.segments]:
        raise RereadError(
            "Kịch bản đã đổi so với lần đọc trước, nên không ghép đoạn mới vào "
            "đoạn cũ được — phải đọc lại toàn bộ.",
            status=409,
        )

    views = [
        SegmentView(
            index=seg.index,
            status=seg.status,
            chars=len(seg.text),
            preview=seg.text[:80],
            attempts=seg.attempts,
            error=seg.error,
        )
        for seg in manifest.segments
    ]
    failed = [v for v in views if v.status != "done"]
    if not failed:
        raise RereadError(
            "Mọi đoạn đều đã đọc xong — không có đoạn nào cần đọc lại.", status=409
        )
    done = [v for v in views if v.status == "done"]

    params = dict(op)
    params["onlySegments"] = [v.index for v in failed]
    params["manifestRequestId"] = origin
    return Reread(
        params=params,
        segments=views,
        read=[v.index for v in failed],
        read_chars=sum(v.chars for v in failed),
        kept=len(done),
        kept_chars=sum(v.chars for v in done),
        engine=manifest.engine,
        voice=manifest.voice,
        voice_known=manifest.voice is not None,
    )
