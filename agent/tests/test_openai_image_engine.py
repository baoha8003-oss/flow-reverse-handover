"""Choosing OpenAI as a board's image engine, end to end.

The engine is a per-node setting, so the interesting cases are all about
what happens when the node asks for something the machine cannot give it.
Two of them are worth more than the happy path:

* **The engine is off.** Falling back to Flow would honour neither the
  user's choice nor their wallet — it spends Flow credits on a node whose
  owner explicitly picked a different supplier. So the node fails.
* **The node has reference photos wired in.** OpenAI's generations endpoint
  draws from text alone. Honouring the engine while dropping the references
  produces a confident picture of the wrong person, and charges for it.

Both are refusals, which reads as unhelpful until you price the alternative.
"""
from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

import pytest
from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import BoardFlowProject, Node, PipelineRun, Plan
from flowboard.services import node_settings, openai_images, pipeline_executor

_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x04\x00\x00\x00\x06\x00"
    b"\x08\x06\x00\x00\x00" + b"\x00" * 300
)


# ── the setting reaches the dispatch ──────────────────────────────────


def test_the_canvas_spelling_of_the_engine_is_understood():
    """The canvas writes camelCase; imported workflows write snake_case
    under `sourceSettings`. Both have to mean the same thing."""
    canvas = SimpleNamespace(type="image", data={"imageEngine": "openai"})
    imported = SimpleNamespace(
        type="image", data={"sourceSettings": {"image_engine": "openai"}}
    )
    assert node_settings.for_dispatch(canvas)["image_engine"] == "openai"
    assert node_settings.for_dispatch(imported)["image_engine"] == "openai"


def test_a_node_that_says_nothing_gets_no_engine_key():
    """Absent is not "flow": leaving the key out lets the dispatch keep its
    own default instead of stamping a choice the user never made."""
    plain = SimpleNamespace(type="image", data={"prompt": "x"})
    assert "image_engine" not in node_settings.for_dispatch(plain)


def test_an_unrecognised_engine_does_not_become_openai():
    """The dangerous direction. A typo must not route spending somewhere
    the user never chose."""
    weird = SimpleNamespace(type="image", data={"imageEngine": "midjourney"})
    assert "image_engine" not in node_settings.for_dispatch(weird)


def test_a_video_node_never_carries_an_image_engine():
    vid = SimpleNamespace(type="video", data={"imageEngine": "openai"})
    assert "image_engine" not in node_settings.for_dispatch(vid)


# ── running a board ───────────────────────────────────────────────────


def _board_with_project(client, project_id="abcd1234") -> dict:
    b = client.post("/api/boards", json={"name": "P"}).json()
    with get_session() as s:
        s.add(BoardFlowProject(board_id=b["id"], flow_project_id=project_id))
        s.commit()
    return b


def _plan(board_id: int, spec: dict) -> int:
    with get_session() as s:
        plan = Plan(board_id=board_id, spec=spec, status="draft")
        s.add(plan)
        s.commit()
        s.refresh(plan)
        return plan.id


async def _run(board_id: int, plan_id: int, monkeypatch, after_materialize=None):
    with get_session() as s:
        pipeline_executor.materialize_plan(s, plan_id)
        if after_materialize is not None:
            after_materialize(s)
        run = PipelineRun(plan_id=plan_id, status="pending")
        s.add(run)
        s.commit()
        s.refresh(run)
        rid = run.id

    from flowboard.worker import processor as proc
    from flowboard.worker.processor import WorkerController, _DEFAULT_HANDLERS

    w = WorkerController(handlers=_DEFAULT_HANDLERS)
    monkeypatch.setattr(proc, "_worker", w)
    task = asyncio.create_task(w.start())
    try:
        await pipeline_executor.run_pipeline(
            rid, request_timeout_s=5.0, poll_interval_s=0.05
        )
    finally:
        w.request_shutdown()
        await asyncio.wait_for(task, timeout=2.0)

    with get_session() as s:
        return {
            n.type: n for n in s.exec(select(Node).where(Node.board_id == board_id)).all()
        }


