"""Character Entities: referenced, never created, and only when tagged.

The audit left two items (#3, #13) that no phase claimed: a `create_character`
hub and "exactly one hub". P7 could not build them because `flow_sdk` has no
character-creation call — and the binary says why. Mined 18/09, the packaged
tool's own refusal reads:

    Nhân vật CHƯA ACTIVE trong Google Flow project hiện tại.
    Hãy tạo lại nhân vật trong đúng project trước khi chạy.

It tells the user to go make it in Flow. `createCharacter`, `characterEntity`
and `/v1/characters` are zero hits in the executable. So there is nothing to
build a hub AROUND: an entity is an id that Flow issues, per project, and every
tool that uses one only references it —

    referenceEntities: [{entityId: "…"}]

The second mined sentence is the one that decides what gets sent:

    ⏭ [Chỉ Upload Tag] Bỏ qua Entity NV [<name>] do không được tag tên trong Prompt

An entity the prompt never names is left out of the payload. Both sentences are
the tests below.
"""
from __future__ import annotations

import asyncio

import pytest

from flowboard.db import get_session
from flowboard.db.models import BoardFlowProject, Node, PipelineRun, Request
from flowboard.services import (
    character_ports,
    character_store,
    flow_sdk,
    pipeline_executor,
)
from flowboard.services import flow_batch as fb
from sqlmodel import select

from tests.flow_fakes import BatchFakeClient, operation_reply

#: A project uuid of the shape `is_valid_project_id` accepts.
_PROJECT = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path, monkeypatch):
    """The registry is files under `storage/`, so point it at a temp dir."""
    monkeypatch.setattr(character_store, "STORAGE_DIR", tmp_path)
    return tmp_path


# ── the registry ──────────────────────────────────────────────────────


def test_a_name_becomes_a_taggable_token():
    """`@@MaiAnh` is how the packaged workflows write it, so the name has to
    survive as one word — and accents have to fold rather than vanish, or two
    different characters become the same empty string."""
    assert character_store.normalize_name("Mai Anh") == "MaiAnh"
    assert character_store.normalize_name("Bà Trưng") == "BaTrung"
    assert character_store.normalize_name("Đạo Sĩ") == "DaoSi"
    assert character_store.normalize_name("!!!") == ""


def test_a_name_that_normalises_to_nothing_is_refused():
    with pytest.raises(ValueError, match="rỗng"):
        character_store.save("p1", character_store.Character(name="???"))


def test_two_characters_cannot_share_a_tag():
    """The tag matcher is case-insensitive, so `MaiAnh` and `maianh` are one
    character as far as a prompt is concerned — and `@@maianh` would have two
    answers."""
    character_store.save("p1", character_store.Character(name="MaiAnh"))
    with pytest.raises(ValueError, match="đã có nhân vật"):
        character_store.save("p1", character_store.Character(name="mai anh"))


def test_the_registry_is_per_project():
    character_store.save("p1", character_store.Character(name="MaiAnh"))
    assert [c.name for c in character_store.list_for("p1")] == ["MaiAnh"]
    assert character_store.list_for("p2") == []


def test_a_corrupt_registry_costs_the_entities_not_the_board(tmp_path):
    (tmp_path / "characters").mkdir()
    (tmp_path / "characters" / "p1.json").write_text("{not json", encoding="utf-8")
    assert character_store.list_for("p1") == []


def test_an_entity_id_belongs_to_the_project_it_was_made_in():
    record = character_store.Character(name="MaiAnh", entity_id="ent-1", project_id="p1")
    assert record.usable_in("p1") is True
    assert record.usable_in("p2") is False
    # Without an entity id there is nothing project-bound: it travels as an
    # uploaded image, and images are uploaded per project anyway.
    image_only = character_store.Character(name="Mẹ", project_id="p1")
    assert image_only.usable_in("p2") is True


# ── the tag rule ──────────────────────────────────────────────────────


def test_a_tag_needs_a_word_boundary():
    assert character_store.is_tagged("Cận cảnh @@MaiAnh bước vào", "MaiAnh")
    assert character_store.is_tagged("@Mai đứng đó", "Mai")
    # `@@Mai` must not match the character `MaiAnh`, or one tag pulls in two.
    assert not character_store.is_tagged("Cận cảnh @@MaiAnh", "Mai")
    assert not character_store.is_tagged("Cận cảnh người mẹ", "MaiAnh")


