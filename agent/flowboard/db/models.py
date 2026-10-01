from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel, Column, JSON


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Board(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    created_at: datetime = Field(default_factory=_utcnow)


class Node(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    board_id: int = Field(foreign_key="board.id", index=True)
    short_id: str = Field(index=True)
    type: str
    x: float = 0.0
    y: float = 0.0
    w: float = 240.0
    h: float = 160.0
    data: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "idle"
    created_at: datetime = Field(default_factory=_utcnow)


class Edge(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    board_id: int = Field(foreign_key="board.id", index=True)
    source_id: int = Field(foreign_key="node.id")
    target_id: int = Field(foreign_key="node.id")
    kind: str = "ref"
    # Per-edge variant pin: when the source node holds multiple variants
    # (`data.mediaIds`), this index selects WHICH variant feeds the
    # downstream as a reference. None = "fall back to the source's
    # active mediaId" (the natural single-variant case).
    #
    # Why per-edge instead of expanding all variants on the wire: each
    # variant of the same upstream produces a SEPARATE Flow API call
    # (Flow doesn't bind output[i] to input[i] when both are
    # multi-variant). Pinning lets the user say "use variant 2 for
    # downstream A, variant 3 for downstream B" with two clicks; the
    # edge UI surfaces the pinned index so the binding stays visible.
    source_variant_idx: Optional[int] = None
    # Which socket on each end the wire is plugged into. A node can have more
    # than one input that means different things — a start frame is not an end
    # frame, image_1 is not image_2 — and without a port name the executor can
    # only tell them apart by arrival order, which is the positional pairing
    # this build removed everywhere else.
    #
    # None means "the node's default socket", which is what every edge drawn
    # by hand in the canvas still is.
    source_port: Optional[str] = None
    target_port: Optional[str] = None


class Request(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    node_id: Optional[int] = Field(default=None, foreign_key="node.id", index=True)
    type: str
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "queued"
    result: dict = Field(default_factory=dict, sa_column=Column(JSON))
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = None

    # Batch + retry bookkeeping (P1 schema; wired by the worker in P2).
    # A request belongs to at most one of node_id / scene_id — canvas mode
    # and tab mode share the same queue.
    # No DB-level FK: SQLite cannot add one through ALTER, so a migrated
    # database would silently disagree with a fresh one (fresh raises on a
    # BatchJob delete, migrated orphans the children). The route layer
    # validates instead — same rule as scene_id below.
    batch_id: Optional[int] = Field(default=None, index=True)
    batch_index: Optional[int] = None  # position within its batch (stable order)
    attempt: int = 0
    max_attempts: int = 3
    # Retries that deliberately don't burn `attempt` still need a ceiling,
    # or an expired token / unsolved captcha re-dispatches for the rest of
    # the session against the one real account this tool depends on.
    free_retries: int = 0
    captcha_retries: int = 0
    # Backoff gate the sweeper reads: don't re-dispatch before this time.
    next_attempt_at: Optional[datetime] = Field(default=None, index=True)
    # In-flight progress, written during processing: {phase, done, total, pct, note}.
    progress: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # Library write-back target (P4). FK not enforced on SQLite ALTER-added
    # columns — the route layer validates.
    scene_id: Optional[int] = Field(default=None, index=True)


class Asset(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    # node_id is optional — assets can arrive from TRPC before any node
    # binding (e.g. the user browses an old Flow project).
    node_id: Optional[int] = Field(default=None, foreign_key="node.id", index=True)
    kind: str  # image | video | thumbnail
    # Media id (the hex uuid from Google Flow). Unique so ingest can upsert.
    uuid_media_id: Optional[str] = Field(default=None, index=True, unique=True)
    # Latest captured signed GCS URL (expires — refreshed when user reopens
    # Flow tab).
    url: Optional[str] = None
    local_path: Optional[str] = None
    mime: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


class MediaProjectMapping(SQLModel, table=True):
    """Cross-project media re-upload cache.

    Flow scopes mediaIds to the project they were uploaded in — a
    ref_media_id from project A is unknown to project B even though we
    have the bytes cached locally. When a dispatch needs to reference
    media from another project (e.g. a cross-board Reference reused on
    a different board), we re-upload the bytes under the target project
    and record the (original, project) → project-local mapping here so
    subsequent dispatches skip the upload round-trip.

    Each row says: "bytes of `original_media_id` are also available
    under `project_id` as `project_local_media_id`". Unique on
    (original_media_id, project_id) — composite index in __table_args__.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    original_media_id: str = Field(index=True)
    project_id: str = Field(index=True)
    project_local_media_id: str
    created_at: datetime = Field(default_factory=_utcnow)
    __table_args__ = (
        UniqueConstraint(
            "original_media_id", "project_id",
            name="uq_media_project_mapping",
        ),
    )


class Reference(SQLModel, table=True):
    """User-curated saved media for cross-board reuse.

    Distinct from Asset (auto-managed cache index). Each Reference
    points at one media_id and snapshots enough metadata to spawn a
    brand-new visual_asset node in any board without re-vision or
    re-upload.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    media_id: str = Field(index=True, unique=True)
    url: Optional[str] = None
    label: str = ""
    kind: str  # "image" | "character" | "visual_asset" | "storyboard_shot"
    ai_brief: Optional[str] = None
    aspect_ratio: Optional[str] = None
    tags: list = Field(default_factory=list, sa_column=Column(JSON))
    pinned: bool = False
    position: int = 0
    source_board_id: Optional[int] = Field(default=None, foreign_key="board.id", index=True)
    source_node_short_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


class ChatMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    board_id: int = Field(foreign_key="board.id", index=True)
    role: str  # user | assistant | system
    content: str
    mentions: list = Field(default_factory=list, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)


class Plan(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    board_id: int = Field(foreign_key="board.id", index=True)
    spec: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "draft"  # draft | approved | running | done | failed
    created_at: datetime = Field(default_factory=_utcnow)


class PlanRevision(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    plan_id: int = Field(foreign_key="plan.id", index=True)
    rev_no: int
    spec: dict = Field(default_factory=dict, sa_column=Column(JSON))
    edits: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=_utcnow)


class PipelineRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    plan_id: int = Field(foreign_key="plan.id", index=True)
    status: str = "pending"  # pending | running | done | failed
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None


class CanvasAction(SQLModel, table=True):
    """One applied canvas-agent edit, with enough to undo its structure.

    Stored rather than derived because undo has to know what the board looked
    like BEFORE — a node's previous `data`, an edge that existed. Recomputing
    that from the current board is guessing.

    `provider` records which AI actually answered. The chain can fall through
    to a different provider than the one the panel showed, and "why does this
    plan look nothing like last time" has no answer without it.

    `undone_at` rather than deleting the row: a spend record and an audit trail
    are the two things this table is for, and a deleted row has neither.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    board_id: int = Field(foreign_key="board.id", index=True)
    #: create_node | configure_node | connect_nodes | disconnect_nodes | ...
    kind: str
    summary: str = ""
    #: What was asked for, as applied. Kept for the diff card and the log.
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    #: Enough previous state to reverse the structure. Shape depends on `kind`.
    inverse: dict = Field(default_factory=dict, sa_column=Column(JSON))
    provider: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
    undone_at: Optional[datetime] = None


class BatchJob(SQLModel, table=True):
    """One user action that fans out to N Request rows (e.g. 20 prompts,
    one video each). Parent status is recomputed from its children's
    statuses — see the worker. Kept separate from Request so a batch has
    its own identity, label, and lifecycle."""
    id: Optional[int] = Field(default=None, primary_key=True)
    kind: str  # gen_video | gen_image | gen_video_text | ...
    label: str = ""
    params: dict = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = "queued"  # queued | running | done | partial | failed | canceled
    total: int = 0
    project_id: Optional[int] = None
    created_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = None


class AppSetting(SQLModel, table=True):
    """Key→value overrides layered over the shipped config.json defaults.

    Value is any JSON scalar/structure (a string ratio, an int count, a
    bool). Only whitelisted keys are ever written here — credential and
    account keys are refused at the route layer, never persisted."""
    key: str = Field(primary_key=True)
    value: Any = Field(default=None, sa_column=Column(JSON))
    updated_at: datetime = Field(default_factory=_utcnow)


class BoardFlowProject(SQLModel, table=True):
    """1:1 link between a local board and a Google Flow project_id.

    Kept as a separate table so we don't have to migrate the Board schema.
    Paygate tier is loaded realtime from the extension via /api/auth/me,
    not persisted here — the binding is purely about project identity.
    """
    board_id: int = Field(primary_key=True, foreign_key="board.id")
    flow_project_id: str
    created_at: datetime = Field(default_factory=_utcnow)