@pytest.mark.asyncio
async def test_asking_for_openai_while_it_is_off_fails_instead_of_billing_flow(
    client, monkeypatch
):
    """The whole reason this branch exists. A quiet fallback to Flow would
    look like success and arrive as a credit charge."""
    from flowboard.services import flow_sdk

    flow_calls: list[dict] = []

    class _Stub:
        async def gen_image(self, **kwargs):  # pragma: no cover — must not run
            flow_calls.append(kwargs)
            return {"raw": {}, "media_ids": ["m-1"], "media_entries": []}

    monkeypatch.setattr(flow_sdk, "_sdk", _Stub())
    monkeypatch.setattr(openai_images, "available", lambda: False)

    b = _board_with_project(client)
    plan_id = _plan(b["id"], {
        "nodes": [{
            "tmp_id": "i", "type": "image",
            "params": {"prompt": "a cat", "sourceSettings": {"image_engine": "openai"}},
        }],
        "edges": [],
    })
    nodes = await _run(b["id"], plan_id, monkeypatch)

    assert nodes["image"].status == "error"
    assert nodes["image"].data.get("error") == "openai_image_disabled"
    assert flow_calls == [], "a disabled engine must not silently bill Flow"


@pytest.mark.asyncio
async def test_reference_photos_are_refused_rather_than_dropped(client, monkeypatch):
    """A prompt naming photos, generated without them, is a wrong image the
    user still paid for. The existing `missing_upload` guard exists for the
    same reason."""
    from flowboard.services import flow_sdk

    class _Stub:
        async def gen_image(self, **kwargs):  # pragma: no cover
            raise AssertionError("must not dispatch")

    monkeypatch.setattr(flow_sdk, "_sdk", _Stub())
    monkeypatch.setattr(openai_images, "available", lambda: True)

    b = _board_with_project(client)
    plan_id = _plan(b["id"], {
        "nodes": [
            {"tmp_id": "c", "type": "character", "params": {"mediaId": "m-face"}},
            {"tmp_id": "i", "type": "image",
             "params": {"prompt": "@@An standing", "sourceSettings": {"image_engine": "openai"}}},
        ],
        "edges": [{"from": "c", "to": "i"}],
    })

    def _fill_the_photo(session):
        """The upload the user actually made. Without it the earlier
        `missing_upload` guard fires first and this test would pass for the
        wrong reason."""
        char = session.exec(
            select(Node).where(Node.board_id == b["id"], Node.type == "character")
        ).first()
        char.data = {**(char.data or {}), "mediaId": "m-face"}
        session.add(char)
        session.commit()

    nodes = await _run(b["id"], plan_id, monkeypatch, _fill_the_photo)
    assert nodes["image"].status == "error"
    assert nodes["image"].data.get("error") == "openai_image_refs_unsupported"


@pytest.mark.asyncio
async def test_a_working_openai_node_still_ends_up_with_a_flow_media_id(
    client, monkeypatch
):
    """The image is drawn elsewhere but must land in Flow anyway: `media_id`
    is how every downstream node addresses a picture."""
    from flowboard.services import flow_sdk, image_ingest

    monkeypatch.setattr(openai_images, "available", lambda: True)

    async def _generate(prompt, **kwargs):
        return openai_images.GeneratedImage(data=_PNG, mime="image/png", source="api-key")

    async def _ingest(raw, mime, project_id, file_name, node_id=None):
        return {"media_id": "m-openai", "mime": mime, "size": len(raw)}

    monkeypatch.setattr(openai_images, "generate", _generate)
    monkeypatch.setattr(image_ingest, "ingest_bytes", _ingest)

    class _Stub:
        async def gen_image(self, **kwargs):  # pragma: no cover
            raise AssertionError("the OpenAI engine must not call Flow's gen_image")

    monkeypatch.setattr(flow_sdk, "_sdk", _Stub())

    b = _board_with_project(client)
    plan_id = _plan(b["id"], {
        "nodes": [{
            "tmp_id": "i", "type": "image",
            "params": {"prompt": "a cat", "sourceSettings": {"image_engine": "openai"}},
        }],
        "edges": [],
    })
    nodes = await _run(b["id"], plan_id, monkeypatch)
    assert nodes["image"].status == "done"
    assert nodes["image"].data.get("mediaId") == "m-openai"


# ── the worker handler on its own ─────────────────────────────────────


