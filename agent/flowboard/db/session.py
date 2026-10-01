import logging
from contextlib import contextmanager

from sqlalchemy import event, inspect
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, SQLModel, create_engine

from flowboard.config import DB_PATH

logger = logging.getLogger(__name__)

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    echo=False,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _configure_sqlite(dbapi_conn, _connection_record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    # WAL lets the worker write progress while a route reads, instead of
    # serializing every access; busy_timeout makes a contended write wait
    # up to 5s for the lock rather than raising "database is locked" and
    # having that surface as a spurious request failure.
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def _ensure_columns(conn, table: str, cols: dict[str, str]) -> None:
    """ALTER-add any of ``cols`` (name → SQLite type/DDL) missing from
    ``table``. Non-destructive and idempotent: existing data is untouched
    and re-running is a no-op. ``create_all`` cannot do this — it skips
    ALTERs on tables that already exist."""
    have = {c["name"] for c in inspect(conn).get_columns(table)}
    for name, ddl in cols.items():
        if name not in have:
            conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db(bind=None) -> None:
    """Migrate an existing DB non-destructively, then create missing tables.

    ``bind`` defaults to the module engine; a test can pass its own engine
    (on a temp file) to exercise migration against a hand-built old schema
    without touching the real database."""
    from flowboard.db import models  # noqa: F401 — registers tables for create_all

    target = bind if bind is not None else engine

    with target.connect() as conn:
        insp = inspect(conn)

        # `asset` gained `url` in Run 6. Older tables lack it. Bring them
        # forward WITHOUT dropping — a drop here would erase real asset
        # rows once the media library (P4) starts writing them. ALTER-add
        # the columns the current model expects instead.
        if insp.has_table("asset"):
            _ensure_columns(
                conn,
                "asset",
                {
                    "url": "VARCHAR",
                    "local_path": "VARCHAR",
                    "mime": "VARCHAR",
                    "uuid_media_id": "VARCHAR",
                },
            )
            # IF NOT EXISTS guards the index NAME, not the data: a legacy
            # table that already carries duplicate uuid_media_id values
            # raises here. That must not stop the app from booting — the user
            # has no way to hand-edit SQLite out of it — so fall back to a
            # non-unique index.
            #
            # Be honest about what that costs: the ingest paths are
            # find-then-insert with no locking, so with the unique index gone
            # nothing prevents further duplicates. The fallback keeps the app
            # usable on a database that is already dirty; it is not a second
            # line of defence. The warning below is the signal to clean up.
            try:
                conn.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_asset_uuid_media_id "
                    "ON asset (uuid_media_id)"
                )
            except (IntegrityError, OperationalError):
                conn.rollback()
                logger.warning(
                    "asset.uuid_media_id holds duplicates — creating a "
                    "non-unique index instead; de-duplicate to restore the "
                    "unique constraint"
                )
                conn.exec_driver_sql(
                    "CREATE INDEX IF NOT EXISTS ix_asset_uuid_media_id "
                    "ON asset (uuid_media_id)"
                )

        # Edge gained variant pinning, then named ports — the latter needed to
        # import the packaged tool's workflow templates, whose wires say which
        # socket they land on. Both are nullable: an existing edge has no port
        # name, and None already means "the node's default socket".
        if insp.has_table("edge"):
            _ensure_columns(
                conn,
                "edge",
                {
                    "source_variant_idx": "INTEGER",
                    "source_port": "VARCHAR",
                    "target_port": "VARCHAR",
                },
            )

        # Request batch/retry columns (P1). attempt/max_attempts carry a
        # NOT NULL default so existing rows get sane values; progress is
        # nullable then backfilled to '{}' because a SQLite JSON column
        # added via ALTER reads back as NULL (→ Python None), not {}.
        if insp.has_table("request"):
            _ensure_columns(
                conn,
                "request",
                {
                    "batch_id": "INTEGER",
                    "batch_index": "INTEGER",
                    "attempt": "INTEGER NOT NULL DEFAULT 0",
                    "max_attempts": "INTEGER NOT NULL DEFAULT 3",
                    "free_retries": "INTEGER NOT NULL DEFAULT 0",
                    "captcha_retries": "INTEGER NOT NULL DEFAULT 0",
                    "next_attempt_at": "DATETIME",
                    "progress": "JSON",
                    "scene_id": "INTEGER",
                },
            )
            conn.exec_driver_sql(
                "UPDATE request SET progress = '{}' WHERE progress IS NULL"
            )
            for col in ("batch_id", "next_attempt_at", "scene_id"):
                conn.exec_driver_sql(
                    f"CREATE INDEX IF NOT EXISTS ix_request_{col} "
                    f"ON request ({col})"
                )

        conn.commit()

    # New tables (batchjob, appsetting, …) are created in full — indexes
    # included — because create_all builds tables that don't yet exist.
    SQLModel.metadata.create_all(target)

    # Data, not schema: carry the two ids a finished render already recorded
    # onto its node. Runs here rather than as a script the user has to know
    # about, for the same reason the ALTERs above do — a database that opens is
    # a database that is already migrated. Idempotent and skipped-by-default,
    # so start-up cost after the first run is one query.
    #
    # It also closes a money hole, which is why it is not deferred: without
    # `sourceModelKey` an Omni clip reads as "not Omni", so the extend button
    # offers an extension Flow will not serve and bills for the attempt.
    from sqlmodel import Session as _Session

    from flowboard.services.node_ops import backfill_operation_ids

    try:
        with _Session(target) as s:
            counts = backfill_operation_ids(s)
            if counts["patched"]:
                s.commit()
                logger.info(
                    "backfill: %d node(s) gained their render ids", counts["patched"]
                )
    except Exception:
        # A database that cannot be back-filled must still open. The two
        # capabilities degrade to "button disabled", which is the safe side.
        logger.exception("backfill of render ids failed; continuing")


@contextmanager
def get_session():
    with Session(engine) as session:
        yield session
