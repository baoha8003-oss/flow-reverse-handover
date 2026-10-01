"""The free check, where the docstring always said it ran: before dispatch.

`prompt_checks` was built in P3 with a stated contract — "running this before
dispatch turns a refused request into a line of text" — and exactly one caller:
`/api/prompt/relay`, which no screen called. Every other prompt in the product
(canvas, Idea tab, storyboard) went to Google unchecked, so both rules were
discovered the expensive way:

* a `@@tag` inside spoken text is **read aloud** in audio already paid for;
* a real person's name in the visual half is refused by the safety filter
  after the request has been sent.

Which two block, and why the others do not, is asserted here too: the rest need
a cast list and a lane this layer has to infer, and a wrong inference blocking a
board someone can actually run is worse than the round trip.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import BoardFlowProject, Node, PipelineRun, Request
from flowboard.services import knowledge, pipeline_executor


# ── the gate in the executor ──────────────────────────────────────────


@pytest.fixture
def a_board(client, monkeypatch):
    """A board with one image node, and an SDK that records dispatches."""
    from flowboard.services import flow_sdk

    dispatched: list[str] = []

    class _Stub:
        async def gen_image(self, **kwargs):
            dispatched.append(kwargs["prompt"])
            return {"raw": {}, "media_ids": ["m-new"], "media_entries": []}

    monkeypatch.setattr(flow_sdk, "_sdk", _Stub())

    board = client.post("/api/boards", json={"name": "B"}).json()
    with get_session() as s:
        s.add(BoardFlowProject(board_id=board["id"], flow_project_id="abcd1234"))
        s.commit()
    return {"board": board, "dispatched": dispatched, "client": client}


def _run_with(a_board, prompt: str, *, node_type: str = "image", title: str = "A"):
    client = a_board["client"]
    node = client.post("/api/nodes", json={
        "board_id": a_board["board"]["id"], "type": node_type, "x": 0, "y": 0,
        "data": {"title": title, "prompt": prompt},
    }).json()
    plan = client.post(f"/api/boards/{a_board['board']['id']}/plan").json()
    with get_session() as s:
        run = PipelineRun(plan_id=plan["id"], status="pending")
        s.add(run)
        s.commit()
        s.refresh(run)
        rid = run.id

    asyncio.run(
        pipeline_executor.run_pipeline(rid, request_timeout_s=0.3, poll_interval_s=0.05)
    )
    with get_session() as s:
        row = s.get(Node, node["id"])
        state = (row.status, (row.data or {}).get("error"))
        requests = s.exec(select(Request)).all()
    return state, len(requests)


def test_a_tag_inside_dialogue_is_refused_before_anything_is_dispatched(a_board):
    """"@@MaiAnh" in spoken text comes back as "at at Mai Anh" in the audio —
    a clip the user pays for, watches, and re-cuts."""
    state, requests = _run_with(
        a_board, "Cận cảnh người mẹ.\nLời thoại: @@MaiAnh chào mẹ"
    )
    assert state == ("error", "prompt_rule:tag_in_dialogue")
    assert requests == 0
    assert a_board["dispatched"] == []


def test_a_real_name_in_the_visual_half_is_refused(a_board):
    """The filter refuses it after the request is sent, which is a round trip
    and a failed node to learn something checkable for free."""
    state, requests = _run_with(a_board, "Chân dung Bác Hồ bên cửa sổ, ánh sáng sớm")
    assert state == ("error", "prompt_rule:banned_name")
    assert requests == 0


def test_the_same_name_in_the_dialogue_half_is_allowed(a_board):
    """The asymmetry this module exists for: the voiceover is spoken by a
    separate model that the filter never sees. Blocking here would reject
    every legitimate historical narration."""
    state, requests = _run_with(
        a_board,
        "Cận cảnh một vị tướng già trong giáp đồng.\nLời thoại: Bác Hồ từng dạy thế",
    )
    assert state[1] != "prompt_rule:banned_name"
    assert requests == 1


def test_a_warning_does_not_block(a_board):
    """`too_long` is a warning: the tail may be ignored by the generator, which
    is the user's problem to weigh, not a reason to refuse their run."""
    state, requests = _run_with(a_board, "một con mèo trên mái nhà. " * 120)
    # A Request row exists, so it was dispatched. (It then times out: no worker
    # runs in this test, which is exactly the state the money tests pin.)
    assert requests == 1
    assert not str(state[1] or "").startswith("prompt_rule:")


