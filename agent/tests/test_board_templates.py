"""Saving a board as a template, and the two folders that must never change.

The packaged tool's nine samples and its own `Workflows/` folder are read
through the same list as personal templates, which is convenient and exactly
why the write endpoints need holding down: one path-shaped file name away
from "delete template" is "delete one of the exe's files", and there is no
undo for that from here.

The other thing under test is the format choice. Saving through the exe's
`{nodes, connections}` shape would be tidier and would silently drop five
canvas node types that have no exe counterpart — so the round-trip test
below deliberately includes every one of them.
"""
from __future__ import annotations

import json

import pytest
from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import Edge, Node
from flowboard.services import board_templates


@pytest.fixture(autouse=True)
def mine_dir(tmp_path, monkeypatch):
    """Personal templates in a temp folder — these tests write and delete."""
    monkeypatch.setattr(board_templates, "MY_TEMPLATE_DIR", tmp_path / "mine")
    return tmp_path / "mine"


@pytest.fixture
def packaged(tmp_path, monkeypatch):
    """A stand-in for the exe's read-only folders, with one file in it."""
    from flowboard.routes import templates as mod

    root = tmp_path / "packaged"
    root.mkdir()
    (root / "sample.json").write_text(
        json.dumps({"nodes": [{"id": "a", "type": "gen_image"}], "connections": []}),
        encoding="utf-8",
    )
    # The packaged samples are listed from the index, not by globbing — a
    # file without an entry is invisible, so the fixture needs both.
    (root / "index.json").write_text(
        json.dumps([{"file": "sample.json", "name": "Mẫu gốc", "description": ""}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(mod, "TEMPLATE_DIR", root)
    monkeypatch.setattr(mod, "INDEX_FILE", root / "index.json")
    monkeypatch.setattr(mod, "USER_TEMPLATE_DIR", tmp_path / "user-workflows")
    return root


def _board_with_every_type(client) -> int:
    """A board using the five types the exe format cannot represent, plus
    the ones it can. If a save loses any of these, the round-trip fails."""
    board = client.post("/api/boards", json={"name": "Bảng đủ loại"}).json()
    ids = {}
    for node_type in ("character", "note", "Storyboard", "review_video",
                      "remove_watermark", "image", "video", "prompt"):
        node = client.post("/api/nodes", json={
            "board_id": board["id"], "type": node_type, "x": 10, "y": 20,
            "data": {"title": f"N-{node_type}", "prompt": "xin chào"},
        }).json()
        ids[node_type] = node["id"]
    client.post("/api/edges", json={
        "board_id": board["id"],
        "source_id": ids["image"], "target_id": ids["video"],
        "target_port": "start_frame",
    })
    return board["id"]


# ── the round trip ────────────────────────────────────────────────────


def test_a_saved_board_comes_back_with_every_node(client):
    """The reason the format is ours and not the exe's. `NODE_TYPE_MAP` has
    no entry for character / note / Storyboard / review_video /
    remove_watermark, so saving through it would drop five of these eight."""
    board_id = _board_with_every_type(client)
    saved = client.post("/api/templates", json={
        "board_id": board_id, "name": "Mẫu đủ loại",
    }).json()
    assert saved["stepCount"] == 8

    imported = client.post(f"/api/templates/{saved['file']}/import").json()
    with get_session() as s:
        types = sorted(
            n.type for n in
            s.exec(select(Node).where(Node.board_id == imported["boardId"])).all()
        )
    assert types == sorted([
        "character", "note", "Storyboard", "review_video",
        "remove_watermark", "image", "video", "prompt",
    ])


def test_the_wire_and_its_port_survive(client):
    """A port name is what tells a start frame from a reference. A template
    that keeps the wire but loses the socket rebuilds a different board."""
    board_id = _board_with_every_type(client)
    saved = client.post("/api/templates", json={
        "board_id": board_id, "name": "Mẫu dây",
    }).json()
    imported = client.post(f"/api/templates/{saved['file']}/import").json()
    with get_session() as s:
        edges = list(s.exec(
            select(Edge).where(Edge.board_id == imported["boardId"])
        ).all())
    assert len(edges) == 1
    assert edges[0].target_port == "start_frame"


def test_a_template_does_not_carry_another_boards_pictures(client):
    """`mediaId` describes one run, not the recipe. Kept, every board made
    from the template would open showing images it does not own."""
    board = client.post("/api/boards", json={"name": "B"}).json()
    client.post("/api/nodes", json={
        "board_id": board["id"], "type": "image", "x": 0, "y": 0,
        "data": {"title": "Ảnh", "prompt": "mèo", "mediaId": "m-abc",
                 "mediaIds": ["m-abc"], "renderedAt": "2026-09-01"},
    })
    saved = client.post("/api/templates", json={
        "board_id": board["id"], "name": "Mẫu sạch",
    }).json()
    doc = client.get(f"/api/templates/{saved['file']}/export").json()
    data = doc["nodes"][0]["data"]
    assert data["prompt"] == "mèo"
    assert "mediaId" not in data and "mediaIds" not in data
    assert "renderedAt" not in data


def test_an_imported_template_is_runnable_without_a_second_plan_step(client):
    """Both importers must produce the same run surface. A personal template
    that imports without a Plan is a board whose Run button does nothing."""
    board_id = _board_with_every_type(client)
    saved = client.post("/api/templates", json={
        "board_id": board_id, "name": "Mẫu chạy được",
    }).json()
    imported = client.post(f"/api/templates/{saved['file']}/import").json()
    assert imported["planId"]
    plan = client.get(f"/api/plans/{imported['planId']}").json()
    assert len(plan["spec"]["_materialized_node_ids"]) == 8


# ── the read-only folders ─────────────────────────────────────────────


def test_a_packaged_sample_cannot_be_deleted(client, packaged):
    """The nine samples belong to the exe. Nothing here may remove one, and
    the delete route reaches only `storage/templates` by construction."""
    resp = client.delete("/api/templates/mine/sample.json")
    assert resp.status_code == 404
    assert (packaged / "sample.json").exists()


def test_a_packaged_sample_cannot_be_renamed(client, packaged):
    resp = client.patch("/api/templates/mine/sample.json", json={"name": "Đổi"})
    assert resp.status_code == 404
    doc = json.loads((packaged / "sample.json").read_text(encoding="utf-8"))
    assert "name" not in doc


@pytest.mark.parametrize("name", [
    "../secret.json",
    "..\\secret.json",
    "sub/dir/x.json",
    "notjson.txt",
])
def test_a_path_shaped_name_reaches_nothing(client, name):
    for verb in (client.delete,):
        resp = verb(f"/api/templates/mine/{name}")
        assert resp.status_code in (400, 404, 405), name


def test_a_symlink_planted_in_the_folder_cannot_delete_its_target(client, tmp_path, mine_dir):
    """The case a bare-name check does NOT cover, and the reason the
    containment test exists at all.

    `Path(file).name` strips directories, so "../x.json" is harmless by the
    time it is resolved. A symlink is different: the name is bare, the path
    LOOKS like it is inside the folder, and `resolve()` then follows it
    somewhere else — where `unlink()` would delete the real file.
    """
    mine_dir.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "important.json"
    outside.write_text("{}", encoding="utf-8")
    link = mine_dir / "innocent.json"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privileges this account does not have")

    resp = client.delete("/api/templates/mine/innocent.json")
    assert resp.status_code == 400
    assert outside.exists(), "a symlink walked the delete out of the folder"


def test_the_listing_says_which_ones_can_be_changed(client, packaged):
    """The UI decides whether to show rename/delete from this flag. Getting
    it wrong offers a delete button for one of the exe's own files."""
    client.post("/api/templates", json={"board_id": _make_empty(client), "name": "Của tôi"})
    listing = client.get("/api/templates").json()
    by_source = {e["source"]: e for e in listing}
    assert by_source["mine"]["writable"] is True
    assert by_source["packaged"]["writable"] is False


def _make_empty(client) -> int:
    board = client.post("/api/boards", json={"name": "B"}).json()
    client.post("/api/nodes", json={
        "board_id": board["id"], "type": "prompt", "x": 0, "y": 0,
        "data": {"title": "P"},
    })
    return board["id"]


# ── the socket a wire plugs into ──────────────────────────────────────


def test_a_hand_drawn_wire_can_name_its_socket(client):
    """Ports used to arrive only from template import, so the same two nodes
    wired the same way behaved differently depending on where the board came
    from — and the executor tells a start frame from a reference by port."""
    board = client.post("/api/boards", json={"name": "B"}).json()
    a = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "image", "x": 0, "y": 0, "data": {"title": "A"},
    }).json()
    b = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "video", "x": 0, "y": 0, "data": {"title": "B"},
    }).json()
    client.post("/api/edges", json={
        "board_id": board["id"], "source_id": a["id"], "target_id": b["id"],
        "target_port": "start_frame",
    })
    with get_session() as s:
        edge = s.exec(select(Edge).where(Edge.board_id == board["id"])).first()
    assert edge.target_port == "start_frame"


