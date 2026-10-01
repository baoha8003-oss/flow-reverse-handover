"""Migration must upgrade an OLD-schema database in place without losing
data. This test deliberately builds the old schema on a temp file and runs
init_db() against its own engine — it does NOT use the conftest autouse
fixture that drops/recreates every table, because that fixture would make a
migration test vacuous (it would always see a fresh, already-current schema).
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from flowboard.db.session import init_db


def _old_schema_engine(tmp_path: Path):
    """A pre-batch `request` table and a pre-`url` `asset` table, seeded
    with a row each — i.e. what a user upgrading from an older build has."""
    db = tmp_path / "old.db"
    eng = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})
    with eng.begin() as c:
        c.exec_driver_sql(
            "CREATE TABLE request ("
            " id INTEGER PRIMARY KEY, node_id INTEGER, type VARCHAR,"
            " params JSON, status VARCHAR, result JSON, error VARCHAR,"
            " created_at DATETIME, finished_at DATETIME)"
        )
        c.exec_driver_sql(
            "INSERT INTO request (id, type, status, params, result) "
            "VALUES (1, 'gen_video', 'done', '{\"prompt\":\"x\"}', '{}')"
        )
        c.exec_driver_sql(
            "CREATE TABLE asset ("
            " id INTEGER PRIMARY KEY, node_id INTEGER, kind VARCHAR,"
            " created_at DATETIME)"
        )
        c.exec_driver_sql(
            "INSERT INTO asset (id, kind) VALUES (1, 'image')"
        )
    return eng


def test_upgrade_adds_columns_without_losing_data(tmp_path):
    eng = _old_schema_engine(tmp_path)

    init_db(bind=eng)

    insp = inspect(eng)
    req_cols = {c["name"] for c in insp.get_columns("request")}
    assert {
        "batch_id",
        "batch_index",
        "attempt",
        "max_attempts",
        "next_attempt_at",
        "progress",
        "scene_id",
    } <= req_cols
    asset_cols = {c["name"] for c in insp.get_columns("asset")}
    assert {"url", "local_path", "mime", "uuid_media_id"} <= asset_cols

    with eng.connect() as c:
        # The pre-existing rows survived.
        assert c.execute(text("SELECT COUNT(*) FROM request")).scalar() == 1
        assert c.execute(text("SELECT COUNT(*) FROM asset")).scalar() == 1
        # attempt/max_attempts backfilled to their NOT NULL defaults.
        attempt, mx = c.execute(
            text("SELECT attempt, max_attempts FROM request WHERE id=1")
        ).one()
        assert attempt == 0 and mx == 3
        # progress backfilled from NULL to '{}' so it reads as a dict, not None.
        prog = c.execute(
            text("SELECT progress FROM request WHERE id=1")
        ).scalar()
        assert prog == "{}"

    # New tables materialized.
    assert insp.has_table("batchjob")
    assert insp.has_table("appsetting")


def test_migration_is_idempotent(tmp_path):
    """Running init_db twice must not error (no duplicate-column ALTER)."""
    eng = _old_schema_engine(tmp_path)
    init_db(bind=eng)
    init_db(bind=eng)  # must be a no-op, not raise
    assert inspect(eng).has_table("request")


def test_retry_counter_columns_are_added(tmp_path):
    """free_retries/captcha_retries hold the ceilings for retries that
    deliberately don't burn `attempt`; an old row must read them as 0."""
    from sqlmodel import Session, select

    from flowboard.db.models import Request

    eng = _old_schema_engine(tmp_path)
    init_db(bind=eng)
    with Session(eng) as s:
        row = s.exec(select(Request).where(Request.id == 1)).one()
        assert row.free_retries == 0
        assert row.captcha_retries == 0


def test_duplicate_media_ids_do_not_block_boot(tmp_path):
    """A legacy asset table can already hold duplicate uuid_media_id values.
    CREATE UNIQUE INDEX raises on those, and init_db runs first thing in the
    app lifespan — so an unguarded failure means the agent simply refuses to
    start, with no path forward for the user."""
    db = tmp_path / "dupes.db"
    eng = create_engine(f"sqlite:///{db}", connect_args={"check_same_thread": False})
    with eng.begin() as c:
        c.exec_driver_sql(
            "CREATE TABLE asset ("
            " id INTEGER PRIMARY KEY, node_id INTEGER, kind VARCHAR,"
            " uuid_media_id VARCHAR, created_at DATETIME)"
        )
        c.exec_driver_sql(
            "INSERT INTO asset (id, kind, uuid_media_id) VALUES "
            "(1, 'image', 'same-media-id'), (2, 'image', 'same-media-id')"
        )

    init_db(bind=eng)  # must not raise

    with eng.connect() as c:
        # Both rows are still there — no data was dropped to force the index.
        assert c.execute(text("SELECT COUNT(*) FROM asset")).scalar() == 2
    indexes = {i["name"] for i in inspect(eng).get_indexes("asset")}
    assert "ix_asset_uuid_media_id" in indexes


def test_progress_reads_back_as_dict_through_the_orm(tmp_path):
    """The whole point of the '{}' backfill: an old row's progress must
    deserialize to {} via the model, so worker code can do progress["x"]
    without a None crash."""
    from sqlmodel import Session, select

    from flowboard.db.models import Request

    eng = _old_schema_engine(tmp_path)
    init_db(bind=eng)
    with Session(eng) as s:
        row = s.exec(select(Request).where(Request.id == 1)).one()
        assert row.progress == {}
        assert row.attempt == 0