def test_an_unknown_tag_is_logged_rather_than_blocking(a_board):
    """Deliberate, and it needs a wired character to even be reachable: the
    cast is inferred from the wires, so a tag naming nobody is an inference
    about the user's intent, not a rule they broke. Blocking on an inference
    stops a board that would have run."""
    client = a_board["client"]
    board_id = a_board["board"]["id"]
    character = client.post("/api/nodes", json={
        "board_id": board_id, "type": "character", "x": 0, "y": 0,
        "data": {"title": "MaiAnh", "mediaId": "m-char"},
    }).json()
    image = client.post("/api/nodes", json={
        "board_id": board_id, "type": "image", "x": 300, "y": 0,
        "data": {"title": "A", "prompt": "@@KhongCoAi đứng trước cửa"},
    }).json()
    client.post("/api/edges", json={
        "board_id": board_id,
        "source_id": character["id"],
        "target_id": image["id"],
        "kind": "ref",
    })
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
    with get_session() as s:
        row = s.get(Node, image["id"])
        error = (row.data or {}).get("error")
        requests = len(s.exec(select(Request)).all())
    assert requests == 1, "a tag nobody recognises must not stop the dispatch"
    assert not str(error or "").startswith("prompt_rule:")


# ── the playbooks, and the two flags nobody could reach ───────────────


def test_a_format_playbook_loads_by_key():
    if not knowledge.available():
        pytest.skip("the packaged skill tree is not on this machine")
    loaded = knowledge.format_playbook("micro_drama")
    assert loaded.text and loaded.files


def test_an_unknown_format_is_empty_not_an_error():
    """The key comes from a picker in the browser, so a stale option must cost
    the caller its playbook, not its storyboard."""
    assert knowledge.format_playbook("khong_co_dang_nay").text == ""


def test_the_formats_route_lists_them(client):
    body = client.get("/api/prompt/formats").json()
    assert {row["key"] for row in body} == set(knowledge.FORMATS)
    assert all(row["title"] for row in body)


@pytest.mark.asyncio
async def test_the_readme_flags_reach_the_prompt(monkeypatch):
    """`use_longform` / `use_3act` are the README's own two supplements. They
    existed as `SUPPLEMENTS` with `use_hook=True` hardcoded beside them and no
    way for a user to switch either on."""
    if not knowledge.available():
        pytest.skip("the packaged skill tree is not on this machine")
    from flowboard.services import prompt_synth

    seen: dict = {}

    async def _run(feature, prompt, *, system_prompt="", **kw):
        seen["system"] = system_prompt
        return '["cảnh một"]'

    monkeypatch.setattr(prompt_synth, "run_llm", _run)
    await prompt_synth.idea_to_prompts("một cô gái bán hàng rong", scene_count=1)
    plain = seen["system"]

    await prompt_synth.idea_to_prompts(
        "một cô gái bán hàng rong", scene_count=1, use_3act=True
    )
    assert len(seen["system"]) > len(plain)


@pytest.mark.asyncio
async def test_a_chosen_format_reaches_the_prompt(monkeypatch):
    if not knowledge.available():
        pytest.skip("the packaged skill tree is not on this machine")
    from flowboard.services import prompt_synth

    seen: dict = {}

    async def _run(feature, prompt, *, system_prompt="", **kw):
        seen["system"] = system_prompt
        return '["cảnh một"]'

    monkeypatch.setattr(prompt_synth, "run_llm", _run)
    await prompt_synth.idea_to_prompts(
        "một cú lật kèo ở quán cà phê", scene_count=1, video_format="micro_drama"
    )
    assert "Format playbook" in seen["system"]


@pytest.mark.asyncio
async def test_the_storyboard_reads_the_same_selector(monkeypatch):
    """Two copies of "which knowledge for this call" drifted once already —
    the Idea tab and the storyboard were reading different halves."""
    if not knowledge.available():
        pytest.skip("the packaged skill tree is not on this machine")
    from flowboard.services import storyboard

    system = storyboard._system_prompt(
        2, 8, "chain", "", video_format="movie_trailer"
    )
    assert "Format playbook" in system


# ── the idea tab sees what the check found ────────────────────────────


def test_the_idea_route_reports_findings(client, monkeypatch):
    """The prompts come back editable, so the finding belongs beside them —
    before the user presses the button that spends credits."""
    from flowboard.services import prompt_synth

    async def _run(feature, prompt, *, system_prompt="", **kw):
        return '["Cận cảnh mẹ.\\nLời thoại: @@MaiAnh chào mẹ", "một con mèo"]'

    monkeypatch.setattr(prompt_synth, "run_llm", _run)
    body = client.post(
        "/api/prompt/idea", json={"idea": "mẹ và con", "scene_count": 2}
    ).json()
    assert len(body["prompts"]) == 2
    tags = [f for f in body["findings"] if f["rule"] == "tag_in_dialogue"]
    assert [f["scene"] for f in tags] == [1]
