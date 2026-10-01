"""Excel in, Excel out.

The packaged tool drives long runs from a spreadsheet: a row per clip, with
its own prompt and settings, and the results collected back into a sheet. The
multi-line prompt boxes elsewhere in this build cover the simple case; this
covers the one they cannot — different settings per row, and a record of what
came out.

Parsing is column-name based rather than positional, because a spreadsheet
that someone reordered should still import.
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/batch", tags=["batch"])

# One request holds the whole sheet in memory while it is parsed.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ROWS = 500
MAX_EXPORT_ROWS = 2000

# Accepted spellings for each column, lowercased. Vietnamese headers are
# first-class: the sheets people actually have are written in Vietnamese.
_COLUMNS: dict[str, tuple[str, ...]] = {
    "prompt": ("prompt", "noi dung", "nội dung", "mo ta", "mô tả", "cau lenh", "câu lệnh"),
    "aspect": ("aspect", "ti le", "tỉ lệ", "ty le", "tỷ lệ", "khung hinh", "khung hình"),
    "model": ("model", "mo hinh", "mô hình", "chat luong", "chất lượng", "quality"),
    "duration": ("duration", "thoi luong", "thời lượng", "seconds", "giay", "giây"),
    "image": ("image", "anh", "ảnh", "hinh", "hình", "media_id", "mediaid"),
}

_ASPECT_ALIASES = {
    "9:16": "VIDEO_ASPECT_RATIO_PORTRAIT",
    "doc": "VIDEO_ASPECT_RATIO_PORTRAIT",
    "dọc": "VIDEO_ASPECT_RATIO_PORTRAIT",
    "portrait": "VIDEO_ASPECT_RATIO_PORTRAIT",
    "16:9": "VIDEO_ASPECT_RATIO_LANDSCAPE",
    "ngang": "VIDEO_ASPECT_RATIO_LANDSCAPE",
    "landscape": "VIDEO_ASPECT_RATIO_LANDSCAPE",
}


class BatchRow(BaseModel):
    prompt: str
    aspect: Optional[str] = None
    model: Optional[str] = None
    duration: Optional[int] = None
    image: Optional[str] = None


class ParseResponse(BaseModel):
    rows: list[BatchRow]
    skipped: int = Field(
        description="Rows with no prompt — blank spacer rows, usually."
    )
    columns: list[str]


def _header_map(header: tuple[Any, ...]) -> dict[str, int]:
    """Column name → index, matched case- and accent-spelling-insensitively."""
    found: dict[str, int] = {}
    for index, cell in enumerate(header):
        label = str(cell or "").strip().lower()
        if not label:
            continue
        for field, aliases in _COLUMNS.items():
            if field in found:
                continue
            if label in aliases:
                found[field] = index
    return found


def _cell(row: tuple[Any, ...], index: Optional[int]) -> Optional[str]:
    if index is None or index >= len(row):
        return None
    value = row[index]
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@router.post("/parse", response_model=ParseResponse)
async def parse_sheet(file: UploadFile = File(...)) -> ParseResponse:
    """Read an .xlsx into rows the generation tabs can dispatch."""
    import openpyxl

    raw = await file.read()
    if not raw:
        raise HTTPException(400, "File rỗng.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            413, f"File lớn hơn {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )
    try:
        # read_only keeps a large sheet from being materialised in full, and
        # data_only returns computed values rather than formula text.
        book = openpyxl.load_workbook(
            io.BytesIO(raw), read_only=True, data_only=True
        )
    except Exception:
        raise HTTPException(
            400, "Không đọc được file. Hãy lưu ở định dạng .xlsx."
        ) from None

    sheet = book.active
    if sheet is None:
        raise HTTPException(400, "File không có sheet nào.")

    rows_iter = sheet.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        raise HTTPException(400, "Sheet trống.") from None

    columns = _header_map(header)
    if "prompt" not in columns:
        known = ", ".join(_COLUMNS["prompt"][:4])
        raise HTTPException(
            400,
            f"Không tìm thấy cột prompt. Đặt tên cột đầu là một trong: {known}.",
        )

    out: list[BatchRow] = []
    skipped = 0
    for row in rows_iter:
        if len(out) >= MAX_ROWS:
            break
        prompt = _cell(row, columns.get("prompt"))
        if not prompt:
            skipped += 1
            continue
        aspect = _cell(row, columns.get("aspect"))
        duration_text = _cell(row, columns.get("duration"))
        duration: Optional[int] = None
        if duration_text:
            try:
                duration = int(float(duration_text))
            except ValueError:
                duration = None
        out.append(
            BatchRow(
                prompt=prompt,
                aspect=_ASPECT_ALIASES.get((aspect or "").lower(), aspect),
                model=_cell(row, columns.get("model")),
                duration=duration,
                image=_cell(row, columns.get("image")),
            )
        )
    book.close()
    if not out:
        raise HTTPException(400, "Sheet không có dòng nào có prompt.")
    logger.info("batch: parsed %d row(s) from %s", len(out), file.filename)
    return ParseResponse(rows=out, skipped=skipped, columns=sorted(columns))


# ── the packaged tool's own sheet ─────────────────────────────────────


#: The three columns `excel_mau_batch.xlsx` ships with, read off the file
#: rather than guessed: sheet `Workflow Batch`, header
#: `image_prompt | video_prompt | Lời Thoại`.
_WORKFLOW_COLUMNS: dict[str, tuple[str, ...]] = {
    "image": ("image_prompt", "image prompt", "anh", "ảnh"),
    "video": ("video_prompt", "video prompt", "veo_prompt", "video"),
    "voice": ("lời thoại", "loi thoai", "thoại", "thoai", "voice", "dialogue"),
}

#: Sheets whose name says they are the packaged batch format.
_WORKFLOW_SHEET_NAMES = ("workflow batch", "workflow_batch")


class WorkflowRow(BaseModel):
    """One ROW of the packaged sheet — which is one video, not one scene.

    Measured on the shipped `excel_mau_batch.xlsx`: each CELL holds several
    lines, and line *i* of the three columns describes the same scene. So a row
    of two-line cells is a two-scene video, and the three lists are read by
    index — which is exactly the shape `analyze_video` writes and `fan_out`
    splits.
    """

    index: int
    imagePrompts: list[str] = []
    videoPrompts: list[str] = []
    voicePrompts: list[str] = []
    #: True when the three columns disagree about how many scenes this row has.
    #: Reported rather than repaired: padding would put a line of dialogue under
    #: the wrong picture, and dropping one would lose a scene silently.
    misaligned: bool = False

    @property
    def scene_count(self) -> int:
        return max(
            len(self.imagePrompts), len(self.videoPrompts), len(self.voicePrompts)
        )


class WorkflowParseResponse(BaseModel):
    rows: list[WorkflowRow]
    sheet: str
    skipped: int = 0


def _lines(cell: Any) -> list[str]:
    if cell is None:
        return []
    return [line.strip() for line in str(cell).splitlines() if line.strip()]


@router.post("/workflow", response_model=WorkflowParseResponse)
async def parse_workflow_sheet(file: UploadFile = File(...)) -> WorkflowParseResponse:
    """Parse the packaged batch sheet into rows of aligned scene lists.

    Separate from `/parse`, which reads the one-row-per-clip shape this build
    already had. Both are real files people have; merging them would mean
    guessing which one arrived.
    """
    import openpyxl

    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "File quá lớn (giới hạn 5 MB).")
    try:
        book = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:
        raise HTTPException(400, f"Không đọc được file Excel: {exc}") from None

    sheet = None
    for name in book.sheetnames:
        if name.strip().lower() in _WORKFLOW_SHEET_NAMES:
            sheet = book[name]
            break
    if sheet is None:
        sheet = book.active
    if sheet is None:
        raise HTTPException(400, "File không có sheet nào.")

    rows_iter = sheet.iter_rows(values_only=True)
    try:
        header = next(rows_iter)
    except StopIteration:
        raise HTTPException(400, "Sheet rỗng.") from None

    columns: dict[str, int] = {}
    for position, cell in enumerate(header or ()):
        label = str(cell or "").strip().lower()
        for key, spellings in _WORKFLOW_COLUMNS.items():
            if key not in columns and label in spellings:
                columns[key] = position
    if "video" not in columns and "image" not in columns:
        raise HTTPException(
            400,
            "Không thấy cột image_prompt hoặc video_prompt — đây có phải sheet "
            "'Workflow Batch' của tool không?",
        )

    out: list[WorkflowRow] = []
    skipped = 0
    for index, row in enumerate(rows_iter, start=1):
        if row is None:
            continue
        images = _lines(_cell(row, columns.get("image")))
        videos = _lines(_cell(row, columns.get("video")))
        voices = _lines(_cell(row, columns.get("voice")))
        if not images and not videos and not voices:
            skipped += 1
            continue
        counts = {len(x) for x in (images, videos, voices) if x}
        out.append(
            WorkflowRow(
                index=index,
                imagePrompts=images,
                videoPrompts=videos,
                voicePrompts=voices,
                misaligned=len(counts) > 1,
            )
        )
        if len(out) >= MAX_ROWS:
            break
    book.close()
    if not out:
        raise HTTPException(400, "Sheet không có hàng nào có prompt.")
    logger.info("batch: parsed %d workflow row(s) from %s", len(out), file.filename)
    return WorkflowParseResponse(rows=out, sheet=sheet.title, skipped=skipped)


class BoardFromRowBody(BaseModel):
    board_id: int
    row: WorkflowRow
    aspect: str = ""
    quality: str = ""
    seconds: int = Field(default=8, ge=1, le=60)


@router.post("/workflow/board")
def board_from_workflow_row(body: BoardFromRowBody) -> dict:
    """Turn one sheet row into a board: the `excel_batch` shape, filled in.

    Dispatches nothing. The prompt node carries the row's three lists — one
    line per scene — so the fan-out button on that node splits them into N
    generation nodes, which is the same path a hand-typed multi-line prompt
    takes. No second copy of "how a row becomes scenes".
    """
    from flowboard.db.models import Board, Node, Plan
    from flowboard.services import archetype_boards
    from flowboard.services.pipeline_executor import materialize_plan

    scenes = body.row.scene_count
    if scenes < 1:
        raise HTTPException(422, "Hàng này không có prompt nào.")

    with get_session() as session:
        if session.get(Board, body.board_id) is None:
            raise HTTPException(404, f"no board {body.board_id}")
        try:
            spec = archetype_boards.build(
                "excel_batch",
                scene_count=scenes,
                aspect=body.aspect,
                quality=body.quality,
                seconds=body.seconds,
            )
        except archetype_boards.ArchetypeBoardError as exc:
            raise HTTPException(422, str(exc)) from None

        plan = Plan(board_id=body.board_id, status="approved", spec=spec)
        session.add(plan)
        session.commit()
        session.refresh(plan)
        result = materialize_plan(session, plan.id)
        session.commit()

        # The prompt node of the blueprint carries the row. Found by its
        # `tmp_id` rather than by title: a title is display text and would tie
        # this to the wording of the blueprint.
        mapping = result.get("tmp_to_node_id") or {}
        node_id = mapping.get("rows")
        if node_id is None:
            raise HTTPException(500, "blueprint không có node prompt 'rows'")
        node = session.get(Node, node_id)
        data = dict(node.data or {})
        data["imagePrompt"] = "\n".join(body.row.imagePrompts)
        data["videoPrompt"] = "\n".join(body.row.videoPrompts)
        data["voicePrompt"] = "\n".join(body.row.voicePrompts)
        # The plain `prompt` is what the fan-out counts lines of, and the
        # picture list is the one that always exists.
        data["prompt"] = data["imagePrompt"] or data["videoPrompt"]
        node.data = data
        session.add(node)
        session.commit()
        # Read before the session closes: an ORM object outside its session
        # cannot answer for its own id.
        plan_id = plan.id
        node_count = len(result.get("node_ids") or [])

    return {
        "planId": plan_id,
        "nodes": node_count,
        "promptNodeId": node_id,
        "scenes": scenes,
        "misaligned": body.row.misaligned,
    }


@router.get("/export")
def export_results(limit: int = 200, type: Optional[str] = None):
    """Recent jobs as an .xlsx: what was asked for, and what came back."""
    import openpyxl

    limit = max(1, min(limit, MAX_EXPORT_ROWS))
    wanted = [t.strip() for t in (type or "").split(",") if t.strip()]

    with get_session() as session:
        statement = select(Request).order_by(Request.id.desc()).limit(limit)
        if wanted:
            statement = statement.where(Request.type.in_(wanted))
        rows = session.exec(statement).all()

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Ket qua"
    sheet.append(
        ["ID", "Loai", "Trang thai", "Prompt", "Media", "Tao luc", "Xong luc", "Loi"]
    )
    for req in rows:
        params = req.params if isinstance(req.params, dict) else {}
        result = req.result if isinstance(req.result, dict) else {}
        media = result.get("media_ids") or []
        sheet.append(
            [
                req.id,
                req.type,
                req.status,
                str(params.get("prompt") or "")[:2000],
                ", ".join(str(m) for m in media if m)[:2000],
                req.created_at.isoformat() if req.created_at else "",
                req.finished_at.isoformat() if req.finished_at else "",
                str(req.error or "")[:500],
            ]
        )

    buffer = io.BytesIO()
    book.save(buffer)
    buffer.seek(0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        buffer,
        media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        headers={
            "Content-Disposition": f'attachment; filename="flowboard-{stamp}.xlsx"'
        },
    )
