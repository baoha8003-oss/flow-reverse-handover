"""Excel batch import/export.

Column matching is the part that silently does the wrong thing: a sheet whose
columns moved, or whose headers are Vietnamese, must still import — otherwise
the feature works only for spreadsheets this code happened to be tested with.
"""
from __future__ import annotations

import io

import pytest


def _sheet(rows: list[list]) -> bytes:
    import openpyxl

    book = openpyxl.Workbook()
    for row in rows:
        book.active.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _upload(client, data: bytes, name: str = "b.xlsx"):
    return client.post(
        "/api/batch/parse",
        files={"file": (name, data, "application/octet-stream")},
    )


# ── import ───────────────────────────────────────────────────────────────

def test_reads_vietnamese_headers():
    """The sheets people actually have are written in Vietnamese."""
    from flowboard.routes.batch import _header_map

    found = _header_map(("Nội dung", "Tỉ lệ", "Thời lượng", "Mô hình"))
    assert found.keys() >= {"prompt", "aspect", "duration", "model"}


def test_columns_are_matched_by_name_not_position(client):
    """A reordered sheet must still import — position matching would quietly
    read the duration column as the prompt."""
    data = _sheet(
        [["Model", "Prompt", "Aspect"], ["fast", "a cat on a wall", "16:9"]]
    )
    body = _upload(client, data).json()
    assert body["rows"][0]["prompt"] == "a cat on a wall"
    assert body["rows"][0]["model"] == "fast"


def test_aspect_aliases_become_backend_tokens(client):
    """'9:16' and 'ngang' are what a person writes; the dispatch needs the
    VIDEO_ASPECT_RATIO_* token."""
    data = _sheet([["Prompt", "Aspect"], ["a", "9:16"], ["b", "ngang"]])
    rows = _upload(client, data).json()["rows"]
    assert rows[0]["aspect"] == "VIDEO_ASPECT_RATIO_PORTRAIT"
    assert rows[1]["aspect"] == "VIDEO_ASPECT_RATIO_LANDSCAPE"


def test_blank_rows_are_skipped_not_dispatched(client):
    """A spacer row would otherwise become an empty paid generation."""
    data = _sheet([["Prompt"], ["a"], [None], ["   "], ["b"]])
    body = _upload(client, data).json()
    assert [r["prompt"] for r in body["rows"]] == ["a", "b"]
    assert body["skipped"] == 2


def test_a_sheet_with_no_prompt_column_says_which_names_work(client):
    data = _sheet([["Foo", "Bar"], ["x", "y"]])
    resp = _upload(client, data)
    assert resp.status_code == 400
    assert "prompt" in resp.json()["detail"].lower()


def test_a_sheet_with_no_usable_rows_is_rejected(client):
    data = _sheet([["Prompt"], [None], [""]])
    assert _upload(client, data).status_code == 400


def test_a_non_xlsx_upload_is_rejected(client):
    assert _upload(client, b"this is not a spreadsheet").status_code == 400


def test_an_empty_upload_is_rejected(client):
    assert _upload(client, b"").status_code == 400


def test_row_count_is_bounded(client):
    """Every row is one paid generation, so the ceiling is a cost guard."""
    from flowboard.routes import batch

    data = _sheet([["Prompt"]] + [[f"p{i}"] for i in range(batch.MAX_ROWS + 50)])
    body = _upload(client, data).json()
    assert len(body["rows"]) == batch.MAX_ROWS


def test_a_non_numeric_duration_is_dropped_not_fatal(client):
    """A stray 'tám giây' in the column should not fail the whole import."""
    data = _sheet([["Prompt", "Duration"], ["a", "tám giây"], ["b", 8]])
    rows = _upload(client, data).json()["rows"]
    assert rows[0]["duration"] is None
    assert rows[1]["duration"] == 8


# ── export ───────────────────────────────────────────────────────────────

def test_export_returns_a_real_workbook(client):
    resp = client.get("/api/batch/export?limit=5")
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"  # xlsx is a zip
    assert "attachment" in resp.headers["content-disposition"]

    import openpyxl

    book = openpyxl.load_workbook(io.BytesIO(resp.content))
    header = [c.value for c in book.active[1]]
    assert header[:4] == ["ID", "Loai", "Trang thai", "Prompt"]


@pytest.mark.parametrize("limit", [0, -5, 99999])
def test_export_clamps_the_limit(client, limit):
    """A caller-supplied limit reaches a SQL LIMIT; unbounded is a way to ask
    the app to build an arbitrarily large workbook in memory."""
    assert client.get(f"/api/batch/export?limit={limit}").status_code == 200
