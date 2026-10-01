"""The library index, the render byte route, and the SRT writer.

These three exist so the post-production feature is reachable at all from a
browser: the render endpoints answer with absolute filesystem paths (which a
page cannot open) and take input paths confined to server-side roots (which a
page cannot write to). What can go wrong here is not ffmpeg — it is path
confinement and content typing, so that is what these cover.
"""
from __future__ import annotations

import pytest

from flowboard.routes import postprod as routes


@pytest.fixture
def renders_dir(tmp_path, monkeypatch):
    """Point the renders folder at a scratch dir for the whole module."""
    out = tmp_path / "renders"
    out.mkdir()
    monkeypatch.setattr(routes, "OUTPUT_DIR", out)
    return out


# ── library index ────────────────────────────────────────────────────────

def test_library_lists_renders_with_a_servable_url(client, renders_dir):
    (renders_dir / "clip.mp4").write_bytes(b"\x00\x01")
    body = client.get("/api/postprod/library").json()
    names = {r["name"]: r for r in body["renders"]}
    assert "clip.mp4" in names
    row = names["clip.mp4"]
    assert row["kind"] == "video"
    # The URL is the whole point: without it the file is unreachable.
    assert row["url"] == "/renders/clip.mp4"
    assert row["sizeBytes"] == 2


def test_library_ignores_files_it_will_not_serve(client, renders_dir):
    (renders_dir / "notes.txt").write_text("not media")
    (renders_dir / "keep.mp4").write_bytes(b"x")
    names = {r["name"] for r in client.get("/api/postprod/library").json()["renders"]}
    assert "keep.mp4" in names
    assert "notes.txt" not in names


def test_library_survives_a_missing_folder(client, tmp_path, monkeypatch):
    """A fresh install has rendered nothing yet; that is not an error."""
    monkeypatch.setattr(routes, "OUTPUT_DIR", tmp_path / "never-created")
    r = client.get("/api/postprod/library")
    assert r.status_code == 200
    assert r.json()["renders"] == []


def test_library_is_newest_first_and_bounded(client, renders_dir, monkeypatch):
    import os
    import time

    monkeypatch.setattr(routes, "MAX_LIBRARY_ITEMS", 2)
    now = time.time()
    for i in range(4):
        p = renders_dir / f"c{i}.mp4"
        p.write_bytes(b"x")
        os.utime(p, (now + i, now + i))
    rows = client.get("/api/postprod/library").json()["renders"]
    assert [r["name"] for r in rows] == ["c3.mp4", "c2.mp4"]


# ── serving a render ─────────────────────────────────────────────────────

def test_render_bytes_are_served(client, renders_dir):
    (renders_dir / "done.mp4").write_bytes(b"video-bytes")
    r = client.get("/renders/done.mp4")
    assert r.status_code == 200, r.text
    assert r.content == b"video-bytes"
    assert r.headers["content-type"].startswith("video/")


@pytest.mark.parametrize("attack", ["%2e%2e%2fsecret.mp4", "..%2Fsecret.mp4"])
def test_render_route_refuses_to_escape_the_folder(
    client, renders_dir, tmp_path, attack
):
    """The name arrives over HTTP; `..` must not reach a sibling file.

    Percent-encoded, deliberately: an HTTP client collapses a literal
    `/renders/../secret.mp4` into `/secret.mp4` before it is ever sent, so
    that spelling tests the client rather than this route. These forms do
    arrive at the handler with `..` intact (verified against the running app).
    """
    (tmp_path / "secret.mp4").write_bytes(b"LEAKED")
    r = client.get(f"/renders/{attack}")
    assert r.status_code == 404
    assert b"LEAKED" not in r.content


def test_render_route_refuses_a_non_media_file(client, renders_dir):
    """Whatever else lands in the folder is not handed to a browser."""
    (renders_dir / "secrets.json").write_text('{"token": "x"}')
    assert client.get("/renders/secrets.json").status_code == 404


def test_missing_render_is_404(client, renders_dir):
    assert client.get("/renders/nope.mp4").status_code == 404


# ── the SRT writer ───────────────────────────────────────────────────────

SRT = "1\n00:00:00,000 --> 00:00:02,000\nXin chào\n"


def test_srt_is_written_and_reusable_as_a_subtitle_input(client, renders_dir):
    r = client.post("/api/postprod/srt", json={"name": "phude", "text": SRT})
    assert r.status_code == 200, r.text
    path = renders_dir / "phude.srt"
    assert path.is_file()
    # Extension is forced so the caller cannot land a .txt that ffmpeg's
    # subtitles filter would refuse.
    assert r.json()["path"] == str(path)


def test_srt_keeps_the_extension_when_given(client, renders_dir):
    client.post("/api/postprod/srt", json={"name": "a.srt", "text": SRT})
    assert (renders_dir / "a.srt").is_file()
    assert not (renders_dir / "a.srt.srt").exists()


def test_srt_is_written_with_a_bom(client, renders_dir):
    """Without the BOM, ffmpeg reads UTF-8 as the system codepage on Windows
    and burns mojibake into the video permanently."""
    client.post("/api/postprod/srt", json={"name": "vn", "text": SRT})
    raw = (renders_dir / "vn.srt").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert "Xin chào" in raw.decode("utf-8-sig")


def test_srt_name_cannot_escape_the_folder(client, renders_dir):
    r = client.post(
        "/api/postprod/srt", json={"name": "../escaped", "text": SRT}
    )
    assert r.status_code == 400
    assert not (renders_dir.parent / "escaped.srt").exists()


def test_srt_rejects_empty_and_oversized_text(client, renders_dir):
    assert client.post(
        "/api/postprod/srt", json={"name": "x", "text": ""}
    ).status_code == 422
    huge = "a" * (routes.MAX_SRT_CHARS + 1)
    assert client.post(
        "/api/postprod/srt", json={"name": "x", "text": huge}
    ).status_code == 422
