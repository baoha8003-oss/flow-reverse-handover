"""The bundled fashion-posing prompt library.

47 hand-written Veo prompts, ~5KB each, in a 252KB markdown file in the
asset root. The packaged tool's affiliate tab picks from them by id and
stores the choice as `selected_fashion_prompt_id: "fashion_male_07"`.

Importing a fashion workflow never needed this: `prompt_mau` nodes carry the
whole prompt inline and `template_import` already maps them to a `prompt`
node. Measured by diffing `thoi_trang_nam_3_canh.json`'s node text against
library entry 7 — identical to the character once the file's `====`
separator is dropped, which is what `test_the_workflow_id_scheme_resolves`
below pins.
"""
from __future__ import annotations

import pytest

from flowboard.services import prompt_library

pytestmark = pytest.mark.skipif(
    not prompt_library.entries(),
    reason="fashion prompt library not present under the asset root",
)


def test_every_prompt_in_the_file_is_parsed():
    assert len(prompt_library.entries()) == 47


def test_ids_follow_the_packaged_tools_scheme():
    """`fashion_male_07`, zero-padded — the spelling the workflows store."""
    ids = [e.id for e in prompt_library.entries()]
    assert "fashion_male_07" in ids
    assert "fashion_male_47" in ids


def test_the_workflow_id_scheme_resolves():
    entry = prompt_library.get("fashion_male_07")
    assert entry is not None and entry.number == 7


def test_an_unpadded_id_resolves_too():
    """A caller composing an id from an integer would write
    `fashion_male_7`; failing that lookup would be a puzzle, not an error."""
    assert prompt_library.get("fashion_male_7") == prompt_library.get(
        "fashion_male_07"
    )


def test_an_unknown_id_is_none_not_the_first_entry():
    assert prompt_library.get("fashion_male_99") is None
    assert prompt_library.get("") is None


def test_the_separator_rule_is_not_part_of_the_prompt():
    """Entries are divided by a rule of equals signs. Left in, it would be
    sent to Veo as part of the instruction."""
    for entry in prompt_library.entries():
        assert "=====" not in entry.body


def test_a_body_is_a_whole_prompt_not_a_heading():
    entry = prompt_library.get("fashion_male_07")
    assert entry is not None
    assert len(entry.body) > 3000
    assert "MOTION TIMELINE" in entry.body


def test_the_summary_drops_the_prompt_number_prefix():
    """The heading reads "PROMPT 7 — <mô tả>"; a dropdown wants the words
    after the dash, not the number it already shows."""
    entry = prompt_library.get("fashion_male_07")
    assert entry is not None
    assert not entry.summary.upper().startswith("PROMPT")
    assert entry.summary


# ── the routes ────────────────────────────────────────────────────────


def test_the_listing_carries_headings_but_no_bodies(client):
    """47 x 5KB would be a quarter of a megabyte to populate a dropdown the
    user reads three words of."""
    r = client.get("/api/prompt/library")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 47
    assert "body" not in rows[0]
    assert rows[0]["summary"]


def test_one_prompt_comes_back_whole(client):
    r = client.get("/api/prompt/library/fashion_male_07")
    assert r.status_code == 200
    assert "MOTION TIMELINE" in r.json()["body"]


def test_an_unknown_prompt_is_a_404(client):
    assert client.get("/api/prompt/library/fashion_male_99").status_code == 404