@pytest.mark.asyncio
async def test_a_failed_upload_is_reported_as_an_upload_failure(monkeypatch):
    """The image exists and was paid for. Calling that "generation failed"
    sends the reader to fix the wrong thing."""
    from flowboard.services import image_ingest
    from flowboard.worker.processor import _handle_gen_image_openai

    async def _generate(prompt, **kwargs):
        return openai_images.GeneratedImage(data=_PNG, mime="image/png", source="relay")

    async def _ingest(*a, **kw):
        raise image_ingest.IngestError("Flow từ chối", status=502)

    monkeypatch.setattr(openai_images, "generate", _generate)
    monkeypatch.setattr(image_ingest, "ingest_bytes", _ingest)

    result, err = await _handle_gen_image_openai(
        {"prompt": "a cat", "project_id": "abcd1234"}
    )
    assert result == {}
    assert "tải lên Flow" in err


@pytest.mark.asyncio
async def test_variants_already_paid_for_are_kept_when_a_later_one_fails(monkeypatch):
    """Failing the node would throw away images that have already been
    charged. Partial is the honest answer, and it says so."""
    from flowboard.services import image_ingest
    from flowboard.worker.processor import _handle_gen_image_openai

    calls = {"n": 0}

    async def _generate(prompt, **kwargs):
        calls["n"] += 1
        if calls["n"] > 2:
            raise openai_images.ImageGenError("hết quota")
        return openai_images.GeneratedImage(data=_PNG, mime="image/png", source="api-key")

    async def _ingest(raw, mime, project_id, file_name, node_id=None):
        return {"media_id": f"m-{calls['n']}", "mime": mime, "size": len(raw)}

    monkeypatch.setattr(openai_images, "generate", _generate)
    monkeypatch.setattr(image_ingest, "ingest_bytes", _ingest)

    result, err = await _handle_gen_image_openai(
        {"prompt": "a cat", "project_id": "abcd1234", "variant_count": 4}
    )
    assert err is None
    assert result["media_ids"] == ["m-1", "m-2"]
    assert "hết quota" in result["partial_error"]


@pytest.mark.asyncio
async def test_a_bad_project_id_never_reaches_openai(monkeypatch):
    """Validate before spending, not after."""
    from flowboard.worker.processor import _handle_gen_image_openai

    async def _generate(*a, **kw):  # pragma: no cover
        raise AssertionError("must not spend on an invalid board")

    monkeypatch.setattr(openai_images, "generate", _generate)
    _, err = await _handle_gen_image_openai({"prompt": "x", "project_id": "!!"})
    assert err == "invalid_project_id"


# ── the estimate ──────────────────────────────────────────────────────


@pytest.fixture
def engine_on(monkeypatch):
    """The OpenAI image path switched on, as a user would.

    Needed explicitly since the estimate started asking: a board whose engine
    is off is quoted as not-ready rather than as dollars, because that is what
    the run does with it.
    """
    from flowboard.services import openai_images

    monkeypatch.setattr(openai_images, "available", lambda: True)


def test_openai_images_are_counted_apart_from_flow_credits(client, engine_on):
    """One number for two accounts would match neither balance."""
    board = client.post("/api/boards", json={"name": "B"}).json()
    client.post("/api/nodes", json={
        "board_id": board["id"], "type": "image", "x": 0, "y": 0,
        "data": {"title": "Ảnh", "prompt": "a cat",
                 "sourceSettings": {"image_engine": "openai"}},
    })
    client.post("/api/nodes", json={
        "board_id": board["id"], "type": "image", "x": 0, "y": 0,
        "data": {"title": "Ảnh Flow", "prompt": "a dog"},
    })
    body = client.get(f"/api/boards/{board['id']}/estimate").json()

    assert body["openaiImageJobs"] == 1
    assert body["billableJobs"] == 1, "the OpenAI node must not read as a Flow credit"
    assert "OpenAI" in body["openaiImageNote"]


