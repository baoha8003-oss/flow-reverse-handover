"""The packaged tool's sample workflows, served as recipes.

Two things carry the feature: putting the steps in execution order (the files
are drawn left-to-right but the coordinates lie — one board starts at
x = -3472), and pulling out the prompt text, which is the part worth having.
"""
from __future__ import annotations

import json

import pytest

from flowboard.routes import templates as mod


@pytest.fixture
def template_dir(tmp_path, monkeypatch):
    """Point every template source at one empty temp folder.

    All THREE sources are redirected, not only the packaged one. These tests
    assert on the WHOLE listing, so a source still aimed at real disk makes
    them fail on a machine that has the packaged tool installed — or, for the
    personal folder, on any machine where a template was ever saved.
    """
    from flowboard.services import board_templates

    monkeypatch.setattr(mod, "TEMPLATE_DIR", tmp_path)
    monkeypatch.setattr(mod, "INDEX_FILE", tmp_path / "index.json")
    monkeypatch.setattr(mod, "USER_TEMPLATE_DIR", tmp_path / "user-workflows")
    monkeypatch.setattr(board_templates, "MY_TEMPLATE_DIR", tmp_path / "mine")
    return tmp_path


def _write(dir_, name, doc, *, index_name=None, description=""):
    (dir_ / name).write_text(json.dumps(doc), encoding="utf-8")
    (dir_ / "index.json").write_text(
        json.dumps(
            [{"file": name, "name": index_name or name, "description": description}]
        ),
        encoding="utf-8",
    )


# ── ordering ─────────────────────────────────────────────────────────────

def test_steps_follow_the_connections_not_the_file_order():
    """The recipe is only useful if the steps are in the order they run."""
    nodes = [
        {"id": "c", "type": "gen_video"},
        {"id": "a", "type": "upload_media"},
        {"id": "b", "type": "gen_image"},
    ]
    connections = [
        {"srcNode": "a", "dstNode": "b"},
        {"srcNode": "b", "dstNode": "c"},
    ]
    assert [n["id"] for n in mod._order_nodes(nodes, connections)] == ["a", "b", "c"]


def test_unconnected_nodes_still_appear():
    """A node nothing points at is usually the input — dropping it would
    leave a recipe that cannot be followed."""
    nodes = [{"id": "a", "type": "text_prompt"}, {"id": "b", "type": "gen_image"}]
    assert len(mod._order_nodes(nodes, [])) == 2


def test_a_cycle_does_not_lose_nodes_or_hang():
    """Hand-edited boards can contain a loop; the recipe must still render."""
    nodes = [{"id": "a", "type": "x"}, {"id": "b", "type": "y"}]
    connections = [
        {"srcNode": "a", "dstNode": "b"},
        {"srcNode": "b", "dstNode": "a"},
    ]
    assert len(mod._order_nodes(nodes, connections)) == 2


def test_connections_to_unknown_nodes_are_ignored():
    nodes = [{"id": "a", "type": "x"}]
    out = mod._order_nodes(nodes, [{"srcNode": "ghost", "dstNode": "a"}])
    assert [n["id"] for n in out] == ["a"]


# ── prompt extraction ────────────────────────────────────────────────────

def test_a_real_prompt_is_extracted():
    long = "Use the reference image as the exact starting frame. " * 2
    assert mod._prompt_of({"settings": {"prompt": long}}) == long.strip()


def test_short_settings_values_are_not_mistaken_for_prompts():
    """`sub_font: "Bungee"` and a filename are settings, not something to
    copy into a prompt box."""
    assert mod._prompt_of({"settings": {"text": "Bungee"}}) is None
    assert mod._prompt_of({"settings": {"prompt": "out.mp4"}}) is None


def test_node_without_settings_is_fine():
    assert mod._prompt_of({"type": "upload_media"}) is None
    assert mod._prompt_of({"settings": None}) is None


# ── routes ───────────────────────────────────────────────────────────────

