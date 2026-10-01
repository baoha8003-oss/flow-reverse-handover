"""The backfill has to RUN, not merely exist.

This whole phase is about one defect class: a capability is implemented and
tested, and no call layer ever reaches it. `backfill_operation_ids` has its own
suite (`test_backfill_render_ids.py`) proving it does the right thing — which
would all pass with the function never called once.

So this pins the wiring instead of the behaviour: open a database the way the
agent opens one, and check the ids landed. It uses `init_db(bind=...)` on a temp
file, the path its own docstring documents for exactly this ("a test can pass its
own engine on a temp file to exercise migration against a hand-built old
schema").
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
from sqlmodel import Session, select

from flowboard.db.models import Board, Node, Request
from flowboard.db.session import init_db


def _fresh_db(tmp_path: Path):
    """A database built and migrated the way start-up builds one."""
    engine = create_engine(f"sqlite:///{tmp_path / 'wired.db'}")
    init_db(bind=engine)
    return engine


def _seed_pre_p16_clip(engine, *, model_key: str) -> int:
    """A finished render whose node predates `_settle_generation_node` keeping ids."""
    with Session(engine) as s:
        board = Board(name="startup")
        s.add(board)
        s.commit()
        s.refresh(board)
        node = Node(
            board_id=board.id, short_id="A1", type="video",
            data={"mediaId": "m-1"},   # no operationNames, no sourceModelKey
        )
        s.add(node)
        s.commit()
        s.refresh(node)
        s.add(Request(
            node_id=node.id, type="gen_video", params={}, status="done",
            result={"operation_names": ["op-a"], "model_key": model_key},
        ))
        s.commit()
        return node.id


def _data(engine, node_id: int) -> dict:
    with Session(engine) as s:
        return dict(s.get(Node, node_id).data or {})


def test_opening_the_database_carries_the_render_ids_onto_the_node(tmp_path):
    engine = _fresh_db(tmp_path)
    nid = _seed_pre_p16_clip(engine, model_key="veo_3_1_t2v_lite")

    init_db(bind=engine)          # the second open: what a restart does

    data = _data(engine, nid)
    assert data["operationNames"] == ["op-a"]
    assert data["sourceModelKey"] == "veo_3_1_t2v_lite"


def test_the_backfill_is_committed_not_just_staged(tmp_path):
    """`backfill_operation_ids` deliberately does not commit — the caller owns the
    transaction. If start-up forgets, the sweep is a no-op that looks like a
    success, which is the mistake `materialize_plan` once shipped."""
    engine = _fresh_db(tmp_path)
    nid = _seed_pre_p16_clip(engine, model_key="abra_r2v_4s")

    init_db(bind=engine)

    # A brand-new session: a staged-but-uncommitted change would be invisible here.
    with Session(engine) as s:
        assert (s.get(Node, nid).data or {}).get("sourceModelKey") == "abra_r2v_4s"


def test_an_omni_clip_gains_the_key_that_makes_its_refusal_work(tmp_path):
    """The money half. Without `sourceModelKey`, `is_omni_model_key("")` is False,
    so the extend refusal cannot fire and Flow bills the attempt."""
    from flowboard.services.flow_sdk import is_omni_model_key

    engine = _fresh_db(tmp_path)
    nid = _seed_pre_p16_clip(engine, model_key="abra_r2v_4s")
    assert is_omni_model_key(_data(engine, nid).get("sourceModelKey")) is False

    init_db(bind=engine)

    assert is_omni_model_key(_data(engine, nid)["sourceModelKey"]) is True


def test_a_database_with_nothing_to_backfill_still_opens(tmp_path):
    engine = _fresh_db(tmp_path)
    init_db(bind=engine)
    with Session(engine) as s:
        assert list(s.exec(select(Node))) == []