def test_a_blank_socket_name_is_no_socket(client):
    """An empty string is a port no node has, so every lookup for it misses —
    which is a silent no-match rather than the "unwired" the user meant."""
    board = client.post("/api/boards", json={"name": "B"}).json()
    a = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "image", "x": 0, "y": 0, "data": {"title": "A"},
    }).json()
    b = client.post("/api/nodes", json={
        "board_id": board["id"], "type": "video", "x": 0, "y": 0, "data": {"title": "B"},
    }).json()
    client.post("/api/edges", json={
        "board_id": board["id"], "source_id": a["id"], "target_id": b["id"],
        "target_port": "   ",
    })
    with get_session() as s:
        edge = s.exec(select(Edge).where(Edge.board_id == board["id"])).first()
    assert edge.target_port is None


# ── rename, edit, delete ──────────────────────────────────────────────


def test_rename_changes_the_name_and_leaves_the_file_alone(client):
    saved = client.post("/api/templates", json={
        "board_id": _make_empty(client), "name": "Tên cũ",
    }).json()
    client.patch(f"/api/templates/mine/{saved['file']}",
                 json={"name": "Tên mới", "description": "mô tả"})
    listing = {e["file"]: e for e in client.get("/api/templates").json()}
    assert listing[saved["file"]]["name"] == "Tên mới"
    assert listing[saved["file"]]["description"] == "mô tả"


