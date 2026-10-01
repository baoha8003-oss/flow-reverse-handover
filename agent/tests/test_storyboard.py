"""An idea in, a whole wired board out.

Nothing new is built underneath: `pipeline_executor.materialize_plan`
already turns a spec of `{"tmp_id", "type", "params"}` plus edges into Node
and Edge rows. This module's only job is getting a model to produce that
spec correctly, so the tests are about the spec — the shape of the graph,
the settings landing where `node_settings` reads them, and every emitted
type being one the executor actually knows.

Drafting spends one text call. Materialising writes rows. No Flow dispatch
happens until someone opens the board and presses Run.
"""
from __future__ import annotations

import json

import pytest

from flowboard.services import storyboard
from flowboard.services.storyboard import StoryboardError, build_spec, parse_scenes

SCENES = [
    {"title": "Mở đầu", "image": "a girl in a yellow dress", "video": "she walks in"},
    {"title": "Giữa", "image": "closer on her face", "video": "she turns to camera"},
    {"title": "Kết", "image": "wide shot", "video": "she walks away"},
]


def _answer(scenes=None) -> str:
    return json.dumps({"scenes": scenes if scenes is not None else SCENES})


# ── reading the plan ──────────────────────────────────────────────────


def test_scenes_parse():
    got = parse_scenes(_answer())
    assert [s["title"] for s in got] == ["Mở đầu", "Giữa", "Kết"]


def test_a_fenced_answer_parses():
    assert len(parse_scenes(f"```json\n{_answer()}\n```")) == 3


def test_a_bare_list_parses_too():
    """Models drop the wrapper object about as often as they keep it."""
    assert len(parse_scenes(json.dumps(SCENES))) == 3


def test_a_scene_with_no_video_prompt_is_dropped():
    """It would materialise into an empty node the user then has to find
    and delete."""
    got = parse_scenes(_answer([{"title": "Trống"}, SCENES[0]]))
    assert len(got) == 1


def test_no_usable_scene_is_an_error():
    with pytest.raises(StoryboardError):
        parse_scenes(_answer([{"title": "Trống"}]))


def test_prose_instead_of_json_is_an_error():
    with pytest.raises(StoryboardError):
        parse_scenes("Đây là ba cảnh: ...")


def test_the_scene_count_is_a_ceiling_not_a_suggestion():
    """A model that returns twelve scenes when asked for three would
    quietly become twelve video dispatches."""
    many = [dict(SCENES[0], title=f"c{i}") for i in range(20)]
    assert len(parse_scenes(_answer(many), limit=3)) == 3


# ── the spec ──────────────────────────────────────────────────────────


def test_every_emitted_type_is_one_the_executor_knows():
    """A type the executor does not know is a node it SKIPS — which shows
    up as a board quietly missing a scene, not as an error."""
    from flowboard.services.pipeline_executor import _VALID_NODE_TYPES

    spec = build_spec(SCENES)
    assert {n["type"] for n in spec["nodes"]} <= set(_VALID_NODE_TYPES)


def test_a_chain_takes_each_clip_from_the_last_frame_of_the_one_before():
    """How every packaged workflow keeps a subject continuous: the previous
    clip's final frame IS the next clip's first."""
    spec = build_spec(SCENES, structure="chain")
    types = [n["type"] for n in spec["nodes"]]
    assert types.count("image") == 1, "only the opening scene needs its own still"
    assert types.count("extract_last_frame") == 2
    assert types.count("video") == 3


def test_independent_scenes_each_get_their_own_still():
    spec = build_spec(SCENES, structure="independent")
    types = [n["type"] for n in spec["nodes"]]
    assert types.count("image") == 3
    assert types.count("extract_last_frame") == 0


def test_the_clips_wire_to_a_start_frame_port():
    """`start_frame` is the port `_start_frame_media_id` looks at. Wire it
    anywhere else and the video node fails with `missing_upstream_image`."""
    from flowboard.services.postprod_plan import START_FRAME_PORTS

    spec = build_spec(SCENES)
    into_video = [
        e for e in spec["edges"]
        if e["to"].startswith("v")
    ]
    assert into_video
    assert all(e["kind"] in START_FRAME_PORTS for e in into_video)


def test_every_edge_endpoint_exists():
    spec = build_spec(SCENES)
    ids = {n["tmp_id"] for n in spec["nodes"]}
    for edge in spec["edges"]:
        assert edge["from"] in ids and edge["to"] in ids, edge


def test_settings_land_where_node_settings_reads_them():
    """Under `sourceSettings`, the same place an imported workflow puts
    them. Anywhere else and the board looks configured while dispatching at
    Flow's defaults."""
    from flowboard.services import node_settings

    spec = build_spec(
        SCENES, aspect="9:16", quality="lite_relaxed", seconds=8
    )
    video = next(n for n in spec["nodes"] if n["type"] == "video")
    settings = video["params"]["sourceSettings"]
    assert node_settings.video_quality(settings) == "lite_relaxed"
    assert node_settings.duration_s(settings) == 8
    assert node_settings.aspect_ratio(settings, media="video") is not None


