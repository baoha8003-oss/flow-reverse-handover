"""The seven shapes, as boards rather than as sentences.

`archetypes.classify` answered which shape a brief means, and `Archetype.chain`
described it in words — "gen_image → create_character (hub) → gen_video Thành
Phần → merge → edit". Nothing could build it, so the classifier's answer left
the user wiring seven nodes by hand: the work the archetype was supposed to
save. Mục #4/#14/#15 of the coverage matrix sat on this.

Two shapes are absent on purpose and the tests say which, because "no board"
without a reason is indistinguishable from a bug.
"""
from __future__ import annotations

import pytest

from flowboard.services import archetype_boards, archetypes


def _spec(key: str, **kw) -> dict:
    return archetype_boards.build(key, **kw)


def _edges_into(spec: dict, tmp_id: str) -> list[dict]:
    return [e for e in spec["edges"] if e["to"] == tmp_id]


def test_every_shape_either_builds_or_says_why():
    for archetype in archetypes.ARCHETYPES:
        if archetype_boards.buildable(archetype.key):
            spec = _spec(archetype.key, scene_count=2)
            assert spec["nodes"] and spec["edges"], archetype.key
        else:
            reason = archetype_boards.refusal(archetype.key)
            assert reason and len(reason) > 30, archetype.key


def test_motion_control_says_what_is_missing():
    """The node came off the canvas because the binary showed it is Flow's Edit
    Video (`abra_edit`, chunked upload) and this build has no such endpoint. A
    blueprint placing a node the executor refuses would be a board that cannot
    run, which is worse than a board that was not built."""
    with pytest.raises(archetype_boards.ArchetypeBoardError) as exc:
        _spec("motion_control")
    assert "abra_edit" in str(exc.value)


def test_an_unknown_key_is_refused_with_the_known_ones():
    with pytest.raises(archetype_boards.ArchetypeBoardError) as exc:
        _spec("khong_co_dang_nay")
    assert "frame_chain" in str(exc.value)


def test_every_node_type_is_one_the_executor_knows():
    """A type `materialize_plan` does not know is a node it SKIPS — a board
    quietly missing a step rather than an error anyone sees. `visual_asset` was
    exactly that: accepted by the canvas, absent from the allowlist."""
    from flowboard.services.pipeline_executor import _VALID_NODE_TYPES

    for archetype in archetypes.ARCHETYPES:
        if not archetype_boards.buildable(archetype.key):
            continue
        types = {n["type"] for n in _spec(archetype.key)["nodes"]}
        assert types <= set(_VALID_NODE_TYPES), (archetype.key, types)


def test_no_generation_node_arrives_with_a_prompt():
    """The shape is what the archetype knows. A prompt invented here would be a
    paid dispatch nobody asked for, so every generation node lands empty and the
    run refuses it exactly as it refuses a hand-built empty one."""
    for archetype in archetypes.ARCHETYPES:
        if not archetype_boards.buildable(archetype.key):
            continue
        for node in _spec(archetype.key)["nodes"]:
            if node["type"] in ("image", "video"):
                assert not node["params"].get("prompt"), archetype.key


def test_a_fresh_archetype_board_costs_nothing_yet(client):
    """The other half of the same claim, measured through the estimate the
    confirmation dialog reads."""
    board = client.post("/api/boards", json={"name": "AB"}).json()
    resp = client.post(
        f"/api/boards/{board['id']}/archetype",
        json={"key": "frame_chain", "scene_count": 3},
    )
    assert resp.status_code == 200, resp.text
    built = resp.json()
    assert built["nodes"] >= 6

    estimate = client.get(f"/api/boards/{board['id']}/estimate").json()
    assert estimate["billableJobs"] == 0
    assert estimate["notReadyJobs"] > 0, "the empty nodes have to be named"


def test_the_route_refuses_a_shape_it_cannot_place(client):
    board = client.post("/api/boards", json={"name": "AB"}).json()
    resp = client.post(
        f"/api/boards/{board['id']}/archetype", json={"key": "motion_control"}
    )
    assert resp.status_code == 422
    assert "Edit Video" in resp.json()["detail"]


def test_the_route_needs_a_board_that_exists(client):
    assert client.post("/api/boards/9999/archetype", json={"key": "slideshow"}).status_code == 404


# ── the shapes that carry a claim worth pinning ───────────────────────


