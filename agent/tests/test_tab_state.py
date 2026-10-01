"""What a tab remembers between sessions.

The packaged tool keeps one state file per tab and they are **global**, not per
project — the files carry `project_index` / `last_project` inside them. These
tests pin that shape, and pin the two refusals that keep a form-memory endpoint
from becoming a general-purpose disk writer: the tab name must be a name (it
becomes a filename), and a tab may not store data in it.
"""
from __future__ import annotations

import json

import pytest

from flowboard.config import STORAGE_DIR
from flowboard.services import tab_state


@pytest.fixture(autouse=True)
def _clean_tab_state():
    directory = STORAGE_DIR / "tab-state"
    if directory.is_dir():
        for path in directory.iterdir():
            path.unlink()
    yield


# ── the round trip ────────────────────────────────────────────────────


def test_a_tab_reads_back_what_it_wrote(client):
    body = {"state": {"url": "https://shop.example/p/1", "shot": "full", "variants": 3}}
    assert client.put("/api/tab-state/affiliate", json=body).status_code == 200
    assert client.get("/api/tab-state/affiliate").json()["state"] == body["state"]


def test_an_unknown_tab_reads_empty_not_404(client):
    """A tab mounting for the first time must not have to handle an error to
    learn it has no memory yet."""
    resp = client.get("/api/tab-state/video-clone")
    assert resp.status_code == 200
    assert resp.json()["state"] == {}


def test_saving_replaces_rather_than_merges(client):
    """A form that cleared a field would otherwise keep the old value forever,
    and "why is this box still filled in" is worse than a lost keystroke."""
    client.put("/api/tab-state/affiliate", json={"state": {"url": "a", "note": "ghi chú"}})
    client.put("/api/tab-state/affiliate", json={"state": {"url": "a"}})
    assert client.get("/api/tab-state/affiliate").json()["state"] == {"url": "a"}


def test_two_tabs_do_not_share_memory(client):
    client.put("/api/tab-state/affiliate", json={"state": {"url": "a"}})
    client.put("/api/tab-state/video-clone", json={"state": {"url": "b"}})
    assert client.get("/api/tab-state/affiliate").json()["state"]["url"] == "a"
    assert client.get("/api/tab-state/video-clone").json()["state"]["url"] == "b"


def test_the_state_is_global_not_per_board(client):
    """The exe keeps `project_index` INSIDE the file rather than a file per
    project. Storing the project as a value is the whole point — a tab is not
    scoped to a board here either."""
    board_a = client.post("/api/boards", json={"name": "A"}).json()["id"]
    board_b = client.post("/api/boards", json={"name": "B"}).json()["id"]
    client.put("/api/tab-state/affiliate", json={"state": {"project_index": board_a}})
    assert client.get("/api/tab-state/affiliate").json()["state"]["project_index"] == board_a
    assert board_b != board_a  # two boards exist; one memory serves both


def test_clearing_and_listing(client):
    client.put("/api/tab-state/affiliate", json={"state": {"url": "a"}})
    client.put("/api/tab-state/video-clone", json={"state": {"url": "b"}})
    assert client.get("/api/tab-state").json()["tabs"] == ["affiliate", "video-clone"]

    assert client.delete("/api/tab-state/affiliate").json()["cleared"] is True
    assert client.delete("/api/tab-state/affiliate").json()["cleared"] is False
    assert client.get("/api/tab-state").json()["tabs"] == ["video-clone"]


def test_vietnamese_text_survives_the_round_trip(client):
    note = "Áo dài lụa — quay cận tay áo, giữ nếp gấp"
    client.put("/api/tab-state/affiliate", json={"state": {"note": note}})
    assert client.get("/api/tab-state/affiliate").json()["state"]["note"] == note
    # And on disk, not escaped into \u sequences that a human cannot read.
    raw = (STORAGE_DIR / "tab-state" / "affiliate.json").read_text(encoding="utf-8")
    assert note in raw


