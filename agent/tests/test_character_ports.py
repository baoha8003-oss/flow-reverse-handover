"""Named characters on a video node, and the request Google refuses.

Component mode takes characters instead of a start frame: three on the Veo
lanes, ten on OMNI, each on its own socket because which character is which
is the whole point.

The check that earns its keep is the refusal. A request carrying both a start
frame AND entities comes back `INVALID_ARGUMENT 13` — after the round-trip,
naming neither wire. Reading the graph first turns that into a message on the
node that says exactly which two wires disagree.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flowboard.services import character_ports as cp
from flowboard.services.postprod_plan import Upstream


def node(node_type="character", media=None, **extra):
    data = dict(extra)
    if media is not None:
        data["mediaId"] = media
    return SimpleNamespace(id=1, type=node_type, data=data)


def wire(port, node_obj=None):
    return Upstream(node_obj or node(media="m-1"), port)


# ── which sockets count ───────────────────────────────────────────────


@pytest.mark.parametrize("port,expected", [
    ("character_1", True),
    ("character_10", True),
    ("character-3", True),
    ("CHARACTER_2", True),
    ("character_9f8e7d6c-1234", True),   # the runtime form a hub produces
    ("start_frame", False),
    ("prompt", False),
    ("character", False),
    (None, False),
    ("", False),
])
def test_character_sockets_are_recognised(port, expected):
    assert cp.is_character_port(port) is expected


def test_ten_does_not_sort_between_one_and_two():
    """The socket order is the order the characters appear in the prompt.
    Sorting them as text swaps who is who in the finished clip."""
    ports = ["character_10", "character_2", "character_1"]
    assert sorted(ports, key=cp.port_order) == [
        "character_1", "character_2", "character_10",
    ]


def test_runtime_sockets_come_after_the_numbered_ones():
    ports = ["character_abc12345", "character_2"]
    assert sorted(ports, key=cp.port_order)[0] == "character_2"


# ── the ceiling ───────────────────────────────────────────────────────


@pytest.mark.parametrize("lane,limit", [
    ("omni", 10),
    ("lite", 3),
    ("fast_relaxed", 3),
    (None, 3),
    ("something-new", 3),
])
def test_the_ceiling_follows_the_lane_and_guesses_low(lane, limit):
    """An unknown lane takes the SMALLER number. Guessing high is what
    produces the rejection this module exists to avoid."""
    assert cp.limit_for(lane) == limit


def test_four_characters_on_a_veo_lane_is_refused():
    wires = [wire(f"character_{i}") for i in range(1, 5)]
    code = cp.validate(wires, lane="lite", has_start_frame=False)
    assert code is not None and code.startswith("too_many_characters:4>3")


def test_four_characters_on_omni_is_fine():
    wires = [wire(f"character_{i}") for i in range(1, 5)]
    assert cp.validate(wires, lane="omni", has_start_frame=False) is None


def test_eleven_characters_is_refused_even_on_omni():
    wires = [wire(f"character_{i}") for i in range(1, 12)]
    assert cp.validate(wires, lane="omni", has_start_frame=False) is not None


# ── the combination Google rejects ────────────────────────────────────


def test_a_start_frame_and_characters_together_is_refused():
    """The component dispatch has no start-frame slot, so a board asking for
    both is asking for something that cannot be sent. The alternative to
    refusing is dropping the start frame silently and generating from the
    wrong opening image — paid for, and wrong invisibly."""
    code = cp.validate([wire("character_1")], lane="omni", has_start_frame=True)
    assert code == "character_with_start_frame"
    assert "khung hình đầu" in cp.explain(code)


def test_a_start_frame_alone_is_not_this_modules_business():
    assert cp.validate([], lane="lite", has_start_frame=True) is None


def test_two_wires_into_one_socket_is_refused():
    """Which character that socket holds has no answer, and the dispatch
    would silently keep whichever the graph yielded first."""
    wires = [wire("character_1"), wire("character_1")]
    assert cp.validate(wires, lane="omni", has_start_frame=False) == "duplicate_character_port"


# ── filled and unfilled sockets ───────────────────────────────────────


def test_an_upload_the_user_never_made_is_reported():
    wires = [wire("character_1"), wire("character_2", node(media=None))]
    code = cp.validate(wires, lane="omni", has_start_frame=False)
    assert code == "missing_character:character_2"
    assert "character_2" in cp.explain(code)


def test_a_generator_upstream_is_not_missing_it_is_pending():
    """An `image` node feeding a character socket has no mediaId before the
    run and a real one after. Calling that missing refuses the very chains
    these boards are built from."""
    wires = [wire("character_1", node("image", media=None))]
    assert cp.validate(wires, lane="omni", has_start_frame=False) is None


def test_collect_returns_media_in_socket_order():
    wires = [
        wire("character_2", node(media="m-hai")),
        wire("character_1", node(media="m-mot")),
        wire("prompt", node("prompt")),
    ]
    assert cp.collect(wires) == [
        ("character_1", "m-mot"), ("character_2", "m-hai"),
    ]


def test_collect_skips_sockets_with_nothing_behind_them_yet():
    wires = [wire("character_1", node(media="m-1")), wire("character_2", node(media=None))]
    assert cp.collect(wires) == [("character_1", "m-1")]


def test_no_character_wires_means_nothing_to_check():
    assert cp.validate([wire("prompt", node("prompt"))], lane="lite", has_start_frame=False) is None
    assert cp.collect([wire("prompt", node("prompt"))]) == []


# ── every refusal says something a person can act on ──────────────────


@pytest.mark.parametrize("code", [
    "character_with_start_frame",
    "too_many_characters:5>3",
    "missing_character:character_2",
    "duplicate_character_port",
])
def test_every_code_has_an_explanation(code):
    text = cp.explain(code)
    assert text != code and len(text) > 20


# ── the executor actually taking the component path ───────────────────


@pytest.mark.asyncio
async def test_a_video_with_characters_dispatches_omni_not_veo(client, monkeypatch):
    """The wiring that makes the module worth having. Characters wired in
    means ingredients, not a start frame — a different Flow endpoint."""
    import asyncio

    from flowboard.db import get_session
    from flowboard.db.models import BoardFlowProject, PipelineRun
    from flowboard.services import flow_sdk, pipeline_executor

    calls: dict = {}

    class _Stub:
        async def gen_video_omni(self, **kwargs):
            calls["omni"] = kwargs
            return {"raw": {}, "operations": [], "media_ids": ["m-omni"]}

        async def gen_video(self, **kwargs):  # pragma: no cover
            raise AssertionError("characters must not go down the Veo path")

    monkeypatch.setattr(flow_sdk, "_sdk", _Stub())

    async def _fake_poll(sdk, dispatch, request_id):
        return {"media_ids": ["m-omni"]}, None

    from flowboard.worker import processor as proc

    monkeypatch.setattr(proc, "_poll_video_dispatch", _fake_poll)

    # Flow scopes media ids per project, so the handler re-uploads refs it
    # has not seen there. That is a separate concern with its own tests;
    # here it stands in as a pass-through.
    async def _synced(ids, project_id):
        return list(ids), []

    monkeypatch.setattr(proc, "ensure_media_ids_in_project", _synced, raising=False)
    import flowboard.services.media_project_sync as sync_mod

    monkeypatch.setattr(sync_mod, "ensure_media_ids_in_project", _synced)

    board = client.post("/api/boards", json={"name": "B"}).json()
    with get_session() as s:
        s.add(BoardFlowProject(board_id=board["id"], flow_project_id="abcd1234"))
        s.commit()

    face = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "character", "x": 0, "y": 0,
        "data": {"title": "An", "mediaId": "m-face"},
    }).json()
    video = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "video", "x": 400, "y": 0,
        # OMNI prices by length, so the handler refuses a dispatch without
        # one — the estimate says the same thing before the run.
        "data": {"title": "Clip", "prompt": "An đi bộ",
                 "sourceSettings": {"quality": "omni", "duration": "8s"}},
    }).json()
    client.post("/api/edges", json={
        "board_id": board["id"], "source_id": face["id"],
        "target_id": video["id"], "target_port": "character_1",
    })

    plan = client.post(f"/api/boards/{board['id']}/plan").json()
    with get_session() as s:
        run = PipelineRun(plan_id=plan["id"], status="pending")
        s.add(run)
        s.commit()
        s.refresh(run)
        rid = run.id

    from flowboard.worker.processor import WorkerController, _DEFAULT_HANDLERS

    w = WorkerController(handlers=_DEFAULT_HANDLERS)
    monkeypatch.setattr(proc, "_worker", w)
    task = asyncio.create_task(w.start())
    try:
        await pipeline_executor.run_pipeline(rid, request_timeout_s=5.0, poll_interval_s=0.05)
    finally:
        w.request_shutdown()
        await asyncio.wait_for(task, timeout=2.0)

    assert calls["omni"]["ref_media_ids"] == ["m-face"]


@pytest.mark.asyncio
async def test_an_image_plus_characters_fails_before_it_costs_anything(client, monkeypatch):
    from flowboard.db import get_session
    from flowboard.db.models import BoardFlowProject, Node
    from flowboard.services import flow_sdk, pipeline_executor
    import asyncio

    class _Stub:
        async def gen_video_omni(self, **kwargs):  # pragma: no cover
            raise AssertionError("must not dispatch an invalid combination")

        async def gen_video(self, **kwargs):  # pragma: no cover
            raise AssertionError("must not dispatch an invalid combination")

    monkeypatch.setattr(flow_sdk, "_sdk", _Stub())

    board = client.post("/api/boards", json={"name": "B"}).json()
    with get_session() as s:
        s.add(BoardFlowProject(board_id=board["id"], flow_project_id="abcd1234"))
        s.commit()

    frame = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "image", "x": 0, "y": 0,
        "data": {"title": "Khung", "mediaId": "m-frame"},
    }).json()
    face = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "character", "x": 0, "y": 200,
        "data": {"title": "An", "mediaId": "m-face"},
    }).json()
    video = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "video", "x": 400, "y": 0,
        "data": {"title": "Clip", "prompt": "An đi bộ"},
    }).json()
    client.post("/api/edges", json={
        "board_id": board["id"], "source_id": frame["id"],
        "target_id": video["id"], "target_port": "start_frame",
    })
    client.post("/api/edges", json={
        "board_id": board["id"], "source_id": face["id"],
        "target_id": video["id"], "target_port": "character_1",
    })

    plan = client.post(f"/api/boards/{board['id']}/plan").json()
    from flowboard.db.models import PipelineRun
    from flowboard.worker import processor as proc
    from flowboard.worker.processor import WorkerController, _DEFAULT_HANDLERS

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
        await pipeline_executor.run_pipeline(rid, request_timeout_s=5.0, poll_interval_s=0.05)
    finally:
        w.request_shutdown()
        await asyncio.wait_for(task, timeout=2.0)

    with get_session() as s:
        node = s.get(Node, video["id"])
    assert node.status == "error"
    assert node.data.get("error") == "character_with_start_frame"