def test_the_frame_chain_continues_from_the_previous_clip():
    """This is the whole point of the shape, and how every packaged workflow
    keeps a subject continuous: the previous clip's final frame IS the next
    clip's first."""
    spec = _spec("frame_chain", scene_count=3)
    types = {n["tmp_id"]: n["type"] for n in spec["nodes"]}
    frames = [tid for tid, t in types.items() if t == "extract_last_frame"]
    assert len(frames) == 2, "one join per gap, not per scene"
    for frame in frames:
        feeds = _edges_into(spec, frame)
        assert [types[e["from"]] for e in feeds] == ["video"]
    # And each of those frames opens the next clip through the start frame
    # socket — the port name is what stops the executor reading it as a
    # reference image and buying a paid clip of the wrong thing.
    starts = [e for e in spec["edges"] if e["kind"] == "start_frame"]
    assert len(starts) == 3


def test_the_remake_shape_feeds_all_three_branch_sockets():
    """`analyze_video` writes three texts; the shape is what connects them to
    three different consumers. Merged onto one socket the voice reads the camera
    directions aloud."""
    spec = _spec("remake", scene_count=2)
    ports = {e["kind"] for e in spec["edges"] if e["from"] == "analyze"}
    assert ports == {"image_prompts", "video_prompts", "voice_prompts"}


def test_the_affiliate_shape_marks_its_writer_as_an_llm_node():
    """Otherwise the instruction travels downstream as if it were a prompt, and
    the generator is told to be an expert instead of describing a picture."""
    spec = _spec("affiliate", scene_count=2)
    writer = next(n for n in spec["nodes"] if n["tmp_id"] == "writer")
    assert writer["params"]["promptMode"] == "gemini"
    assert "JSON" in writer["params"]["prompt"]


def test_the_character_shape_numbers_its_sockets():
    """`character_1`, `character_2` — the number is the order they appear in the
    prompt, so swapping two of them is a different video."""
    spec = _spec("character_drama", scene_count=2)
    ports = sorted(e["kind"] for e in spec["edges"] if e["to"] == "v1")
    assert ports == ["character_1", "character_2"]


def test_the_slideshow_dispatches_no_video_generation():
    """The cheapest of the seven: `sync_image_voice` turns one frame into a clip
    as long as the narration, so nothing goes to Flow for motion."""
    spec = _spec("slideshow", scene_count=3)
    assert not any(n["type"] == "video" for n in spec["nodes"])
    assert sum(1 for n in spec["nodes"] if n["type"] == "sync_image_voice") == 3


def test_the_settings_land_where_node_settings_reads_them():
    """`ratio` / `quality` / `duration`, not the plausible-looking
    `aspect_ratio` / `model_quality` / `duration_seconds`. A wrong spelling is
    not an error anywhere; it is a board that reads as configured and runs
    landscape on the charged lane."""
    from types import SimpleNamespace

    from flowboard.services import node_settings

    spec = _spec(
        "frame_chain", scene_count=1, seconds=6,
        aspect="VIDEO_ASPECT_RATIO_PORTRAIT", quality="lite_relaxed",
    )
    video = next(n for n in spec["nodes"] if n["type"] == "video")
    node = SimpleNamespace(
        type="video", data={"sourceSettings": video["params"]["sourceSettings"]}
    )
    merged = node_settings.merged_settings(node)
    assert node_settings.video_quality(merged) == "lite_relaxed"
    assert node_settings.duration_s(merged) == 6


def test_the_scene_count_is_bounded():
    """Each scene is a paid dispatch once the prompts are filled in, so the
    ceiling is a cost guard as much as a sanity one."""
    assert len(_spec("frame_chain", scene_count=99)["nodes"]) == len(
        _spec("frame_chain", scene_count=12)["nodes"]
    )
    assert _spec("frame_chain", scene_count=0)["nodes"]


def test_a_blueprint_emitting_an_unknown_type_is_refused(monkeypatch):
    """The guard, exercised. `materialize_plan` SKIPS a type it does not know,
    so without this a future blueprint would produce a board missing a step and
    nothing would say so — which is how `visual_asset` went missing."""
    monkeypatch.setitem(
        archetype_boards._BLUEPRINTS,
        "slideshow",
        lambda count, settings: {
            "nodes": [{"tmp_id": "x", "type": "motion_control", "params": {}}],
            "edges": [],
        },
    )
    with pytest.raises(archetype_boards.ArchetypeBoardError) as exc:
        _spec("slideshow")
    assert "motion_control" in str(exc.value)
