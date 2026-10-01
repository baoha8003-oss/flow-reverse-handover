"""The packaged tool's 9 sample workflows, served two ways.

As a **recipe**: an ordered list of what happens, which screen here does it,
and the exact prompt text — useful when you want to follow the workflow by
hand across the tabs.

As a **board**: `POST /{file}/import` builds real Node and Edge rows on a new
canvas. Sixteen of the exe's seventeen node types map onto a canvas type (see
`services/template_import.py`); the one that does not imports as a note rather
than disappearing.

An earlier version of this module argued that importing was impossible
because most node types had no counterpart. That was wrong — the counterparts
existed under different names, and the seven post-production types were added
here rather than left out. The graph, not just the prose, is the valuable part
of these files.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from flowboard.db import get_session
from flowboard.services import assets, board_templates, template_import

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/templates", tags=["templates"])

TEMPLATE_DIR = assets.DATA_DIR / "system_workflows"
INDEX_FILE = assets.DATA_DIR / "system_workflows.json"

#: The packaged tool's *personal* project folder, alongside the nine shipped
#: samples. Its "Dự án" selector lists these; `default_workflow.json` is the
#: blank board it opens with. Same `{nodes, connections}` shape as the shipped
#: templates, but no entry in `system_workflows.json`, which is why reading
#: only the index missed them entirely — a user's own saved boards were
#: invisible to this build.
USER_TEMPLATE_DIR = assets.ASSET_ROOT / "Workflows"

def _template_dirs() -> tuple[Path, ...]:
    """All three sources, packaged first.

    Order decides name collisions: a shipped sample and a personal file with
    the same name resolve to the shipped one, so a stray file in the personal
    folder cannot shadow the originals.

    A function rather than a module constant on purpose. A tuple built at
    import time would freeze whatever `TEMPLATE_DIR` pointed at then, and the
    test suite redirects these at a temp folder to isolate listing tests —
    a snapshot silently defeats that and lets the real asset library leak in.
    """
    return (TEMPLATE_DIR, USER_TEMPLATE_DIR, board_templates.MY_TEMPLATE_DIR)

# Each of the exe's node types → the screen in THIS build that does that job.
# The point of the whole feature: turn a graph you cannot run into steps you
# can follow.
STEP_GUIDE: dict[str, str] = {
    "upload_media": "Chọn ảnh đầu vào (nút chọn ảnh ở tab tương ứng)",
    "link_list": "Danh sách link đầu vào — dùng tab Video Clone để tải về",
    "video_image_list": "Danh sách ảnh/video đầu vào",
    "text_prompt": "Prompt — gõ ở tab tạo ảnh/video",
    "prompt_mau": "Prompt mẫu — chép vào ô prompt",
    "prompt_list": "Nhiều prompt, mỗi dòng một cảnh (Text to Video)",
    "gemini_prompt": "Nhờ AI viết prompt — tab Ý tưởng → Video",
    "analyze_video": "Tab Phân tích video",
    "gen_image": "Tab Text to Image / Image to Image",
    "gen_video": "Tab Text to Video / Image to Video",
    "motion_control": "Hoán đổi cử động — bản này chưa hỗ trợ, node nhập về dạng ghi chú",
    "extract_last_frame": "Cut & Merge → Cắt frame cuối",
    "merge_video": "Cut & Merge → Ghép video",
    "add_bgm": "Cut & Merge → Nhạc nền",
    "create_voice": "Cut & Merge → Lồng tiếng",
    "align_video_voice": "Cut & Merge → Lồng tiếng (khớp thời lượng)",
    "sync_image_voice": "Cut & Merge → Lồng tiếng (khớp thời lượng)",
    "edit_video": "Cut & Merge → Phụ đề / Logo / Tiêu đề",
}

# Settings fields that hold prompt-like text worth surfacing.
_TEXT_FIELDS = ("prompt", "text", "content", "value", "prompt_text", "template")
# Below this a "prompt" is a label or a filename, not something worth copying.
_MIN_PROMPT_CHARS = 40


class TemplateSummary(BaseModel):
    file: str
    name: str
    description: str
    stepCount: int
    #: ``packaged`` (the exe's nine samples) · ``tool`` (its own Workflows
    #: folder) · ``mine`` (saved from this canvas). Only the last is writable,
    #: and the UI must not offer rename/delete on the other two — they belong
    #: to the packaged tool and deleting one cannot be undone from here.
    source: str = "packaged"
    writable: bool = False


class TemplateStep(BaseModel):
    order: int
    type: str
    label: str
    doneWith: str
    prompt: Optional[str] = None


class TemplateDetail(BaseModel):
    file: str
    name: str
    description: str
    steps: list[TemplateStep]
    unmappedTypes: list[str]


def _safe_template_path(file: str) -> Path:
    """Resolve a template name, refusing anything outside the two folders.

    `Path(file).name` strips any directory part before resolution, and the
    resolved path must still sit under one of the roots — so neither a
    ``../`` in the request nor a symlink inside the folder can reach a file
    the caller was not offered.
    """
    name = Path(file).name
    if not name.endswith(".json"):
        raise HTTPException(400, "template name must end with .json")
    for root_dir in _template_dirs():
        root = root_dir.resolve()
        path = (root_dir / name).resolve()
        if root in path.parents and path.is_file():
            return path
    raise HTTPException(404, f"no template named {name}")


def _read_index() -> list[dict[str, Any]]:
    try:
        raw = json.loads(INDEX_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    return [e for e in raw if isinstance(e, dict) and e.get("file")]


def _order_nodes(nodes: list[dict], connections: list[dict]) -> list[dict]:
    """Sort nodes into execution order.

    A topological sort over the connection graph, falling back to the file's
    own order for anything unconnected. Templates are drawn left-to-right but
    the coordinates are not reliable — some of these boards were rearranged by
    hand and one starts at x = -3472.
    """
    by_id = {n["id"]: n for n in nodes if isinstance(n, dict) and n.get("id")}
    incoming: dict[str, set[str]] = {nid: set() for nid in by_id}
    outgoing: dict[str, list[str]] = {nid: [] for nid in by_id}
    for conn in connections:
        src, dst = conn.get("srcNode"), conn.get("dstNode")
        if src in by_id and dst in by_id:
            incoming[dst].add(src)
            outgoing[src].append(dst)

    ordered: list[dict] = []
    ready = [nid for nid in by_id if not incoming[nid]]
    seen: set[str] = set()
    while ready:
        nid = ready.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        ordered.append(by_id[nid])
        for nxt in outgoing[nid]:
            incoming[nxt].discard(nid)
            if not incoming[nxt] and nxt not in seen:
                ready.append(nxt)
    # A cycle (or a node only reachable through one) keeps its file order
    # rather than vanishing from the recipe.
    ordered.extend(by_id[nid] for nid in by_id if nid not in seen)
    return ordered


def _prompt_of(node: dict) -> Optional[str]:
    settings = node.get("settings")
    if not isinstance(settings, dict):
        return None
    for field in _TEXT_FIELDS:
        value = settings.get(field)
        if isinstance(value, str) and len(value.strip()) >= _MIN_PROMPT_CHARS:
            return value.strip()
    return None


def _node_count(path: Path) -> int:
    try:
        return len(json.loads(path.read_text(encoding="utf-8-sig")).get("nodes") or [])
    except (OSError, json.JSONDecodeError):
        # Listed but unreadable: still show it, with 0 steps, rather than
        # dropping it silently.
        return 0


@router.get("", response_model=list[TemplateSummary])
def list_templates() -> list[TemplateSummary]:
    """Every workflow on disk: the nine shipped samples, then the packaged
    tool's own saved boards from `Workflows/`.

    The personal folder has no index file, so its entries are discovered by
    listing `*.json` and named after the file. An empty board still appears,
    with ``stepCount: 0`` saying so — the same choice this endpoint already
    made for a file it cannot parse, and better than a folder that silently
    shows fewer boards than the user saved.
    """
    out: list[TemplateSummary] = []
    seen: set[str] = set()
    for entry in _read_index():
        name = Path(str(entry["file"])).name
        seen.add(name)
        out.append(
            TemplateSummary(
                file=name,
                name=str(entry.get("name") or entry["file"]),
                description=str(entry.get("description") or ""),
                stepCount=_node_count(TEMPLATE_DIR / name),
            )
        )

    if USER_TEMPLATE_DIR.is_dir():
        for path in sorted(USER_TEMPLATE_DIR.glob("*.json")):
            if path.name in seen:
                continue
            seen.add(path.name)
            out.append(
                TemplateSummary(
                    file=path.name,
                    name=path.stem,
                    description="Workflow đã lưu trong tool gốc",
                    stepCount=_node_count(path),
                    source="tool",
                )
            )

    # Saved from this canvas. Listed last but marked writable — these are the
    # only ones the rename / edit / delete endpoints will touch.
    for entry in board_templates.list_mine():
        if entry["file"] in seen:
            continue
        seen.add(entry["file"])
        out.append(
            TemplateSummary(
                file=entry["file"],
                name=entry["name"],
                description=entry["description"],
                stepCount=entry["stepCount"],
                source="mine",
                writable=True,
            )
        )
    return out


@router.get("/{file}", response_model=TemplateDetail)
def read_template(file: str) -> TemplateDetail:
    """One workflow as an ordered recipe with its prompts."""
    path = _safe_template_path(file)
    try:
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"template unreadable: {exc}") from None

    if board_templates.is_native(doc):
        return _native_detail(path, doc)

    nodes = [n for n in (doc.get("nodes") or []) if isinstance(n, dict)]
    connections = [c for c in (doc.get("connections") or []) if isinstance(c, dict)]
    meta = next(
        (e for e in _read_index() if Path(str(e["file"])).name == path.name), {}
    )

    steps: list[TemplateStep] = []
    unmapped: list[str] = []
    for index, node in enumerate(_order_nodes(nodes, connections), start=1):
        node_type = str(node.get("type") or "?")
        guide = STEP_GUIDE.get(node_type)
        if guide is None and node_type not in unmapped:
            unmapped.append(node_type)
        steps.append(
            TemplateStep(
                order=index,
                type=node_type,
                label=str(node.get("label") or node_type),
                doneWith=guide or "Chưa có bước tương ứng trong bản này",
                prompt=_prompt_of(node),
            )
        )

    return TemplateDetail(
        file=path.name,
        name=str(meta.get("name") or path.stem),
        description=str(meta.get("description") or ""),
        steps=steps,
        unmappedTypes=unmapped,
    )


class ImportResult(BaseModel):
    """What the import actually produced.

    The counts are reported next to the file's own totals on purpose: an
    import that quietly lost a third of the graph would otherwise look
    exactly like one that worked.
    """

    boardId: int
    #: A plan over exactly the imported nodes, so the board can be run with
    #: the executor that already exists.
    planId: int
    name: str
    nodes: int
    nodesInFile: int
    edges: int
    edgesInFile: int
    danglingEdges: int
    unsupported: list[str]


@router.post("/{file}/import", response_model=ImportResult)
def import_to_board(file: str) -> ImportResult:
    """Build a board from one workflow file.

    Free and offline — this writes rows, it does not dispatch anything. The
    board is created every call rather than reused, because these templates
    are starting points to edit, and a second import should not overwrite the
    edits made to the first.
    """
    path = _safe_template_path(file)
    try:
        doc = template_import.read_template(path)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None

    meta = next(
        (e for e in _read_index() if Path(str(e["file"])).name == path.name), {}
    )
    native = board_templates.is_native(doc)
    name = str((doc.get("name") if native else None) or meta.get("name") or path.stem)

    with get_session() as session:
        summary = (
            board_templates.import_native(session, doc, name=name)
            if native
            else template_import.import_template(session, doc, name=name)
        )

    logger.info(
        "template import: %s → board %s (%d/%d nodes, %d/%d edges)",
        path.name,
        summary["board_id"],
        summary["nodes"],
        summary["nodes_in_file"],
        summary["edges"],
        summary["edges_in_file"],
    )
    return ImportResult(
        boardId=summary["board_id"],
        planId=summary["plan_id"],
        name=summary["name"],
        nodes=summary["nodes"],
        nodesInFile=summary["nodes_in_file"],
        edges=summary["edges"],
        edgesInFile=summary["edges_in_file"],
        danglingEdges=summary["dangling_edges"],
        unsupported=summary["unsupported"],
    )


# ── personal templates: the writable third of the list ────────────────
#
# Everything below refuses to touch the packaged samples or the exe's own
# `Workflows/` folder — those are read through `_safe_template_path` and
# written by nothing. `board_templates.safe_mine_path` resolves only inside
# `storage/templates`, so "delete template" cannot reach a file the user did
# not create here even if the request names one.


def _as_http(exc: board_templates.TemplateError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


def _native_detail(path: Path, doc: dict) -> TemplateDetail:
    """A personal template as the same ordered recipe the samples produce.

    Its nodes are canvas types rather than exe types, so the "do this on
    screen X" column would be a lie; it names the node instead.
    """
    steps: list[TemplateStep] = []
    for index, node in enumerate(doc.get("nodes") or [], start=1):
        if not isinstance(node, dict):
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        node_type = str(node.get("type") or "?")
        prompt = data.get("prompt")
        steps.append(
            TemplateStep(
                order=index,
                type=node_type,
                label=str(data.get("title") or node_type),
                doneWith="Node trên canvas này",
                prompt=prompt if isinstance(prompt, str) and prompt.strip() else None,
            )
        )
    return TemplateDetail(
        file=path.name,
        name=str(doc.get("name") or path.stem),
        description=str(doc.get("description") or ""),
        steps=steps,
        unmappedTypes=[],
    )


class SaveTemplateBody(BaseModel):
    board_id: int
    name: str
    description: str = ""


@router.post("", response_model=TemplateSummary)
def save_template(body: SaveTemplateBody) -> TemplateSummary:
    """Save a board as a personal template.

    Saved in this app's own format, not the exe's: `NODE_TYPE_MAP` is
    many-to-one and five canvas types have no exe counterpart, so a save
    through that converter would silently drop nodes from the user's board.
    """
    with get_session() as session:
        try:
            saved = board_templates.save_board_as_template(
                session, body.board_id, name=body.name, description=body.description
            )
        except board_templates.TemplateError as exc:
            raise _as_http(exc) from None
    return TemplateSummary(
        file=saved["file"],
        name=saved["name"],
        description=saved["description"],
        stepCount=len(saved["nodes"]),
        source="mine",
        writable=True,
    )


class RenameTemplateBody(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


@router.patch("/mine/{file}")
def rename_template(file: str, body: RenameTemplateBody) -> dict:
    """Rename or re-describe a personal template."""
    try:
        return board_templates.update_meta(
            file, name=body.name, description=body.description
        )
    except board_templates.TemplateError as exc:
        raise _as_http(exc) from None


class ReplaceTemplateBody(BaseModel):
    board_id: int


@router.put("/mine/{file}", response_model=TemplateSummary)
def replace_template(file: str, body: ReplaceTemplateBody) -> TemplateSummary:
    """Overwrite a personal template with a board's current state — "edit"."""
    with get_session() as session:
        try:
            saved = board_templates.replace_from_board(session, file, body.board_id)
        except board_templates.TemplateError as exc:
            raise _as_http(exc) from None
    return TemplateSummary(
        file=saved["file"],
        name=saved["name"],
        description=saved["description"],
        stepCount=len(saved["nodes"]),
        source="mine",
        writable=True,
    )


