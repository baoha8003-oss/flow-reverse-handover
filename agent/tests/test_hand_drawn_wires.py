"""Boards drawn by hand, where no wire has a port name.

The canvas draws one nameless wire per connection, and `START_FRAME_PORTS`
accepts a nameless wire so those boards keep working. That blanket acceptance
turned out to answer two different questions the same way:

* a `prompt` wired into a video looked like a start-frame socket that would
  never fill, so the simplest board anyone can draw — prompt, video, run —
  refused with `missing_upstream_image`;
* a `character` wired into a video looked like a FILLED start-frame socket, so
  a paid image-to-video clip was generated from a character reference sheet.

Neither node can supply a still. These tests pin reading the wire by what is on
the other end of it, and they go through the real executor because the bug was
in what got dispatched, not in what a helper returned.
"""
from __future__ import annotations

import asyncio

import pytest

from flowboard.db import get_session
from flowboard.db.models import BoardFlowProject, Node, PipelineRun
from flowboard.services import pipeline_executor
from flowboard.services.postprod_plan import Upstream


def _board(client) -> dict:
    board = client.post("/api/boards", json={"name": "B"}).json()
    with get_session() as s:
        s.add(BoardFlowProject(board_id=board["id"], flow_project_id="abcd1234"))
        s.commit()
    return board


def _node(client, board_id, node_type, **data) -> dict:
    return client.post("/api/nodes", json={
        "board_id": board_id, "type": node_type, "x": 0, "y": 0, "data": data,
    }).json()


def _wire(client, board_id, source, target) -> None:
    """A connection exactly as the canvas draws one: no port name."""
    resp = client.post("/api/edges", json={
        "board_id": board_id, "source_id": source["id"], "target_id": target["id"],
    })
    assert resp.status_code in (200, 201), resp.text


class _Recorder:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def gen_video(self, **kw):
        self.calls.append(("gen_video", kw))
        return {"raw": {}, "operation_names": ["op"], "model_key": "veo_3_1_i2v_s_fast"}

    async def gen_video_text(self, **kw):
        self.calls.append(("gen_video_text", kw))
        return {"raw": {}, "operation_names": ["op"]}

    async def gen_video_omni(self, **kw):
        self.calls.append(("gen_video_omni", kw))
        return {"raw": {}, "operation_names": ["op"]}

    async def gen_image(self, **kw):
        self.calls.append(("gen_image", kw))
        return {"raw": {}, "media_ids": ["m-img"], "media_entries": []}


async def _run(client, board, monkeypatch) -> _Recorder:
    from flowboard.services import flow_sdk
    from flowboard.worker import processor as proc
    from flowboard.worker.processor import WorkerController, _DEFAULT_HANDLERS

    rec = _Recorder()
    monkeypatch.setattr(flow_sdk, "_sdk", rec)

    async def _poll(sdk, dispatch, request_id):
        return {"media_ids": ["m-out"]}, None

    monkeypatch.setattr(proc, "_poll_video_dispatch", _poll)

    async def _sync(ids, project_id):
        return list(ids), []

    monkeypatch.setattr(
        "flowboard.services.media_project_sync.ensure_media_ids_in_project", _sync
    )

    plan = client.post(f"/api/boards/{board['id']}/plan").json()
    with get_session() as s:
        run = PipelineRun(plan_id=plan["id"], status="pending")
        s.add(run)
        s.commit()
        s.refresh(run)
        rid = run.id

    w = WorkerController(handlers=_DEFAULT_HANDLERS)
    monkeypatch.setattr(proc, "_worker", w)
    task = asyncio.create_task(w.start())
    try:
        await pipeline_executor.run_pipeline(
            rid, request_timeout_s=5.0, poll_interval_s=0.05
        )
    finally:
        w.request_shutdown()
        await asyncio.wait_for(task, timeout=3.0)
    return rec


def _status(node_id: int) -> tuple[str, object]:
    with get_session() as s:
        node = s.get(Node, node_id)
        return node.status, (node.data or {}).get("error")


# ── the simplest board there is ───────────────────────────────────────


@pytest.mark.asyncio
async def test_a_prompt_wired_into_a_video_is_text_to_video(client, monkeypatch):
    """Prompt, video, run. This refused with `missing_upstream_image` because
    the nameless wire counted as a start-frame socket nothing would fill."""
    board = _board(client)
    prompt = _node(client, board["id"], "prompt", title="Ý", prompt="một con mèo")
    video = _node(client, board["id"], "video", title="Clip")
    _wire(client, board["id"], prompt, video)

    rec = await _run(client, board, monkeypatch)

    kinds = [name for name, _ in rec.calls]
    assert kinds == ["gen_video_text"], kinds
    assert _status(video["id"])[0] == "done"