def test_list_reports_step_counts(client, template_dir):
    _write(
        template_dir,
        "w.json",
        {"nodes": [{"id": "a", "type": "gen_image"}], "connections": []},
        index_name="Mẫu thử",
        description="mô tả",
    )
    body = client.get("/api/templates").json()
    assert body == [
        {
            "file": "w.json", "name": "Mẫu thử", "description": "mô tả",
            "stepCount": 1,
            # A packaged sample. The pair matters: the UI decides whether to
            # offer rename/delete from `writable`, and offering it on one of
            # the exe's own files would delete something this app does not own.
            "source": "packaged", "writable": False,
        }
    ]


def test_detail_maps_each_step_to_a_screen(client, template_dir):
    """The whole point: a node type you cannot run here becomes an
    instruction telling you which tab does that job."""
    _write(
        template_dir,
        "w.json",
        {
            "nodes": [
                {"id": "a", "type": "merge_video", "label": "Ghép"},
                {"id": "b", "type": "edit_video", "label": "Sửa"},
            ],
            "connections": [{"srcNode": "a", "dstNode": "b"}],
        },
    )
    body = client.get("/api/templates/w.json").json()
    assert [s["type"] for s in body["steps"]] == ["merge_video", "edit_video"]
    assert "Cut & Merge" in body["steps"][0]["doneWith"]
    assert body["unmappedTypes"] == []


def test_detail_flags_a_type_with_no_equivalent(client, template_dir):
    """Better to say so than to invent an instruction."""
    _write(
        template_dir,
        "w.json",
        {"nodes": [{"id": "a", "type": "some_future_node"}], "connections": []},
    )
    body = client.get("/api/templates/w.json").json()
    assert body["unmappedTypes"] == ["some_future_node"]
    assert "Chưa có" in body["steps"][0]["doneWith"]


def test_template_name_cannot_escape_the_folder(client, template_dir, tmp_path):
    """The name arrives over HTTP and becomes a path."""
    (tmp_path.parent / "secret.json").write_text("{}", encoding="utf-8")
    for attack in ("../secret.json", "..%2Fsecret.json"):
        assert client.get(f"/api/templates/{attack}").status_code in (400, 404)


def test_non_json_name_is_refused(client, template_dir):
    assert client.get("/api/templates/passwd").status_code == 400


def test_missing_template_is_404(client, template_dir):
    (template_dir / "index.json").write_text("[]", encoding="utf-8")
    assert client.get("/api/templates/nope.json").status_code == 404


def test_a_missing_index_is_an_empty_list_not_a_crash(client, template_dir):
    assert client.get("/api/templates").json() == []


def test_an_unreadable_template_is_still_listed(client, template_dir):
    """Dropping it silently would leave the user wondering where it went."""
    (template_dir / "broken.json").write_text("{not json", encoding="utf-8")
    (template_dir / "index.json").write_text(
        json.dumps([{"file": "broken.json", "name": "Hỏng", "description": ""}]),
        encoding="utf-8",
    )
    body = client.get("/api/templates").json()
    assert body[0]["name"] == "Hỏng"
    assert body[0]["stepCount"] == 0


# ── the real shipped templates ───────────────────────────────────────────

def test_every_shipped_template_parses_and_is_fully_mapped(client):
    """Runs against the real files in the asset library. Every node type they
    use must map to a screen here — an unmapped one means the recipe has a
    hole, and this is where that gets noticed."""
    listing = client.get("/api/templates").json()
    if not listing:
        pytest.skip("asset library not present")
    checked = 0
    for entry in listing:
        # The listing now also carries the packaged tool's own saved boards,
        # and one of them (`default_workflow.json`) is its blank starting
        # board — genuinely zero nodes. It belongs in the listing, with
        # `stepCount: 0` saying so, but there is nothing in it to map.
        if entry["stepCount"] == 0:
            continue
        detail = client.get(f"/api/templates/{entry['file']}").json()
        assert detail["steps"], f"{entry['file']} produced no steps"
        assert detail["unmappedTypes"] == [], (
            f"{entry['file']} uses node types with no mapping: "
            f"{detail['unmappedTypes']}"
        )
        checked += 1
    assert checked, "every listed template was empty — the mapping went unchecked"