@router.delete("/mine/{file}")
def delete_template(file: str) -> dict:
    """Delete a personal template. Reaches only `storage/templates`."""
    try:
        board_templates.delete(file)
    except board_templates.TemplateError as exc:
        raise _as_http(exc) from None
    return {"ok": True}


@router.get("/{file}/export")
def export_template(file: str) -> dict:
    """The raw document, for the browser to save to a file.

    Works for all three sources: exporting a packaged sample is reading, and
    reading them was never restricted.
    """
    path = _safe_template_path(file)
    try:
        return board_templates.read(path)
    except board_templates.TemplateError as exc:
        raise _as_http(exc) from None


class ImportJsonBody(BaseModel):
    #: The parsed file. Either shape is accepted — this app's own export, or
    #: a workflow file from the packaged tool, which is what a user is most
    #: likely to already have.
    document: dict
    name: str = ""


@router.post("/import-json", response_model=TemplateSummary)
def import_json(body: ImportJsonBody) -> TemplateSummary:
    """Save an uploaded JSON document as a personal template.

    Saving rather than importing straight to a board: the file then appears
    in the list with everything else and can be used more than once. Import
    to a board is the existing `/{file}/import`, one step later.
    """
    doc = body.document
    if not isinstance(doc, dict):
        raise HTTPException(400, "file phải là một object JSON")
    nodes = doc.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise HTTPException(400, "file không có node nào — không phải workflow")
    if not board_templates.is_native(doc) and not isinstance(doc.get("connections"), list):
        raise HTTPException(
            400,
            "không nhận ra định dạng: cần `connections` (file của tool gốc) "
            "hoặc `format` (file xuất từ app này)",
        )

    name = (body.name or str(doc.get("name") or "")).strip() or "Mẫu đã nhập"
    doc = {**doc, "name": name}
    try:
        stem = board_templates.slugify(name)
        path = board_templates._unique_path(stem)
        board_templates._write(path, doc)
    except board_templates.TemplateError as exc:
        raise _as_http(exc) from None

    logger.info("template imported from file: %s (%d nodes)", path.name, len(nodes))
    return TemplateSummary(
        file=path.name,
        name=name,
        description=str(doc.get("description") or ""),
        stepCount=len(nodes),
        source="mine",
        writable=True,
    )
