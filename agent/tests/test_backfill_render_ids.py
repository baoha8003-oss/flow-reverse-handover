"""Carrying the two ids a finished render recorded onto its node.

`_settle_generation_node` only began writing `operationNames` and
`sourceModelKey` at P16, so every clip rendered before then has a node that
knows what it produced and not what produced it. Both facts were on the
`Request` row the whole time.

This is not tidying, and the second bullet is the reason it runs at start-up
rather than waiting for someone to ask:

* an upscale needs the OPERATION id, which cannot be derived from the media id —
  the capture warns that swapping them is accepted and then fails NOT_FOUND. So
  a pre-P16 clip is un-upscalable, and the user already paid for that clip;
* an extension refuses an Omni source by reading the model key. With the key
  ABSENT the refusal cannot fire: a blank string does not start with `abra_`, so
  an Omni clip reads as "not Omni" and the button offers an extension Flow will
  not serve — billed on the attempt. The backfill closes that, and
  `hasUnknownSource` on the frontend closes the residue.
"""
from __future__ import annotations

from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import Board, Node, Request
from flowboard.services.node_ops import backfill_operation_ids
from flowboard.short_id import generate_unique_short_id


def _node_and_request(
    *,
    result: dict | None,
    status: str = "done",
    req_type: str = "gen_video",
    data: dict | None = None,
) -> int:
    with get_session() as s:
        board = Board(name="backfill")
        s.add(board)
        s.commit()
        s.refresh(board)
        node = Node(
            board_id=board.id,
            short_id=generate_unique_short_id(s, board.id),
            type="video",
            data=data if data is not None else {"mediaId": "m-1"},
        )
        s.add(node)
        s.commit()
        s.refresh(node)
        s.add(Request(
            node_id=node.id, type=req_type, params={}, status=status, result=result,
        ))
        s.commit()
        return node.id


def _run() -> dict[str, int]:
    with get_session() as s:
        counts = backfill_operation_ids(s)
        s.commit()
        return counts


def _data(node_id: int) -> dict:
    with get_session() as s:
        return dict(s.get(Node, node_id).data or {})


def test_the_operation_id_is_carried_across():
    nid = _node_and_request(result={
        "operation_names": ["op-a"], "media_ids": ["m-1"],
        "model_key": "veo_3_1_t2v_lite",
    })
    _run()
    assert _data(nid)["operationNames"] == ["op-a"]


def test_the_model_key_is_carried_across():
    """This is the half that closes the money hole."""
    nid = _node_and_request(result={
        "operation_names": ["op-a"], "model_key": "abra_r2v_4s",
    })
    _run()
    assert _data(nid)["sourceModelKey"] == "abra_r2v_4s"


def test_the_media_the_node_already_had_is_untouched():
    """A backfill that rewrote `mediaId` would replace the clip the user is
    looking at with whatever the row happened to record."""
    nid = _node_and_request(
        result={"operation_names": ["op-a"], "media_ids": ["m-from-row"],
                "model_key": "veo_3_1_t2v_lite"},
        data={"mediaId": "m-on-node", "prompt": "giữ nguyên"},
    )
    _run()
    data = _data(nid)
    assert data["mediaId"] == "m-on-node"
    assert data["prompt"] == "giữ nguyên"


def test_running_it_twice_changes_nothing_the_second_time():
    nid = _node_and_request(result={
        "operation_names": ["op-a"], "model_key": "veo_3_1_t2v_lite",
    })
    first = _run()
    second = _run()
    assert first["patched"] == 1
    assert second["patched"] == 0
    assert _data(nid)["operationNames"] == ["op-a"]


def test_a_node_that_already_has_both_is_skipped():
    nid = _node_and_request(
        result={"operation_names": ["op-row"], "model_key": "abra_r2v_4s"},
        data={"mediaId": "m", "operationNames": ["op-node"],
              "sourceModelKey": "veo_3_1_t2v_lite"},
    )
    assert _run()["patched"] == 0
    data = _data(nid)
    assert data["operationNames"] == ["op-node"]
    assert data["sourceModelKey"] == "veo_3_1_t2v_lite"


def test_a_half_filled_node_gains_only_the_missing_half():
    nid = _node_and_request(
        result={"operation_names": ["op-row"], "model_key": "abra_r2v_4s"},
        data={"mediaId": "m", "operationNames": ["op-node"]},
    )
    _run()
    data = _data(nid)
    assert data["operationNames"] == ["op-node"]
    assert data["sourceModelKey"] == "abra_r2v_4s"