def test_an_untagged_entity_is_left_out_not_sent():
    """The packaged tool's own behaviour: an entity the prompt does not name has
    nothing to attach to, and sending it spends the lane's character budget on
    someone who does not appear."""
    a = character_store.save(
        "p1", character_store.Character(name="MaiAnh", entity_id="ent-a")
    )
    b = character_store.save(
        "p1", character_store.Character(name="Hung", entity_id="ent-b")
    )
    plan = character_store.plan_entities(
        [("character_1", a.id), ("character_2", b.id)],
        project_id="p1",
        prompt="Cận cảnh @@MaiAnh mở cửa",
    )
    assert plan.entity_ids == ["ent-a"]
    assert plan.skipped_untagged == ["Hung"]
    assert plan.refusal is None


def test_an_entity_from_another_project_is_refused_with_the_tools_own_reason():
    record = character_store.save(
        "p1", character_store.Character(name="MaiAnh", entity_id="ent-a")
    )
    plan = character_store.plan_entities(
        [("character_1", record.id)], project_id="p1", prompt="@@MaiAnh"
    )
    assert plan.entity_ids == ["ent-a"]

    # Same record, different project: the id is not active there.
    moved = character_store.Character(
        name="MaiAnh", entity_id="ent-a", project_id="p-old", id=record.id
    )
    character_store._write("p1", [moved])
    plan = character_store.plan_entities(
        [("character_1", record.id)], project_id="p1", prompt="@@MaiAnh"
    )
    assert plan.entity_ids == []
    assert plan.refusal and plan.refusal.startswith("character_wrong_project:")
    assert "CHƯA ACTIVE" in character_store.explain_refusal(plan.refusal)


def test_an_image_only_character_is_not_an_entity():
    """It is still a usable character — over the reference-image path — so it
    must not be reported as missing or refused."""
    record = character_store.save(
        "p1", character_store.Character(name="Me", media_id="m-1")
    )
    plan = character_store.plan_entities(
        [("character_1", record.id)], project_id="p1", prompt="@@Me"
    )
    assert plan.entity_ids == [] and plan.refusal is None and plan.unknown == []


def test_a_link_to_a_deleted_character_is_reported():
    plan = character_store.plan_entities(
        [("character_1", "gone")], project_id="p1", prompt="@@X"
    )
    assert plan.unknown == ["character_1:gone"]


def test_the_entity_order_follows_the_socket_number():
    """`character_10` must not sort between 1 and 2: the number is the order the
    characters appear in the prompt, so swapping two is a different video."""
    made = [
        character_store.save(
            "p1", character_store.Character(name=f"NV{i}", entity_id=f"ent-{i}")
        )
        for i in range(1, 12)
    ]
    links = [(f"character_{i}", made[i - 1].id) for i in (10, 2, 1, 11)]
    plan = character_store.plan_entities(
        [(p, c) for p, c in sorted(links, key=lambda x: character_ports.port_order(x[0]))],
        project_id="p1",
        prompt=" ".join(f"@@NV{i}" for i in (1, 2, 10, 11)),
    )
    assert plan.entity_ids == ["ent-1", "ent-2", "ent-10", "ent-11"]


# ── the wires ─────────────────────────────────────────────────────────


class _Wire:
    def __init__(self, node_type, port, data):
        self.node = type("N", (), {"type": node_type, "data": data})()
        self.port = port


def test_a_socket_reports_the_character_it_links_to():
    wires = [
        _Wire("character", "character_2", {"characterId": "b"}),
        _Wire("character", "character_1", {"characterId": "a"}),
        _Wire("character", "character_3", {"mediaId": "m-1"}),
    ]
    assert character_ports.collect_links(wires) == [
        ("character_1", "a"), ("character_2", "b")
    ]


def test_a_registered_character_needs_no_uploaded_still():
    """`unfilled` reports sockets that will never produce media. An entity is
    not media — reporting it as missing refused the one path that uses none."""
    wires = [_Wire("character", "character_1", {"characterId": "a"})]
    assert character_ports.unfilled(wires) == []
    wires = [_Wire("character", "character_1", {})]
    assert character_ports.unfilled(wires) == ["character_1"]


# ── the payload ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_sdk_refuses_entities_rather_than_dropping_them():
    """No entity slot on the batch path, so the dispatch is refused for free.

    This test used to assert the opposite: that `referenceEntities` rode beside
    `referenceImages` in the REST body. It did, and that transport is gone. The
    captured Omni payload has no entity slot, and the two ways to "cope" are
    both worse than an error -- dropping the entities renders a different
    character at full price, and swapping in the reference images renders
    whatever stills happen to be wired.

    The property that matters is the second assertion: nothing is sent. A
    refusal that still spends a captcha and a credit is not a refusal.
    """
    from flowboard.services import flow_sdk
    fake = BatchFakeClient()
    sdk = flow_sdk.FlowSDK(fake)
    out = await sdk.gen_video_omni(
        prompt="@@MaiAnh mở cửa",
        project_id="abcd1234",
        ref_media_ids=["media-1"],
        ref_entity_ids=["ent-a", "ent-b"],
        duration_s=4,
        paygate_tier="PAYGATE_TIER_ONE",
    )
    assert out["error"].startswith(flow_sdk.UNSUPPORTED_PREFIX + "reference_entities")
    assert fake.calls == []


