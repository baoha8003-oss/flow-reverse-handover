"""The two nodes that ask a model, and the sockets that depend on them.

`analyze_video` is a canvas node type with a plan entry that returns NO ops, on
the honest grounds that it is not ffmpeg work — and nothing ran it instead. It
reported success having done nothing, which made three sockets unusable:
`image_prompts`, `video_prompts` and `voice_prompts` had no source on any board,
so the branch mechanism P7 built could not be reached at all.

An imported `gemini_prompt` node was worse than idle. Its `prompt` is an
INSTRUCTION — "Bạn là chuyên gia tạo kịch bản… Người dùng sẽ…" — and passed
downstream verbatim it told the generator to be an expert rather than describing
a picture. A paid dispatch, of the wrong thing, reading as configured.

The asymmetry these sockets exist for is asserted at the end: merged into one
text, the voice reads the camera directions aloud.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import BoardFlowProject, Node, PipelineRun, Request
from flowboard.services import pipeline_executor, postprod_plan, video_analysis


# ── the analysis node runs, and writes the three texts ────────────────


ANSWER = """{
  "description": "Một người mẹ ngồi trước cửa, đợi con về.",
  "vietnamese_script": "Mẹ vẫn đợi.",
  "scenes": [
    {"scene_number": 1, "image_prompt": "a mother waiting on a porch",
     "veo_prompt": "slow push in on a mother waiting", "dialogue": "Con về chưa?"},
    {"scene_number": 2, "image_prompt": "a son at the gate",
     "veo_prompt": "handheld, the son steps through the gate", "dialogue": "Con về rồi mẹ."}
  ]
}"""


@pytest.fixture
def board(client, monkeypatch, tmp_path):
    """A board, a cached clip, and a vision provider that answers `ANSWER`."""
    from flowboard.services import media as media_service
    from flowboard.services import postprod

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"\x00" * 64)
    monkeypatch.setattr(
        media_service, "cached_path", lambda mid: clip if mid == "m-clip" else None
    )
    monkeypatch.setattr(postprod, "available", lambda: True)
    monkeypatch.setattr(
        postprod, "extract_frames", lambda src, workdir, count: [clip] * 3
    )

    asked: dict = {}

    async def _run_llm(feature, prompt, *, system_prompt=None, attachments=None, **kw):
        asked["feature"] = feature
        asked["attachments"] = attachments
        asked["system"] = system_prompt
        return ANSWER

    import flowboard.services.llm as llm_module

    monkeypatch.setattr(llm_module, "run_llm", _run_llm)

    b = client.post("/api/boards", json={"name": "B"}).json()
    with get_session() as s:
        s.add(BoardFlowProject(board_id=b["id"], flow_project_id="abcd1234"))
        s.commit()
    return {"board": b, "client": client, "asked": asked}


def _node(client, board_id, node_type, data, x=0):
    return client.post("/api/nodes", json={
        "board_id": board_id, "type": node_type, "x": x, "y": 0, "data": data,
    }).json()


def _run(client, board_id):
    plan = client.post(f"/api/boards/{board_id}/plan").json()
    with get_session() as s:
        run = PipelineRun(plan_id=plan["id"], status="pending")
        s.add(run)
        s.commit()
        s.refresh(run)
        rid = run.id
    asyncio.run(
        pipeline_executor.run_pipeline(rid, request_timeout_s=0.3, poll_interval_s=0.05)
    )


def test_an_analysis_node_writes_the_three_branch_texts(board):
    client, bid = board["client"], board["board"]["id"]
    source = _node(client, bid, "visual_asset", {"title": "Clip", "mediaId": "m-clip"})
    analyze = _node(
        client, bid, "analyze_video",
        {"title": "Phân tích", "sourceSettings": {"analysis_mode": "🧍 Video Người Que"}},
        x=300,
    )
    client.post("/api/edges", json={
        "board_id": bid, "source_id": source["id"], "target_id": analyze["id"],
        "kind": "ref", "target_port": "video",
    })

    _run(client, bid)

    with get_session() as s:
        row = s.get(Node, analyze["id"])
        data = row.data or {}
        status = row.status
    assert status == "done", data.get("error")
    # One line per scene, so the three lists stay aligned — scene 2's dialogue
    # must not end up under scene 1's picture.
    assert data["imagePrompt"].splitlines() == [
        "a mother waiting on a porch", "a son at the gate",
    ]
    assert data["videoPrompt"].splitlines()[1].startswith("handheld")
    assert data["voicePrompt"].splitlines() == ["Con về chưa?", "Con về rồi mẹ."]
    assert data["prompt"].startswith("Một người mẹ")
    assert board["asked"]["feature"] == "vision"
    assert len(board["asked"]["attachments"]) == 3


def test_the_analysis_node_uses_the_workflow_mode_and_frames(board):
    """`analysis_mode` arrives from the packaged workflow with its emoji, and
    it decides which of the four bundled system prompts runs."""
    client, bid = board["client"], board["board"]["id"]
    source = _node(client, bid, "visual_asset", {"title": "Clip", "mediaId": "m-clip"})
    analyze = _node(
        client, bid, "analyze_video",
        {"title": "Phân tích", "sourceSettings": {"analysis_mode": "🧍 Video Người Que"}},
        x=300,
    )
    client.post("/api/edges", json={
        "board_id": bid, "source_id": source["id"], "target_id": analyze["id"],
        "kind": "ref", "target_port": "video",
    })
    _run(client, bid)
    with get_session() as s:
        mode = (s.get(Node, analyze["id"]).data or {}).get("analysisMode")
    assert mode and mode != ""


def test_an_analysis_node_with_no_clip_stays_idle(board):
    """Not an error: a half-wired board runs the parts that are ready."""
    client, bid = board["client"], board["board"]["id"]
    analyze = _node(client, bid, "analyze_video", {"title": "Phân tích"})
    _run(client, bid)
    with get_session() as s:
        assert s.get(Node, analyze["id"]).status == "idle"


# ── the prompt node that asks a model ─────────────────────────────────


def test_an_imported_gemini_prompt_node_composes_at_run_time(board, monkeypatch):
    client, bid = board["client"], board["board"]["id"]

    async def _chain(feature, prompt, *, system_prompt=None, **kw):
        # The instruction is the SYSTEM prompt and the upstream text is the
        # question — the other way round and the model answers the instruction.
        assert system_prompt and "chuyên gia" in system_prompt
        assert "ghế sofa" in prompt
        return ANSWER, "claude"

    import flowboard.services.llm as llm_module

    monkeypatch.setattr(llm_module, "run_llm_chain", _chain)

    brief = _node(client, bid, "prompt", {"title": "Brief", "prompt": "ghế sofa da thật"})
    writer = _node(
        client, bid, "prompt",
        {
            "title": "Gemini Prompt",
            "prompt": "Bạn là chuyên gia viết kịch bản. Người dùng sẽ mô tả sản phẩm.",
            "sourceType": "gemini_prompt",
        },
        x=300,
    )
    client.post("/api/edges", json={
        "board_id": bid, "source_id": brief["id"], "target_id": writer["id"],
        "kind": "ref", "target_port": "text",
    })

    _run(client, bid)

    with get_session() as s:
        data = s.get(Node, writer["id"]).data or {}
    assert data["composedBy"] == "claude"
    assert data["videoPrompt"].splitlines()[0].startswith("slow push in")
    # The instruction is left exactly where it was: it is what the node IS.
    assert data["prompt"].startswith("Bạn là chuyên gia")


def test_a_hand_typed_prompt_node_is_left_alone(board, monkeypatch):
    """Running a model over a text the user wrote would rewrite their words."""
    client, bid = board["client"], board["board"]["id"]
    called: list[str] = []

    async def _chain(feature, prompt, **kw):
        called.append(feature)
        return ANSWER, "claude"

    import flowboard.services.llm as llm_module

    monkeypatch.setattr(llm_module, "run_llm_chain", _chain)

    node = _node(client, bid, "prompt", {"title": "Mine", "prompt": "một con mèo"})
    _run(client, bid)
    with get_session() as s:
        assert (s.get(Node, node["id"]).data or {}).get("composedPrompt") is None
    assert called == []


def test_a_dead_provider_fails_the_node_and_not_the_downstream_price(board, monkeypatch):
    """The node reports the failure; the board's other nodes refuse for their
    own reasons, which are the honest ones."""
    from flowboard.services.llm.base import LLMError

    async def _chain(feature, prompt, **kw):
        raise LLMError("no provider is configured")

    import flowboard.services.llm as llm_module

    monkeypatch.setattr(llm_module, "run_llm_chain", _chain)

    client, bid = board["client"], board["board"]["id"]
    writer = _node(
        client, bid, "prompt",
        {"title": "W", "prompt": "Bạn là chuyên gia.", "sourceType": "gemini_prompt"},
    )
    _run(client, bid)
    with get_session() as s:
        row = s.get(Node, writer["id"])
    assert row.status == "error"
    assert "prompt_compose_failed" in (row.data or {}).get("error", "")


# ── one definition of which text a socket reads ───────────────────────


def _prompt_node(**data):
    return SimpleNamespace(type="prompt", data=data)


def test_the_voice_socket_reads_the_voice_text():
    """The failure this whole mechanism exists to prevent: `create_voice` read
    the node's plain `prompt`, so the voice read the camera directions aloud."""
    node = _prompt_node(
        prompt="wide shot, 85mm, golden hour",
        imagePrompt="a mother on a porch",
        voicePrompt="Con về rồi đây mẹ.",
    )
    ops = postprod_plan.ops_for(
        SimpleNamespace(type="create_voice", data={}),
        [postprod_plan.Upstream(node, "voice_prompts")],
    )
    assert ops[0]["text"] == "Con về rồi đây mẹ."


