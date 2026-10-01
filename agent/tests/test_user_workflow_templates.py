"""The packaged tool's own saved boards, not just the nine shipped samples.

`Workflows/` is where the exe keeps the user's personal projects — its "Dự án"
selector lists that folder, and `default_workflow.json` is the blank board it
opens with. This build only ever read `data_general/system_workflows/` plus the
`system_workflows.json` index, so a board the user had saved in the original
tool was invisible here: not listed, not readable, not importable.

Nothing about the file format differs (`{nodes, connections}`), so the fix is
about where the endpoint looks, and that is what these check. They run against
the real folder like `test_template_import.py` does, and skip when the packaged
tool is not installed.
"""
from __future__ import annotations

import json

import pytest

from flowboard.routes.templates import TEMPLATE_DIR, USER_TEMPLATE_DIR

USER_FILES = sorted(p.name for p in USER_TEMPLATE_DIR.glob("*.json")) if USER_TEMPLATE_DIR.is_dir() else []
SHIPPED_FILES = sorted(p.name for p in TEMPLATE_DIR.glob("*.json"))

needs_user_files = pytest.mark.skipif(
    not USER_FILES, reason="packaged tool's Workflows/ folder not installed"
)


@needs_user_files
def test_personal_boards_appear_in_the_listing(client):
    listed = {t["file"] for t in client.get("/api/templates").json()}
    missing = set(USER_FILES) - listed
    assert not missing, f"saved boards not listed: {sorted(missing)}"


@needs_user_files
def test_the_shipped_samples_are_still_listed(client):
    """The personal folder is additive. Losing the nine packaged samples in
    exchange would be a worse bug than the one being fixed."""
    listed = {t["file"] for t in client.get("/api/templates").json()}
    assert set(SHIPPED_FILES) <= listed


@needs_user_files
def test_a_personal_board_reads_back_with_its_steps(client):
    """Pick the richest saved board rather than the first: an empty one
    would pass a step-count assertion for the wrong reason."""
    richest = max(
        USER_FILES,
        key=lambda n: len(json.loads((USER_TEMPLATE_DIR / n).read_text(encoding="utf-8-sig")).get("nodes") or []),
    )
    doc = json.loads((USER_TEMPLATE_DIR / richest).read_text(encoding="utf-8-sig"))
    if not doc.get("nodes"):
        pytest.skip("every saved board is empty; nothing to read back")

    body = client.get(f"/api/templates/{richest}").json()
    assert len(body["steps"]) == len(doc["nodes"])


@needs_user_files
def test_a_personal_board_imports_onto_a_board(client):
    richest = max(
        USER_FILES,
        key=lambda n: len(json.loads((USER_TEMPLATE_DIR / n).read_text(encoding="utf-8-sig")).get("nodes") or []),
    )
    doc = json.loads((USER_TEMPLATE_DIR / richest).read_text(encoding="utf-8-sig"))
    if not doc.get("nodes"):
        pytest.skip("every saved board is empty; nothing to import")

    resp = client.post(f"/api/templates/{richest}/import")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["nodes"] == len(doc["nodes"]), "lost nodes on import"
    assert body["edges"] == len(doc["connections"]), "lost wires on import"


@needs_user_files
def test_an_empty_board_is_listed_rather_than_hidden(client):
    """`default_workflow.json` has zero nodes. It still belongs in the list —
    `stepCount: 0` tells the truth, whereas dropping it makes the folder look
    like it holds fewer boards than the user saved. Same choice this endpoint
    already made for a file it cannot parse."""
    empty = [
        n for n in USER_FILES
        if not (json.loads((USER_TEMPLATE_DIR / n).read_text(encoding="utf-8-sig")).get("nodes") or [])
    ]
    if not empty:
        pytest.skip("no empty saved board to check")
    listing = {t["file"]: t for t in client.get("/api/templates").json()}
    for name in empty:
        assert name in listing
        assert listing[name]["stepCount"] == 0


# ── the folder must not become a way out of the folder ────────────────


@pytest.mark.parametrize("name", [
    "../../../windows/win.ini",
    "..\\..\\secrets.json",
    "not-json.txt",
])
def test_traversal_and_non_json_are_refused(client, name):
    assert client.get(f"/api/templates/{name}").status_code in (400, 404)