def test_editing_replaces_the_graph_but_keeps_the_name(client):
    """"Edit" is: open it, rearrange, save back. The name was chosen once."""
    saved = client.post("/api/templates", json={
        "board_id": _make_empty(client), "name": "Giữ tên này",
    }).json()
    bigger = _board_with_every_type(client)
    updated = client.put(f"/api/templates/mine/{saved['file']}",
                         json={"board_id": bigger}).json()
    assert updated["name"] == "Giữ tên này"
    assert updated["stepCount"] == 8


def test_deleting_removes_it_from_the_listing(client):
    saved = client.post("/api/templates", json={
        "board_id": _make_empty(client), "name": "Sẽ xoá",
    }).json()
    assert client.delete(f"/api/templates/mine/{saved['file']}").status_code == 200
    assert saved["file"] not in {e["file"] for e in client.get("/api/templates").json()}


def test_two_templates_with_the_same_name_do_not_overwrite_each_other(client):
    """Silently overwriting loses a template the user saved earlier and
    never says so."""
    board = _make_empty(client)
    first = client.post("/api/templates", json={"board_id": board, "name": "Trùng"}).json()
    second = client.post("/api/templates", json={"board_id": board, "name": "Trùng"}).json()
    assert first["file"] != second["file"]
    files = {e["file"] for e in client.get("/api/templates").json()}
    assert first["file"] in files and second["file"] in files


def test_a_name_that_is_only_punctuation_is_refused(client):
    resp = client.post("/api/templates", json={"board_id": _make_empty(client), "name": "///"})
    assert resp.status_code == 400


def test_vietnamese_names_keep_their_diacritics(client):
    """Stripping them would make "Kịch bản" and "Kich ban" the same file —
    a collision the user did not ask for, in the language they name things."""
    saved = client.post("/api/templates", json={
        "board_id": _make_empty(client), "name": "Kịch bản",
    }).json()
    assert "Kịch bản" in saved["file"]


# ── import from a file ────────────────────────────────────────────────


def test_a_workflow_file_from_the_exe_can_be_imported(client):
    """The file a user is most likely to already have on disk."""
    resp = client.post("/api/templates/import-json", json={
        "document": {
            "nodes": [{"id": "a", "type": "gen_image", "settings": {}}],
            "connections": [],
        },
        "name": "Từ tool gốc",
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["stepCount"] == 1


def test_our_own_export_can_be_imported_back(client):
    saved = client.post("/api/templates", json={
        "board_id": _board_with_every_type(client), "name": "Vòng tròn",
    }).json()
    doc = client.get(f"/api/templates/{saved['file']}/export").json()
    resp = client.post("/api/templates/import-json",
                       json={"document": doc, "name": "Nhập lại"})
    assert resp.status_code == 200
    assert resp.json()["stepCount"] == 8


def test_a_json_file_that_is_not_a_workflow_is_refused(client):
    resp = client.post("/api/templates/import-json",
                       json={"document": {"hello": "world"}})
    assert resp.status_code == 400


def test_a_shape_we_cannot_read_says_which_two_are_accepted(client):
    """"Invalid file" leaves the user guessing. Naming both formats tells
    them what to go and find."""
    resp = client.post("/api/templates/import-json", json={
        "document": {"nodes": [{"id": "a"}], "somethingElse": []},
    })
    assert resp.status_code == 400
    assert "connections" in resp.json()["detail"]