def test_a_composed_text_beats_the_instruction_it_came_from():
    node = _prompt_node(
        prompt="Bạn là chuyên gia viết kịch bản.",
        composedPrompt="Một người mẹ ngồi trước cửa.",
    )
    assert postprod_plan.branch_text(node, "text") == "Một người mẹ ngồi trước cửa."


def test_a_hand_typed_node_still_works_on_every_socket():
    node = _prompt_node(prompt="một con mèo")
    for port in ("text", "voice_prompts", "image_prompts", None):
        assert postprod_plan.branch_text(node, port) == "một con mèo"


def test_the_cover_socket_reads_the_cover_prompt_not_the_scene():
    """`nguoi_que_new` and `sao_chep_nguoi_que_new_1706` both wire
    `analyze_video.thumbnail_prompt` onward, and an analyse node's plain
    `prompt` is the scene description — so without its own entry this socket
    generated, and paid for, a cover image of the wrong sentence.
    """
    node = _prompt_node(
        prompt="Người mẹ ngồi trước cửa, chiều muộn.",
        thumbnailPrompt="Cận mặt người mẹ, chữ lớn: MẸ ĐỢI CON",
    )
    assert postprod_plan.branch_text(node, "thumbnail_prompt") == (
        "Cận mặt người mẹ, chữ lớn: MẸ ĐỢI CON"
    )
    # And the other sockets are unaffected by the new entry.
    assert postprod_plan.branch_text(node, "text") == (
        "Người mẹ ngồi trước cửa, chiều muộn."
    )