def test_a_node_that_kept_its_model_key_keeps_it():
    """The mirror of the test above, and the one that was missing.

    With BOTH fields already present the early skip fires and the per-field guards
    are never reached, so only this shape — key present, operation id absent —
    exercises the guard that stops the model key being rewritten. A backfill that
    overwrote it would relabel a clip from whatever the newest row happened to say,
    and the extend refusal reads exactly that field.
    """
    nid = _node_and_request(
        result={"operation_names": ["op-row"], "model_key": "abra_r2v_4s"},
        data={"mediaId": "m", "sourceModelKey": "veo_3_1_t2v_lite"},
    )
    _run()
    data = _data(nid)
    assert data["sourceModelKey"] == "veo_3_1_t2v_lite"
    assert data["operationNames"] == ["op-row"]


def test_an_unfinished_request_is_not_a_source():
    """A `running` row's ids may still change; a `failed` row's clip does not
    exist. Neither describes what the node is showing."""
    nid = _node_and_request(
        result={"operation_names": ["op-a"], "model_key": "veo_3_1_t2v_lite"},
        status="running",
    )
    assert _run()["patched"] == 0
    assert "operationNames" not in _data(nid)


def test_a_row_with_no_result_is_skipped_without_raising():
    nid = _node_and_request(result=None)
    _run()
    assert "operationNames" not in _data(nid)


def test_a_list_of_nothing_but_nulls_is_not_recorded():
    """Recording `[None, None]` would make an upscale button appear for a clip
    with no operation to address — the same rule `_settle_generation_node` keeps."""
    nid = _node_and_request(result={"operation_names": [None, None]})
    _run()
    assert "operationNames" not in _data(nid)


def test_the_newest_finished_render_wins():
    """A node re-run twice should describe its LATEST clip. Whichever row came
    back first is not an answer to that."""
    nid = _node_and_request(result={
        "operation_names": ["op-old"], "model_key": "abra_r2v_4s",
    })
    with get_session() as s:
        s.add(Request(
            node_id=nid, type="gen_video", params={}, status="done",
            result={"operation_names": ["op-new"], "model_key": "veo_3_1_t2v_lite"},
        ))
        s.commit()
    _run()
    data = _data(nid)
    assert data["operationNames"] == ["op-new"]
    assert data["sourceModelKey"] == "veo_3_1_t2v_lite"


def test_a_recovered_clip_counts_as_a_render():
    """`poll_video` is how a render abandoned at the executor's timeout is found
    again. Its clip is as real as any other and its row carries the same ids."""
    nid = _node_and_request(
        result={"operation_names": ["op-a"], "model_key": "veo_3_1_t2v_lite"},
        req_type="poll_video",
    )
    _run()
    assert _data(nid)["operationNames"] == ["op-a"]


def test_a_non_video_request_is_not_a_source():
    """An image row has no operation to upscale and no video model to extend."""
    nid = _node_and_request(
        result={"operation_names": ["op-a"], "model_key": "GEM_PIX_2"},
        req_type="gen_image",
    )
    assert _run()["patched"] == 0
    assert "sourceModelKey" not in _data(nid)


def test_a_request_with_no_node_is_ignored():
    with get_session() as s:
        s.add(Request(
            node_id=None, type="gen_video", params={}, status="done",
            result={"operation_names": ["op-a"], "model_key": "veo_3_1_t2v_lite"},
        ))
        s.commit()
    assert _run()["patched"] == 0


def test_the_backfill_does_not_commit_on_its_own():
    """Same contract as the other two functions in this module: the caller owns
    the transaction, because `init_db` writes once for the whole sweep."""
    nid = _node_and_request(result={
        "operation_names": ["op-a"], "model_key": "veo_3_1_t2v_lite",
    })
    with get_session() as s:
        backfill_operation_ids(s)
        s.rollback()
    assert "operationNames" not in _data(nid)


def test_a_request_can_never_point_at_a_node_that_is_gone():
    """Why the `node is None` guard in the backfill is belt-and-braces.

    I wrote a test for "the row points at a deleted node" and the database
    refused to set it up: a foreign key forbids the state. Deletion cannot
    produce it either — `delete_node_cascade` detaches the row first, setting
    `node_id` to NULL, and NULL rows are filtered out before the lookup.

    So the guard cannot be reached, and this test records that rather than
    leaving a guard nobody can explain. It pins the constraint that makes the
    guard redundant, so if the FK is ever dropped this test fails and the guard
    becomes load-bearing again.
    """
    import pytest
    from sqlalchemy.exc import IntegrityError

    nid = _node_and_request(result={"operation_names": ["op-a"]})
    with pytest.raises(IntegrityError):
        with get_session() as s:
            row = s.exec(select(Request).where(Request.node_id == nid)).first()
            row.node_id = 999999
            s.add(row)
            s.commit()


def test_a_detached_row_is_skipped_rather_than_crashing():
    """What deletion actually produces: `node_id` NULL, which is filtered out."""
    nid = _node_and_request(result={
        "operation_names": ["op-a"], "model_key": "veo_3_1_t2v_lite",
    })
    with get_session() as s:
        row = s.exec(select(Request).where(Request.node_id == nid)).first()
        row.node_id = None
        s.add(row)
        s.commit()
    assert _run()["patched"] == 0
