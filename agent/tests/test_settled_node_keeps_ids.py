"""A finished clip has to remember what produced it, not just what it produced.

`_settle_generation_node` used to write only `mediaId` / `mediaIds`, and the
dispatch's other two facts — the operation ids and the model key — were dropped
on the floor. Both are needed AFTER the render:

* an upscale addresses the OPERATION in payload slot 0 and the MEDIA in slot 4.
  They are different ids, and the capture that recorded the call warns that
  swapping them gets the request accepted and then failed with NOT_FOUND. With
  only the media id kept, upscale is unreachable;
* an extension has to know whether the source was Omni, because Flow only
  extends Veo clips — it greys the button out for `abra_*`. Without the model
  key the app can only find that out by dispatching.

So this is not bookkeeping for its own sake: dropping them is what made two
capabilities impossible to offer from a finished node.
"""
from __future__ import annotations

from types import SimpleNamespace

from flowboard.db import get_session
from flowboard.db.models import Board, Node
from flowboard.services.pipeline_executor import _settle_generation_node
from flowboard.short_id import generate_unique_short_id


def _video_node() -> int:
    with get_session() as s:
        board = Board(name="settle test")
        s.add(board)
        s.commit()
        s.refresh(board)
        node = Node(
            board_id=board.id,
            short_id=generate_unique_short_id(s, board.id),
            type="video",
            data={"prompt": "một cảnh"},
        )
        s.add(node)
        s.commit()
        s.refresh(node)
        return node.id


def _settle(node_id: int, result: dict) -> dict:
    """Run the real settle and hand back what landed on the node."""
    settled = SimpleNamespace(status="done", error=None, result=result)
    _settle_generation_node(node_id, settled, set(), {})
    with get_session() as s:
        return dict(s.get(Node, node_id).data or {})


def test_the_operation_ids_are_kept_beside_the_media_ids():
    """Paired by slot, the way the dispatch returned them — clip *i* came from
    operation *i*, and an upscale of clip *i* needs that operation."""
    data = _settle(_video_node(), {
        "media_ids": ["media-a", "media-b"],
        "operation_names": ["op-a", "op-b"],
        "model_key": "veo_3_1_i2v_lite",
    })
    assert data["mediaIds"] == ["media-a", "media-b"]
    assert data["operationNames"] == ["op-a", "op-b"]


def test_the_model_that_produced_the_clip_is_kept():
    """The extend refusal reads this. Without it the only way to learn a clip is
    Omni is to dispatch an extension and be told no."""
    data = _settle(_video_node(), {
        "media_ids": ["m"], "operation_names": ["o"],
        "model_key": "abra_i2v_8s",
    })
    assert data["sourceModelKey"] == "abra_i2v_8s"


def test_a_dispatch_with_no_operation_ids_writes_no_empty_key():
    """An absent key and a key holding `[]` read differently downstream: the
    second looks like "asked and got none"."""
    data = _settle(_video_node(), {"media_ids": ["m"], "operation_names": []})
    assert "operationNames" not in data


def test_a_list_of_nothing_but_nulls_is_not_recorded():
    """A 3-variant batch where every slot failed. Recording `[None, None]` would
    make an upscale button appear for a clip that has no operation to address."""
    data = _settle(_video_node(), {
        "media_ids": ["m"], "operation_names": [None, None],
    })
    assert "operationNames" not in data


def test_a_partial_batch_keeps_the_slots_it_did_get():
    """One clip landed, one did not. The surviving pairing still has to work, so
    the list is kept whole — slot alignment is the point."""
    data = _settle(_video_node(), {
        "media_ids": [None, "media-b"],
        "operation_names": [None, "op-b"],
    })
    assert data["operationNames"] == [None, "op-b"]
    assert data["mediaIds"] == [None, "media-b"]


def test_a_missing_model_key_writes_no_key():
    data = _settle(_video_node(), {"media_ids": ["m"], "operation_names": ["o"]})
    assert "sourceModelKey" not in data


def test_the_media_id_behaviour_is_unchanged():
    """The fix must not disturb what was already right: slot 0 being None must
    still not become `mediaId = None` on a node reporting success."""
    data = _settle(_video_node(), {
        "media_ids": [None, "media-b"], "operation_names": ["op-a", "op-b"],
    })
    assert "mediaId" not in data