def test_a_type_the_executor_stopped_knowing_is_refused_loudly(monkeypatch):
    """The two lists have to agree, and the failure mode when they do not is
    nasty: `materialize_plan` SKIPS a node whose type it does not recognise,
    so a renamed type would show up as a board quietly missing every
    scene-chaining step — no error anywhere.

    Simulated by taking `extract_last_frame` out of the executor's registry,
    because that is exactly what a future rename would do."""
    from flowboard.services import pipeline_executor

    monkeypatch.setattr(
        pipeline_executor,
        "_VALID_NODE_TYPES",
        pipeline_executor._VALID_NODE_TYPES - {"extract_last_frame"},
    )
    with pytest.raises(StoryboardError) as exc:
        build_spec(SCENES, structure="chain")
    assert "extract_last_frame" in str(exc.value)


def test_an_unknown_structure_is_refused():
    with pytest.raises(StoryboardError):
        build_spec(SCENES, structure="spiral")


def test_a_single_scene_needs_no_chaining():
    spec = build_spec(SCENES[:1])
    assert [n["type"] for n in spec["nodes"]] == ["image", "video"]


# ── the draft call ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_idea_and_the_scene_count_reach_the_planner(monkeypatch):
    import flowboard.services.llm as llm_module

    seen: dict = {}

    async def _run(kind, prompt, *, system_prompt="", **kw):
        seen["idea"] = prompt
        seen["system"] = system_prompt
        return _answer()

    monkeypatch.setattr(llm_module, "run_llm", _run)
    out = await storyboard.draft("một cô gái ở Hà Nội", scene_count=3)

    assert seen["idea"] == "một cô gái ở Hà Nội"
    assert "3 scenes" in seen["system"]
    assert len(out["scenes"]) == 3


@pytest.mark.asyncio
async def test_a_chain_tells_the_planner_the_scenes_run_continuously(monkeypatch):
    import flowboard.services.llm as llm_module

    seen: dict = {}

    async def _run(kind, prompt, *, system_prompt="", **kw):
        seen["system"] = system_prompt
        return _answer()

    monkeypatch.setattr(llm_module, "run_llm", _run)
    await storyboard.draft("ý tưởng", structure="chain")
    assert "continuously" in seen["system"]


@pytest.mark.asyncio
async def test_an_empty_idea_is_refused_before_any_call():
    with pytest.raises(StoryboardError):
        await storyboard.draft("   ")


# ── the route ─────────────────────────────────────────────────────────


@pytest.fixture
def planner(monkeypatch):
    import flowboard.services.llm as llm_module

    async def _run(kind, prompt, *, system_prompt="", **kw):
        return _answer()

    monkeypatch.setattr(llm_module, "run_llm", _run)


def test_a_preview_writes_no_rows(client, planner):
    """A board that appears without being asked for is worse than one
    extra click."""
    board = client.post("/api/boards", json={"name": "s"}).json()["id"]
    r = client.post("/api/prompt/storyboard", json={
        "board_id": board, "idea": "một cô gái ở Hà Nội", "scene_count": 3,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["plan_id"] is None
    assert len(body["scenes"]) == 3
    assert body["nodes"] == 6  # 1 still + 2 last-frames + 3 clips
    assert client.get(f"/api/boards/{board}").json()["nodes"] == []


def test_materialising_builds_the_board(client, planner):
    board = client.post("/api/boards", json={"name": "s"}).json()["id"]
    r = client.post("/api/prompt/storyboard", json={
        "board_id": board, "idea": "một cô gái ở Hà Nội",
        "scene_count": 3, "materialize": True,
        "aspect": "9:16", "quality": "lite_relaxed",
    })
    assert r.status_code == 200, r.text
    assert r.json()["plan_id"] is not None

    detail = client.get(f"/api/boards/{board}").json()
    types = sorted(n["type"] for n in detail["nodes"])
    assert types == ["extract_last_frame", "extract_last_frame", "image",
                     "video", "video", "video"]
    assert len(detail["edges"]) == 5


def test_the_built_board_estimates_without_dispatching(client, planner):
    """The board is rows, not requests. The estimate is what stands between
    it and the credits."""
    board = client.post("/api/boards", json={"name": "s"}).json()["id"]
    client.post("/api/prompt/storyboard", json={
        "board_id": board, "idea": "ý tưởng", "scene_count": 3,
        "materialize": True,
    })
    body = client.get(f"/api/boards/{board}/estimate").json()
    assert body["billableJobs"] == 4  # one image + three clips


def test_an_unknown_board_is_a_404(client, planner):
    r = client.post("/api/prompt/storyboard", json={
        "board_id": 999999, "idea": "ý tưởng",
    })
    assert r.status_code == 404


@pytest.mark.parametrize("count", [0, 13, -1])
def test_the_scene_count_is_bounded(client, planner, count):
    board = client.post("/api/boards", json={"name": "s"}).json()["id"]
    r = client.post("/api/prompt/storyboard", json={
        "board_id": board, "idea": "ý tưởng", "scene_count": count,
    })
    assert r.status_code == 422