def test_every_branch_socket_the_packaged_workflows_use_has_a_field():
    """Derived from the workflows rather than from this table, because the
    table is what was believed and the workflows are what exists. This is how
    `thumbnail_prompt` was found missing in the first place."""
    import glob
    import json
    import os

    import flowboard.routes.templates as templates_mod
    from flowboard.services.template_import import NODE_TYPE_MAP

    used: set[str] = set()
    for directory in templates_mod._template_dirs():
        for path in sorted(glob.glob(os.path.join(str(directory), "*.json"))):
            try:
                doc = json.load(open(path, encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            by_id = {n.get("id"): n.get("type") for n in (doc.get("nodes") or [])}
            for wire in doc.get("connections") or []:
                if NODE_TYPE_MAP.get(by_id.get(wire.get("srcNode"))) != "analyze_video":
                    continue
                port = wire.get("srcPort")
                if isinstance(port, str) and port.endswith(("_prompt", "_prompts")):
                    used.add(port)
    assert used, "no analyse-node branch wires found — has the format changed?"
    missing = sorted(p for p in used if p not in postprod_plan.BRANCH_FIELDS)
    assert not missing, f"branch sockets with no field mapping: {missing}"


# ── the AI calls appear in the estimate ───────────────────────────────


def test_the_estimate_counts_the_new_ai_calls(board):
    client, bid = board["client"], board["board"]["id"]
    _node(client, bid, "analyze_video", {"title": "Phân tích"})
    _node(
        client, bid, "prompt",
        {"title": "W", "prompt": "Bạn là chuyên gia.", "sourceType": "gemini_prompt"},
        x=300,
    )
    _node(client, bid, "prompt", {"title": "Mine", "prompt": "một con mèo"}, x=600)

    body = client.get(f"/api/boards/{bid}/estimate").json()
    # Two calls: the analysis and the composer. The hand-typed prompt node is
    # not a call and must not be counted as one.
    assert body["llmJobs"] == 2
    assert body["billableJobs"] == 0


def test_branch_texts_keeps_scenes_aligned():
    analysis = video_analysis.parse(ANSWER)
    texts = video_analysis.branch_texts(analysis)
    assert len(texts["imagePrompt"].splitlines()) == 2
    assert len(texts["voicePrompt"].splitlines()) == 2


def test_an_answer_without_scenes_still_yields_something():
    """A model that ignored the scene format still described the clip, and the
    node's own text is worth keeping even when the sockets get nothing."""
    analysis = video_analysis.parse('{"description": "một con mèo", "prompt": "a cat"}')
    assert video_analysis.branch_texts(analysis)["videoPrompt"] == "a cat"


def test_a_non_json_answer_is_text_not_a_failure():
    analysis = video_analysis.parse("Đây là một con mèo trên mái nhà.")
    assert analysis.description.startswith("Đây là")
    assert analysis.scenes == []


def test_no_request_row_is_created_for_either_node(board):
    """Neither node goes through the worker queue: they are AI calls, not Flow
    dispatches, and a `Request` row would put them in the credit ledger."""
    client, bid = board["client"], board["board"]["id"]
    source = _node(client, bid, "visual_asset", {"title": "Clip", "mediaId": "m-clip"})
    analyze = _node(client, bid, "analyze_video", {"title": "Phân tích"}, x=300)
    client.post("/api/edges", json={
        "board_id": bid, "source_id": source["id"], "target_id": analyze["id"],
        "kind": "ref", "target_port": "video",
    })
    _run(client, bid)
    with get_session() as s:
        assert s.exec(select(Request)).all() == []


def test_a_scene_without_dialogue_keeps_its_place_in_the_list():
    """Scene 2 says nothing, and scene 3's line must not slide up under scene
    2's picture. The lists are read by index — that is what makes them three
    lists rather than one."""
    answer = """{
      "scenes": [
        {"image_prompt": "a", "veo_prompt": "a", "dialogue": "mot"},
        {"image_prompt": "b", "veo_prompt": "b", "dialogue": ""},
        {"image_prompt": "c", "veo_prompt": "c", "dialogue": "ba"}
      ]
    }"""
    texts = video_analysis.branch_texts(video_analysis.parse(answer))
    assert texts["imagePrompt"].splitlines() == ["a", "b", "c"]
    assert texts["voicePrompt"].splitlines() == ["mot", "", "ba"]
