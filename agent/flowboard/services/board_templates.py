"""Saving a board as a reusable template, and reading one back.

The packaged tool's nine samples and the user's own `Workflows/` folder are
**read-only** — they belong to the exe and this build only borrows them. What
was missing was somewhere to put a board *this* app built, so a canvas someone
arranged and tuned could be used twice. That lives in ``storage/templates``.

**Why a native format instead of the exe's.** The obvious move is to save in
the exe's ``{nodes, connections}`` shape so everything is one format. It does
not work, and the reason is worth writing down: ``NODE_TYPE_MAP`` is
many-to-one — four exe prompt types collapse into one ``prompt`` node, three
list types into ``visual_asset`` — and five of this canvas's types
(``character``, ``note``, ``Storyboard``, ``review_video``,
``remove_watermark``) have no exe counterpart at all. Saving through that
converter would quietly drop nodes from the user's own board. A save that
loses work is worse than no save.

So personal templates carry the board verbatim, and the reader accepts both:
a payload with ``connections`` is the exe's shape and goes through
``template_import``; one with our ``format`` marker is read here. That also
means "import a JSON file" accepts a workflow exported from the exe, which is
the case a user is most likely to have on disk.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlmodel import select

from flowboard.config import STORAGE_DIR
from flowboard.db.models import Board, Edge, Node, Plan
from flowboard.short_id import generate_unique_short_id

logger = logging.getLogger(__name__)

#: Templates this app saved. The only writable template root — the packaged
#: samples and the exe's own `Workflows/` are never written to or deleted.
MY_TEMPLATE_DIR = STORAGE_DIR / "templates"

#: Marker for our own shape, versioned so a later change can be detected
#: rather than guessed at.
FORMAT = "flowboard/board@1"

#: Bounds on what may be written. A template is a file name and a document
#: someone will open later; neither should be unbounded.
MAX_TEMPLATE_BYTES = 4 * 1024 * 1024
MAX_NAME_CHARS = 120
MAX_DESCRIPTION_CHARS = 400

_UNSAFE = re.compile(r"[^0-9A-Za-zÀ-ỹ _.\-]")


class TemplateError(RuntimeError):
    """Something about the template is wrong. ``status`` is the HTTP answer."""

    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# ── file names ────────────────────────────────────────────────────────


def slugify(name: str) -> str:
    """A file name from a template name, without inventing one.

    Diacritics are kept: these names are Vietnamese and stripping them makes
    "Kịch bản" and "Kich ban" the same file, which is a collision the user
    did not ask for. Only characters that a path cannot safely hold are
    removed.
    """
    cleaned = _UNSAFE.sub(" ", (name or "").strip())
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if not cleaned:
        raise TemplateError("tên mẫu không dùng được")
    return cleaned[:MAX_NAME_CHARS]


def _unique_path(stem: str) -> Path:
    """``<stem>.json``, or ``<stem> (2).json`` when taken.

    Overwriting silently is the alternative, and it loses a template the user
    saved earlier under the same name without ever saying so.
    """
    MY_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    candidate = MY_TEMPLATE_DIR / f"{stem}.json"
    n = 2
    while candidate.exists():
        candidate = MY_TEMPLATE_DIR / f"{stem} ({n}).json"
        n += 1
        if n > 999:
            raise TemplateError("quá nhiều mẫu trùng tên")
    return candidate


def safe_mine_path(file: str) -> Path:
    """Resolve a personal template, refusing anything outside the folder.

    Same shape as `routes/templates._safe_template_path`, but this one guards
    a folder that can be WRITTEN to and DELETED from, so the check is what
    stands between a file name in a request and an arbitrary path.
    """
    name = Path(file).name
    if not name.endswith(".json"):
        raise TemplateError("tên mẫu phải kết thúc bằng .json")
    root = MY_TEMPLATE_DIR.resolve()
    path = (MY_TEMPLATE_DIR / name).resolve()
    if root not in path.parents:
        raise TemplateError("tên mẫu không hợp lệ")
    if not path.is_file():
        raise TemplateError(f"không có mẫu tên {name}", status=404)
    return path


# ── reading the folder ────────────────────────────────────────────────


def read(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TemplateError(f"không đọc được mẫu: {exc}") from exc
    if not isinstance(raw, dict):
        raise TemplateError("mẫu không phải một object")
    return raw


def list_mine() -> list[dict]:
    """Every personal template, newest first — a saved-from-canvas list is
    read as history, so the one just saved belongs at the top."""
    if not MY_TEMPLATE_DIR.is_dir():
        return []
    out: list[dict] = []
    for path in MY_TEMPLATE_DIR.glob("*.json"):
        try:
            raw = read(path)
        except TemplateError:
            # Unreadable but present: listed with what is known, not hidden.
            # A template that vanishes from the list is a template the user
            # cannot delete either.
            out.append({
                "file": path.name, "name": path.stem,
                "description": "⚠️ file hỏng, không đọc được", "stepCount": 0,
                "savedAt": "",
            })
            continue
        nodes = raw.get("nodes")
        out.append({
            "file": path.name,
            "name": str(raw.get("name") or path.stem),
            "description": str(raw.get("description") or ""),
            "stepCount": len(nodes) if isinstance(nodes, list) else 0,
            "savedAt": str(raw.get("savedAt") or ""),
        })
    out.sort(key=lambda e: (e["savedAt"], e["file"]), reverse=True)
    return out


def is_native(raw: dict) -> bool:
    return raw.get("format") == FORMAT


# ── board → template ──────────────────────────────────────────────────


def export_board(session, board_id: int, *, name: str, description: str = "") -> dict:
    """The whole board as a document, ready to write or hand to the browser.

    Node ids become positions in the list rather than being carried over:
    database ids mean nothing in another database, and a template that
    remembers them is a template that only imports correctly once.
    """
    board = session.get(Board, board_id)
    if board is None:
        raise TemplateError(f"không có board {board_id}", status=404)

    nodes = list(session.exec(select(Node).where(Node.board_id == board_id)).all())
    edges = list(session.exec(select(Edge).where(Edge.board_id == board_id)).all())
    index = {n.id: i for i, n in enumerate(nodes)}

    return {
        "format": FORMAT,
        "name": (name or board.name or "Mẫu").strip()[:MAX_NAME_CHARS],
        "description": (description or "").strip()[:MAX_DESCRIPTION_CHARS],
        "savedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nodes": [
            {
                "i": index[n.id],
                "type": n.type,
                "x": n.x, "y": n.y, "w": n.w, "h": n.h,
                "data": _clean_node_data(n.data or {}),
            }
            for n in nodes
        ],
        "edges": [
            {
                "from": index[e.source_id],
                "to": index[e.target_id],
                "kind": e.kind,
                "sourcePort": e.source_port,
                "targetPort": e.target_port,
                "sourceVariantIdx": e.source_variant_idx,
            }
            for e in edges
            if e.source_id in index and e.target_id in index
        ],
    }


#: Node data that describes ONE run rather than the recipe. A template is the
#: arrangement, not its output — carrying these would make every board created
#: from the template claim to already hold media it does not have, and the
#: canvas would render thumbnails for another board's images.
_RUN_ONLY_KEYS = frozenset({
    "mediaId", "mediaIds", "slotErrors", "status", "error", "renderedAt",
    "thumbnailUrl", "reviewRounds", "aiBrief", "aiBriefStatus",
    "autoPromptStatus", "partial_error",
})


def _clean_node_data(data: dict) -> dict:
    return {k: v for k, v in data.items() if k not in _RUN_ONLY_KEYS}


def save_board_as_template(session, board_id: int, *, name: str, description: str = "") -> dict:
    doc = export_board(session, board_id, name=name, description=description)
    path = _unique_path(slugify(doc["name"]))
    _write(path, doc)
    logger.info("template saved: %s (%d nodes)", path.name, len(doc["nodes"]))
    return {"file": path.name, **doc}


def _write(path: Path, doc: dict) -> None:
    payload = json.dumps(doc, ensure_ascii=False, indent=1)
    if len(payload.encode("utf-8")) > MAX_TEMPLATE_BYTES:
        raise TemplateError("mẫu quá lớn")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def update_meta(file: str, *, name: Optional[str], description: Optional[str]) -> dict:
    """Rename / re-describe. The FILE keeps its name on purpose.

    Renaming the file too would break any link to it and would need the same
    collision handling as a fresh save, for no benefit: the list shows
    ``name``, not the file.
    """
    path = safe_mine_path(file)
    doc = read(path)
    if name is not None:
        cleaned = name.strip()
        if not cleaned:
            raise TemplateError("tên mẫu không được rỗng")
        doc["name"] = cleaned[:MAX_NAME_CHARS]
    if description is not None:
        doc["description"] = description.strip()[:MAX_DESCRIPTION_CHARS]
    _write(path, doc)
    return {"file": path.name, "name": doc.get("name"), "description": doc.get("description")}


def replace_from_board(session, file: str, board_id: int) -> dict:
    """Overwrite a personal template with the current state of a board.

    This is "edit": the user opens the template, rearranges it, and saves back.
    Name and description survive — they were named once and the edit is about
    the graph.
    """
    path = safe_mine_path(file)
    existing = read(path)
    doc = export_board(
        session,
        board_id,
        name=str(existing.get("name") or path.stem),
        description=str(existing.get("description") or ""),
    )
    _write(path, doc)
    return {"file": path.name, **doc}


def delete(file: str) -> None:
    path = safe_mine_path(file)
    path.unlink()
    logger.info("template deleted: %s", path.name)


# ── template → board ──────────────────────────────────────────────────


def import_native(session, raw: dict, *, name: str) -> dict:
    """Rebuild a board from our own format."""
    board = Board(name=name)
    session.add(board)
    session.commit()
    session.refresh(board)
    board_id = board.id
    assert board_id is not None

    nodes_raw = raw.get("nodes")
    edges_raw = raw.get("edges")
    if not isinstance(nodes_raw, list):
        nodes_raw = []
    if not isinstance(edges_raw, list):
        edges_raw = []

    by_index: dict[int, Node] = {}
    for position, entry in enumerate(nodes_raw):
        if not isinstance(entry, dict):
            continue
        node_type = entry.get("type")
        if not isinstance(node_type, str) or not node_type:
            continue
        data = entry.get("data")
        node = Node(
            board_id=board_id,
            short_id=generate_unique_short_id(session, board_id),
            type=node_type,
            x=_num(entry.get("x"), 0.0),
            y=_num(entry.get("y"), 0.0),
            w=_num(entry.get("w"), 240.0),
            h=_num(entry.get("h"), 160.0),
            data=_clean_node_data(data) if isinstance(data, dict) else {},
        )
        session.add(node)
        session.commit()
        session.refresh(node)
        key = entry.get("i")
        by_index[key if isinstance(key, int) else position] = node

    edges_made = 0
    dangling = 0
    for entry in edges_raw:
        if not isinstance(entry, dict):
            continue
        src = by_index.get(entry.get("from"))
        dst = by_index.get(entry.get("to"))
        if src is None or dst is None:
            dangling += 1
            continue
        kind = entry.get("kind")
        session.add(
            Edge(
                board_id=board_id,
                source_id=src.id,
                target_id=dst.id,
                kind=kind if isinstance(kind, str) and kind else "ref",
                source_port=_opt_str(entry.get("sourcePort")),
                target_port=_opt_str(entry.get("targetPort")),
                source_variant_idx=(
                    entry.get("sourceVariantIdx")
                    if isinstance(entry.get("sourceVariantIdx"), int)
                    else None
                ),
            )
        )
        edges_made += 1
    session.commit()

    # Same handle-onto-the-nodes Plan `template_import` creates, so a board
    # made from a personal template runs through the identical executor path.
    # Two importers producing two different run surfaces is how one of them
    # ends up subtly broken and nobody notices.
    plan = Plan(
        board_id=board_id,
        status="approved",
        spec={
            "nodes": [],
            "edges": [],
            "_materialized_node_ids": [n.id for n in by_index.values()],
            "_source": "board_template",
            "_template": name,
        },
    )
    session.add(plan)
    session.commit()
    session.refresh(plan)

    return {
        "board_id": board_id,
        "plan_id": plan.id,
        "name": name,
        "nodes": len(by_index),
        "nodes_in_file": len([n for n in nodes_raw if isinstance(n, dict)]),
        "edges": edges_made,
        "edges_in_file": len([e for e in edges_raw if isinstance(e, dict)]),
        "dangling_edges": dangling,
        "unsupported": [],
    }


def _num(value: Any, fallback: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return fallback
    return fallback if num != num else num


def _opt_str(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and value else None