@pytest.mark.asyncio
async def test_the_refusal_says_which_capture_is_missing():
    """A bare "unsupported" sends someone looking for a bug in their board."""
    from flowboard.services import flow_sdk
    sdk = flow_sdk.FlowSDK(BatchFakeClient())
    out = await sdk.gen_video_omni(
        prompt="x", project_id="abcd1234", ref_media_ids=["m-1"],
        ref_entity_ids=["ent-a"], duration_s=4,
    )
    assert "entity" in out["error"]
    # And it says what still works, so the reference-image path is not assumed
    # broken too.
    assert "tham chiếu" in out["error"]


@pytest.mark.asyncio
async def test_entities_alone_are_enough(monkeypatch):
    """The packaged tool's own guard reads "reference_media_ids or
    reference_entities is required" — either one. Refusing on images alone made
    the entity path unreachable even once the payload could carry it."""
    from flowboard.worker import processor

    dispatched: dict = {}

    class _Stub:
        async def gen_video_omni(self, **kwargs):
            dispatched.update(kwargs)
            return {"raw": {}, "operation_names": ["op-1"]}

    from flowboard.services import flow_sdk

    monkeypatch.setattr(flow_sdk, "_sdk", _Stub())

    async def _poll(sdk, dispatch, request_id):
        return {"media_ids": ["m-out"]}, None

    monkeypatch.setattr(processor, "_poll_video_dispatch", _poll)

    result, err = await processor._handle_gen_video_omni({
        "prompt": "@@MaiAnh mở cửa",
        "project_id": "abcd1234",
        "ref_entity_ids": ["ent-a"],
        "duration_s": 4,
        "paygate_tier": "PAYGATE_TIER_ONE",
    })
    assert err is None, err
    assert dispatched["ref_entity_ids"] == ["ent-a"]
    assert dispatched["ref_media_ids"] == []


@pytest.mark.asyncio
async def test_neither_images_nor_entities_is_still_refused():
    from flowboard.worker import processor

    _, err = await processor._handle_gen_video_omni({
        "prompt": "x", "project_id": "abcd1234", "duration_s": 4,
        "paygate_tier": "PAYGATE_TIER_ONE",
    })
    assert err == "missing_ref_media_ids"


# ── the run ───────────────────────────────────────────────────────────


def _run_board(client, board_id):
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


def test_the_executor_sends_the_tagged_entity(client, monkeypatch):
    """End to end on the board: a character node linked to a registered entity,
    tagged in the prompt, reaches the request as `ref_entity_ids`."""
    board = client.post("/api/boards", json={"name": "E"}).json()
    with get_session() as s:
        s.add(BoardFlowProject(board_id=board["id"], flow_project_id="abcd1234"))
        s.commit()
    record = character_store.save(
        "abcd1234", character_store.Character(name="MaiAnh", entity_id="ent-a")
    )

    char = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "character", "x": 0, "y": 0,
        "data": {"title": "MaiAnh", "characterId": record.id},
    }).json()
    video = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "video", "x": 300, "y": 0,
        "data": {
            "title": "Clip", "prompt": "Cận cảnh @@MaiAnh mở cửa",
            "sourceSettings": {"quality": "omni", "duration": 4},
        },
    }).json()
    client.post("/api/edges", json={
        "board_id": board["id"], "source_id": char["id"], "target_id": video["id"],
        "kind": "ref", "target_port": "character_1",
    })

    _run_board(client, board["id"])

    with get_session() as s:
        rows = s.exec(select(Request)).all()
        params = [r.params for r in rows]
    assert len(rows) == 1, params
    assert rows[0].type == "gen_video_omni"
    assert params[0]["ref_entity_ids"] == ["ent-a"]