# ── the refusals ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tab",
    [
        "../settings",
        "..%2Fsettings",
        "Affiliate",          # the name becomes a filename; case would collide
        "tab with spaces",
        "",
        "-leading-dash",
        "a" * 41,
    ],
)
def test_a_tab_name_that_is_not_a_name_is_refused(tab):
    """The name arrives in a URL and becomes a filename. Anything outside the
    allowed shape is either a typo or an attempt to write elsewhere."""
    with pytest.raises(tab_state.TabStateError):
        tab_state.save(tab, {"x": 1})
    with pytest.raises(tab_state.TabStateError):
        tab_state.load(tab)


def test_a_traversing_tab_name_writes_nothing_outside_the_tab_dir(client):
    """httpx folds `../` before the request leaves, so go at the service
    directly — that is the layer that has to hold."""
    victim = STORAGE_DIR / "settings.json"
    before = victim.read_text(encoding="utf-8") if victim.is_file() else None
    with pytest.raises(tab_state.TabStateError):
        tab_state.save("../settings", {"pwned": True})
    after = victim.read_text(encoding="utf-8") if victim.is_file() else None
    assert after == before


def test_a_tab_that_tries_to_store_data_is_refused(client):
    """A tab may remember the boxes someone typed in. It may not turn the disk
    into its scratch space."""
    payload = {"state": {"blob": "x" * (tab_state.MAX_BYTES + 1)}}
    resp = client.put("/api/tab-state/affiliate", json=payload)
    assert resp.status_code == 422
    assert "quá lớn" in resp.json()["detail"]
    # And nothing was written — the refusal is before the write, not after.
    assert client.get("/api/tab-state/affiliate").json()["state"] == {}


def test_too_many_keys_is_refused(client):
    payload = {"state": {f"k{i}": i for i in range(tab_state.MAX_KEYS + 1)}}
    resp = client.put("/api/tab-state/affiliate", json=payload)
    assert resp.status_code == 422
    assert str(tab_state.MAX_KEYS) in resp.json()["detail"]


def test_the_packaged_key_count_fits(client):
    """25 keys for `affiliate_state` and 13 for `data_clone` — the ceiling has
    to be above the thing it was measured from."""
    assert tab_state.MAX_KEYS >= 25
    payload = {"state": {f"k{i}": f"v{i}" for i in range(25)}}
    assert client.put("/api/tab-state/affiliate", json=payload).status_code == 200


def test_a_non_object_state_is_refused(client):
    assert client.put("/api/tab-state/affiliate", json={"state": ["a"]}).status_code == 422


# ── what a corrupt file costs ─────────────────────────────────────────


def test_a_corrupt_file_costs_the_memory_not_the_tab(client):
    directory = STORAGE_DIR / "tab-state"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "affiliate.json").write_text("{not json", encoding="utf-8")
    assert client.get("/api/tab-state/affiliate").json()["state"] == {}
    # And the tab can write over it.
    client.put("/api/tab-state/affiliate", json={"state": {"url": "a"}})
    assert client.get("/api/tab-state/affiliate").json()["state"] == {"url": "a"}


def test_a_json_scalar_on_disk_reads_as_empty(client):
    directory = STORAGE_DIR / "tab-state"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "affiliate.json").write_text('"a string"', encoding="utf-8")
    assert tab_state.load("affiliate") == {}


def test_the_write_is_atomic_leaving_no_tmp_behind(client):
    client.put("/api/tab-state/affiliate", json={"state": {"url": "a"}})
    directory = STORAGE_DIR / "tab-state"
    assert sorted(p.name for p in directory.iterdir()) == ["affiliate.json"]
    # Valid JSON, not a half-written file.
    json.loads((directory / "affiliate.json").read_text(encoding="utf-8"))


def test_a_failed_write_leaves_the_previous_memory_intact(client, monkeypatch):
    """Write-then-rename, not write-in-place: a crash between the two must cost
    the new value, never the old one. Without the rename the tab would come
    back from a failed save with a truncated file."""
    import pathlib

    client.put("/api/tab-state/affiliate", json={"state": {"url": "đã lưu"}})

    def boom(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(pathlib.Path, "replace", boom)
    with pytest.raises(OSError):
        tab_state.save("affiliate", {"url": "nửa đường"})
    assert tab_state.load("affiliate") == {"url": "đã lưu"}