@pytest.mark.asyncio
async def test_an_image_wired_into_a_video_is_still_the_start_frame(
    client, monkeypatch
):
    """The other half: a nameless wire from something that CAN supply a still
    keeps working exactly as before."""
    board = _board(client)
    image = _node(client, board["id"], "visual_asset", title="Ảnh", mediaId="m-user")
    video = _node(client, board["id"], "video", title="Clip", prompt="cho nó đi")
    _wire(client, board["id"], image, video)

    rec = await _run(client, board, monkeypatch)

    assert [name for name, _ in rec.calls] == ["gen_video"]
    assert rec.calls[0][1]["start_media_id"] == "m-user"


@pytest.mark.asyncio
async def test_a_character_sheet_is_never_the_start_frame(client, monkeypatch):
    """A reference sheet as the opening frame buys a clip of a contact sheet.
    Refusing costs nothing; dispatching cost a paid i2v."""
    board = _board(client)
    char = _node(client, board["id"], "character", title="NV", mediaId="m-sheet")
    video = _node(client, board["id"], "video", title="Clip", prompt="cô ấy đi")
    _wire(client, board["id"], char, video)

    rec = await _run(client, board, monkeypatch)

    assert rec.calls == [], "a character sheet reached Flow as a start frame"
    status, error = _status(video["id"])
    assert status == "error"
    assert error == "character_wire_without_port"


# ── the helpers, directly ─────────────────────────────────────────────


def _up(node_type, port, media=None):
    data = {"mediaId": media} if media else {}
    return Upstream(
        type("N", (), {"id": 1, "type": node_type, "data": data})(), port
    )


@pytest.mark.parametrize("node_type,expected", [
    ("image", True),
    ("visual_asset", True),
    ("video", True),
    ("extract_last_frame", True),
    ("prompt", False),
    ("note", False),
    ("character", False),
])
def test_which_nameless_wires_occupy_the_start_frame_socket(node_type, expected):
    assert pipeline_executor._is_start_frame_wire(_up(node_type, None)) is expected


def test_a_named_start_frame_port_is_taken_at_its_word():
    """An imported workflow says `start_frame` outright; the type check is only
    for wires that say nothing."""
    assert pipeline_executor._is_start_frame_wire(_up("prompt", "start_frame")) is True


# ── a producer that already ran and produced nothing ──────────────────


@pytest.mark.asyncio
async def test_a_dead_producer_upstream_does_not_become_text_to_video(
    client, monkeypatch
):
    """The estimate gives a producer the benefit of the doubt, because before
    the run it really will produce. The executor must not: it walks the board
    in topological order, so a wired producer still holding no media has had
    its turn and produced none. Falling through bought a clip that ignores the
    photo the board was built around."""
    board = _board(client)
    image = _node(client, board["id"], "image", title="Ảnh")  # no prompt → skipped
    video = _node(client, board["id"], "video", title="Clip", prompt="cho nó đi")
    _wire(client, board["id"], image, video)

    rec = await _run(client, board, monkeypatch)

    assert rec.calls == [], "a dead start-frame chain was billed as text-to-video"
    status, error = _status(video["id"])
    assert status == "error"
    assert error == "missing_upstream_image"


def test_the_estimate_still_trusts_a_producer_that_has_not_run(client):
    """The same wire, asked before the run: this is a chain, not a fault, and
    quoting it as skipped would under-count the bill."""
    board = _board(client)
    image = _node(client, board["id"], "image", title="Ảnh", prompt="một con mèo")
    video = _node(client, board["id"], "video", title="Clip", prompt="cho nó đi")
    _wire(client, board["id"], image, video)

    body = client.get(f"/api/boards/{board['id']}/estimate").json()
    assert body["billableJobs"] == 2, body["items"]
    assert body["notReadyJobs"] == 0


def test_the_two_answers_come_from_one_function(client):
    """Two implementations of "can this find a start frame?" would drift, and
    the drift shows up as a quote that does not match the run."""
    wires = [_up("image", None)]
    node = type("N", (), {"type": "video"})()
    assert pipeline_executor.start_frame_unavailable(node, wires) is False
    assert pipeline_executor.start_frame_unavailable(node, wires, runtime=True) is True