def test_an_entity_from_another_project_stops_the_node_before_dispatch(client):
    board = client.post("/api/boards", json={"name": "E"}).json()
    with get_session() as s:
        s.add(BoardFlowProject(board_id=board["id"], flow_project_id="abcd1234"))
        s.commit()
    # Registered under a different project id, then wired here.
    stale = character_store.Character(
        name="MaiAnh", entity_id="ent-a", project_id="other-project"
    )
    character_store._write("abcd1234", [stale])

    char = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "character", "x": 0, "y": 0,
        "data": {"title": "MaiAnh", "characterId": stale.id},
    }).json()
    video = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "video", "x": 300, "y": 0,
        "data": {
            "title": "Clip", "prompt": "@@MaiAnh mở cửa",
            "sourceSettings": {"quality": "omni", "duration": 4},
        },
    }).json()
    client.post("/api/edges", json={
        "board_id": board["id"], "source_id": char["id"], "target_id": video["id"],
        "kind": "ref", "target_port": "character_1",
    })

    _run_board(client, board["id"])

    with get_session() as s:
        node = s.get(Node, video["id"])
        rows = s.exec(select(Request)).all()
    assert node.status == "error"
    assert (node.data or {}).get("error", "").startswith("character_wrong_project:")
    assert rows == [], "nothing may be dispatched for an entity that is not active"


# ── the reference key on the wire is the key that was reported ────────────


@pytest.mark.asyncio
async def test_the_veo_reference_lane_actually_reaches_the_wire():
    """Asserted on the ENVELOPE, because the old test asserted on a stand-in.

    `test_money_safety.py` replaces the whole SDK method with `_OmniSpy`, so it
    could only check which key the HANDLER passed in — and a prefix guard inside
    the real `gen_video_omni` then refused that key before any RPC. 101 tests were
    green while the lane was dead in production.

    Measured 20/09: `MZZa6b` reads the model from `request[2]` for a Veo name too
    (`[7]` MODEL_ACCESS_DENIED there, `[5]` for a bogus name in the same slot), so
    the slot is right and the refusal was wrong.
    """
    key = flow_sdk.VEO_R2V_LANES["lite_relaxed"]
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_VIDEO_REFERENCES, operation_reply("op-r2v", _PROJECT))
    out = await flow_sdk.FlowSDK(client=fake).gen_video_omni(
        prompt="cô gái cầm sản phẩm",
        project_id=_PROJECT,
        ref_media_ids=["m-face"],
        duration_s=8,
        model_key=key,
    )
    assert out.get("error") is None, out.get("error")
    calls = fake.calls_for(fb.RPC_GEN_VIDEO_REFERENCES)
    assert len(calls) == 1, "the lane was refused before reaching Flow"
    assert key in calls[0]["freq"], "the Veo key never made it onto the wire"
    assert out["model_key"] == key


@pytest.mark.asyncio
async def test_the_reported_reference_key_is_the_one_sent():
    """`omni_reference_video_request` rebuilt the key from duration+resolution and
    ignored the one it was handed, while the result reported the caller's.

    Measured divergence before the fix: reported `abra_r2v_4s_360p`, wire
    `abra_r2v_8s`. Harmless only because the single production caller derived the
    key from the same two inputs — coincidence, not construction. `review_loop`
    reads the reported field to decide whether re-running a clip is free.
    """
    fake = BatchFakeClient()
    fake.reply(fb.RPC_GEN_VIDEO_REFERENCES, operation_reply("op-1", _PROJECT))
    out = await flow_sdk.FlowSDK(client=fake).gen_video_omni(
        prompt="x", project_id=_PROJECT, ref_media_ids=["m-1"],
        duration_s=8, resolution="720p",
        # Deliberately inconsistent with duration/resolution: the point is that
        # the key wins, not that the arithmetic agrees.
        model_key="abra_r2v_4s_360p",
    )
    freq = fake.calls_for(fb.RPC_GEN_VIDEO_REFERENCES)[0]["freq"]
    assert out["model_key"] == "abra_r2v_4s_360p"
    assert "abra_r2v_4s_360p" in freq
    assert "abra_r2v_8s" not in freq, "the builder rebuilt its own key again"


@pytest.mark.asyncio
async def test_an_unmeasured_reference_key_is_still_refused_by_name():
    """The guard was widened to the measured Veo key, not to a prefix. A prefix
    would wave through every `veo_3_1_r2v_*` name, and the other three carry
    `portrait` and answered `[5]`."""
    fake = BatchFakeClient()
    out = await flow_sdk.FlowSDK(client=fake).gen_video_omni(
        prompt="x", project_id=_PROJECT, ref_media_ids=["m-1"],
        duration_s=8, model_key="veo_3_1_r2v_fast_portrait",
    )
    assert out["error"] and "veo_r2v_" in out["error"]
    assert fake.calls_for(fb.RPC_GEN_VIDEO_REFERENCES) == []
