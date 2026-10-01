"""The worker reaches ffmpeg by a route that asked nobody.

`routes/postprod` confines every input path to `_input_roots()` for a stated
reason: without it "every endpoint that takes a file path is an
arbitrary-file-read primitive". The worker calls the same ffmpeg functions with
paths out of a board's settings, and one of them — the aspect pass's background
image — went straight through.

The threat is small and real: a board or an imported workflow names
`C:/Users/.../.ssh/id_rsa` as its backdrop, ffmpeg reads it, fails to decode it,
and reports what it saw in a log the caller can read.
"""
from __future__ import annotations

import pytest

from flowboard.worker import postprod_handler as h


def test_a_background_inside_the_allowed_roots_is_used(tmp_path, monkeypatch):
    from flowboard.routes import postprod as routes_postprod

    root = tmp_path / "inputs"
    root.mkdir()
    image = root / "backdrop.png"
    image.write_bytes(b"\x89PNG")

    monkeypatch.setattr(routes_postprod, "_input_roots", lambda: [root.resolve()])
    path, note = h._background_image(str(image))
    assert path == image
    assert note is None


def test_a_background_outside_them_is_refused_with_a_reason(tmp_path, monkeypatch):
    """Falling back to the flat colour keeps the node running; saying so keeps
    the result from looking like the backdrop simply did not apply."""
    from flowboard.routes import postprod as routes_postprod

    allowed = tmp_path / "inputs"
    allowed.mkdir()
    secret = tmp_path / "elsewhere" / "id_rsa"
    secret.parent.mkdir()
    secret.write_text("PRIVATE KEY", encoding="utf-8")

    monkeypatch.setattr(routes_postprod, "_input_roots", lambda: [allowed.resolve()])
    path, note = h._background_image(str(secret))
    assert path is None
    assert note and "ngoài thư mục được phép" in note


def test_a_traversal_out_of_an_allowed_root_is_refused(tmp_path, monkeypatch):
    """`resolve()` before comparing, or `inputs/../elsewhere/x` walks out."""
    from flowboard.routes import postprod as routes_postprod

    allowed = tmp_path / "inputs"
    allowed.mkdir()
    outside = tmp_path / "elsewhere" / "x.png"
    outside.parent.mkdir()
    outside.write_bytes(b"\x89PNG")

    monkeypatch.setattr(routes_postprod, "_input_roots", lambda: [allowed.resolve()])
    path, _ = h._background_image(str(allowed / ".." / "elsewhere" / "x.png"))
    assert path is None


def test_a_media_id_is_still_the_normal_path(tmp_path, monkeypatch):
    """Cached media is already inside storage, so it does not go through the
    path check at all — and must not start failing it."""
    cached = tmp_path / "m-1.png"
    cached.write_bytes(b"\x89PNG")
    monkeypatch.setattr(
        h.media_service, "cached_path", lambda text: cached if text == "m-1" else None
    )
    path, note = h._background_image("m-1")
    assert path == cached and note is None


def test_a_missing_background_still_reads_as_missing(monkeypatch):
    """The commonest case by far: the shipped workflows name the original
    author's own machine. That must stay "not found", not "not allowed"."""
    monkeypatch.setattr(h.media_service, "cached_path", lambda text: None)
    path, note = h._background_image("D:/TOOL/anh nv/background.png")
    assert path is None
    assert note and "Không tìm thấy" in note


# ── open-output-folder opens folders ──────────────────────────────────


def test_a_storage_path_that_names_a_file_is_refused(client, tmp_path, monkeypatch):
    """On Windows `os.startfile` means "open with the default handler", and for
    a `.bat` or an `.exe` that is run it. `VIDEO_OUTPUT_DIR` arrives through the
    settings API and through imported configs, so it is not worth trusting to
    be a directory."""
    from flowboard.services import media as media_service

    a_file = tmp_path / "not-a-folder.bat"
    a_file.write_text("echo hi", encoding="utf-8")
    monkeypatch.setattr(media_service, "output_dir", lambda: a_file)

    resp = client.post("/api/media/open-output-folder")
    assert resp.status_code == 400
    assert "không phải thư mục" in resp.json()["detail"]


def test_a_real_folder_still_opens(client, tmp_path, monkeypatch):
    import flowboard.routes.media as media_routes
    from flowboard.services import media as media_service

    folder = tmp_path / "renders"
    folder.mkdir()
    monkeypatch.setattr(media_service, "output_dir", lambda: folder)
    opened: list[str] = []
    monkeypatch.setattr(
        media_routes.sys if hasattr(media_routes, "sys") else __import__("sys"),
        "platform", "linux", raising=False,
    )
    monkeypatch.setattr(
        __import__("subprocess"), "Popen", lambda args, **kw: opened.append(args[1])
    )

    resp = client.post("/api/media/open-output-folder")
    assert resp.status_code == 200, resp.text
    assert resp.json()["path"] == str(folder)