def test_the_note_names_the_model_and_where_its_price_came_from(
    client, monkeypatch, engine_on
):
    from flowboard.services import settings_store

    monkeypatch.setattr(
        settings_store, "get",
        lambda key: {"OPENAI_IMAGE_MODEL": "gpt-image-1-mini"}.get(key.upper()),
    )
    board = client.post("/api/boards", json={"name": "B"}).json()
    client.post("/api/nodes", json={
        "board_id": board["id"], "type": "image", "x": 0, "y": 0,
        "data": {"title": "Ảnh", "prompt": "a cat",
                 "sourceSettings": {"image_engine": "openai"}},
    })
    note = client.get(f"/api/boards/{board['id']}/estimate").json()["openaiImageNote"]
    assert "gpt-image-1-mini" in note
    assert "giá công bố" in note, "a published price must not read like a guess"


def test_the_switches_are_savable_and_start_off(client):
    """A whitelisted key the settings API then rejects is a control that
    cannot be switched on — a failure mode this whitelist has had before."""
    before = client.get("/api/settings").json()
    for key in ("OPENAI_IMAGE_ENABLED", "OPENAI_IMAGE_RELAY_ENABLED",
                "OPENAI_IMAGE_PREFER_RELAY"):
        assert before.get(key) in (None, False), f"{key} must ship off"

    resp = client.put("/api/settings", json={"values": {
        "OPENAI_IMAGE_ENABLED": True,
        "OPENAI_IMAGE_MODEL": "gpt-image-2",
        "OPENAI_IMAGE_QUALITY": "medium",
    }})
    assert resp.status_code == 200, resp.text
    after = client.get("/api/settings").json()
    assert after["OPENAI_IMAGE_ENABLED"] is True
    assert after["OPENAI_IMAGE_QUALITY"] == "medium"


def test_the_switches_are_booleans_not_truthy_strings(client):
    """`"false"` is a truthy string. Accepting one here would read as ON."""
    resp = client.put("/api/settings", json={"values": {"OPENAI_IMAGE_ENABLED": "false"}})
    assert resp.status_code == 400


def test_a_board_without_openai_nodes_makes_no_claim_about_openai(client):
    board = client.post("/api/boards", json={"name": "B"}).json()
    client.post("/api/nodes", json={
        "board_id": board["id"], "type": "image", "x": 0, "y": 0,
        "data": {"title": "Ảnh", "prompt": "a cat"},
    })
    body = client.get(f"/api/boards/{board['id']}/estimate").json()
    assert body["openaiImageJobs"] == 0
    assert body["openaiImageNote"] == ""


# ── the estimate refuses what the run refuses ─────────────────────────


def _openai_node(client, board_id, **data):
    payload = {"title": "Ảnh", "prompt": "a cat",
               "sourceSettings": {"image_engine": "openai"}}
    payload.update(data)
    return client.post("/api/nodes", json={
        "board_id": board_id, "type": "image", "x": 0, "y": 0, "data": payload,
    }).json()


def test_a_disabled_engine_is_quoted_as_skipped_not_as_dollars(client, monkeypatch):
    """Every board imported while OpenAI is off used to read "will cost
    $0.006 x N" and then generate nothing at all."""
    from flowboard.services import openai_images

    monkeypatch.setattr(openai_images, "available", lambda: False)
    board = client.post("/api/boards", json={"name": "B"}).json()
    _openai_node(client, board["id"])

    body = client.get(f"/api/boards/{board['id']}/estimate").json()
    assert body["openaiImageJobs"] == 0
    assert body["notReadyJobs"] == 1
    note = next(i["note"] for i in body["items"])
    assert "đang tắt" in note


def test_a_reference_wired_openai_node_is_quoted_as_skipped(client, engine_on):
    """The run refuses it — the generations endpoint draws from text alone —
    so quoting it bills for a call that never happens."""
    board = client.post("/api/boards", json={"name": "B"}).json()
    target = _openai_node(client, board["id"])
    ref = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "character", "x": 0, "y": 0,
        "data": {"title": "NV", "mediaId": "m-ref"},
    }).json()
    client.post("/api/edges", json={
        "board_id": board["id"], "source_id": ref["id"], "target_id": target["id"],
    })

    body = client.get(f"/api/boards/{board['id']}/estimate").json()
    assert body["openaiImageJobs"] == 0
    assert body["notReadyJobs"] == 1
    assert "ảnh tham chiếu" in next(
        i["note"] for i in body["items"] if i["nodeId"] == target["id"]
    )
