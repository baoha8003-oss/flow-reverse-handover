"""Post-production endpoints take file paths from the request body.

Outputs were already confined to storage/renders. Inputs were not: any
path on disk was accepted, so `POST /api/postprod/logo` with
`video=C:/Users/me/Documents/tax.pdf` would hand that file to ffmpeg and
mux it into a render the caller can then download. These tests pin the
input side shut.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from flowboard.config import STORAGE_DIR
from flowboard.routes import postprod as routes
from flowboard.services import postprod


def test_input_outside_allowed_roots_is_refused(client, tmp_path, monkeypatch):
    """A real, readable file still gets refused when it lives nowhere the
    app is allowed to read from."""
    monkeypatch.setattr(routes, "OUTPUT_DIR", tmp_path / "renders")
    called: dict = {}

    def fake_burn(*a, **k):
        called["ran"] = True
        raise AssertionError("service must not be reached")

    monkeypatch.setattr(postprod, "burn_subtitles", fake_burn)

    outsider = tmp_path / "somewhere_else" / "private.mp4"
    outsider.parent.mkdir(parents=True, exist_ok=True)
    outsider.write_bytes(b"x")
    srt = tmp_path / "somewhere_else" / "sub.srt"
    srt.write_text("1\n", encoding="utf-8")

    r = client.post(
        "/api/postprod/subtitles",
        json={"video": str(outsider), "srt": str(srt), "output": "out.mp4"},
    )
    assert r.status_code == 400
    assert "outside the allowed folders" in r.json()["detail"]
    assert called == {}


def test_traversal_out_of_an_allowed_root_is_refused(
    client, tmp_path, monkeypatch
):
    """`..` segments must be collapsed BEFORE the containment check.

    Comparing the raw string would let `<allowed>/../secret.mp4` pass a
    prefix test while resolving somewhere else entirely.
    """
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("FLOWBOARD_INPUT_ROOTS", str(allowed))
    monkeypatch.setattr(routes, "OUTPUT_DIR", tmp_path / "renders")

    secret = tmp_path / "secret.mp4"
    secret.write_bytes(b"x")
    logo = allowed / "logo.png"
    logo.write_bytes(b"x")

    traversal = str(allowed / ".." / "secret.mp4")
    r = client.post(
        "/api/postprod/logo",
        json={"video": traversal, "logo": str(logo), "output": "out.mp4"},
    )
    assert r.status_code == 400
    assert "outside the allowed folders" in r.json()["detail"]


def test_storage_dir_is_always_an_allowed_root(client, monkeypatch, tmp_path):
    """Media the app itself produced lives under STORAGE_DIR, so it must
    stay readable without any extra configuration."""
    monkeypatch.delenv("FLOWBOARD_INPUT_ROOTS", raising=False)
    monkeypatch.setattr(routes, "OUTPUT_DIR", tmp_path / "renders")

    src = STORAGE_DIR / "containment_probe.mp4"
    src.write_bytes(b"x")
    captured: dict = {}

    def fake_trim(video, audio, dst):
        captured["video"] = Path(video)
        return Path(dst)

    monkeypatch.setattr(postprod, "trim_to_audio", fake_trim)
    monkeypatch.setattr(postprod, "probe_duration", lambda p: 1.0)

    try:
        r = client.post(
            "/api/postprod/fit-narration",
            json={
                "video": str(src),
                "audio": str(src),
                "output": "out.mp4",
            },
        )
        assert r.status_code == 200, r.text
        assert captured["video"] == src.resolve()
    finally:
        src.unlink(missing_ok=True)


def test_case_differing_path_is_accepted_on_windows(client, monkeypatch, tmp_path):
    """A drive-letter / case difference must not falsely reject a real file.

    On Windows the filesystem is case-insensitive, so an allowed root of
    `D:\\allowed` and an input of `d:\\allowed\\clip.mp4` name the same
    place; the containment check compares through normcase so it holds.
    On POSIX normcase is a no-op and this simply exercises the exact-case
    path.
    """
    import os

    allowed = tmp_path / "Allowed"
    allowed.mkdir()
    monkeypatch.setenv("FLOWBOARD_INPUT_ROOTS", str(allowed))
    monkeypatch.setattr(routes, "OUTPUT_DIR", tmp_path / "renders")

    clip = allowed / "clip.mp4"
    clip.write_bytes(b"x")
    captured: dict = {}

    def fake_trim(video, audio, dst):
        captured["video"] = str(video)
        return Path(dst)

    monkeypatch.setattr(postprod, "trim_to_audio", fake_trim)
    monkeypatch.setattr(postprod, "probe_duration", lambda p: 1.0)

    swapped = os.path.normcase(str(clip))
    swapped = swapped.swapcase() if os.name == "nt" else str(clip)
    r = client.post(
        "/api/postprod/fit-narration",
        json={"video": swapped, "audio": swapped, "output": "out.mp4"},
    )
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_input_path_is_rejected(client, blank, monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "OUTPUT_DIR", tmp_path / "renders")
    r = client.post(
        "/api/postprod/subtitles",
        json={"video": blank, "srt": blank, "output": "out.mp4"},
    )
    assert r.status_code == 400
    assert "required" in r.json()["detail"]


# ── library lookups must not become a bypass ─────────────────────────────
# `/bgm` resolves its track through the bundled music library FIRST, and a
# library hit skips _existing() entirely. `BGM_DIR / name` looked safe but
# pathlib DROPS the base when the operand is absolute, and `..` is only
# collapsed by resolve() — so both forms returned a live path and walked
# straight past the containment check.


def test_bgm_library_lookup_refuses_an_absolute_path_outside_the_library(
    client, tmp_path, monkeypatch
):
    from flowboard.services import assets

    monkeypatch.setattr(routes, "OUTPUT_DIR", tmp_path / "renders")
    monkeypatch.setattr(assets, "BGM_DIR", tmp_path / "nhac_nen")
    (tmp_path / "nhac_nen").mkdir()

    outsider = tmp_path / "elsewhere" / "private.mp3"
    outsider.parent.mkdir(parents=True, exist_ok=True)
    outsider.write_bytes(b"x")

    video = STORAGE_DIR / "clip.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"x")

    def fake_mix(*a, **k):
        raise AssertionError("service must not be reached")

    monkeypatch.setattr(postprod, "mix_bgm", fake_mix)

    r = client.post(
        "/api/postprod/bgm",
        json={
            "video": str(video),
            "track": str(outsider),
            "output": "out.mp4",
        },
    )
    assert r.status_code == 400
    assert "outside the allowed folders" in r.json()["detail"]


def test_bgm_library_lookup_refuses_traversal(tmp_path, monkeypatch):
    from flowboard.services import assets

    library = tmp_path / "nhac_nen"
    library.mkdir()
    monkeypatch.setattr(assets, "BGM_DIR", library)
    (tmp_path / "secret.mp3").write_bytes(b"x")

    assert assets.bgm_path("../secret.mp3") is None


def test_bgm_library_lookup_still_finds_real_tracks(tmp_path, monkeypatch):
    """The fix must not break the feature it protects."""
    from flowboard.services import assets

    library = tmp_path / "nhac_nen"
    library.mkdir()
    track = library / "calm.mp3"
    track.write_bytes(b"x")
    monkeypatch.setattr(assets, "BGM_DIR", library)

    assert assets.bgm_path("calm.mp3") == track.resolve()
    assert assets.bgm_path("calm") == track.resolve()  # stem match


def test_font_lookup_is_confined_to_the_font_directory(tmp_path, monkeypatch):
    from flowboard.services import assets

    fonts = tmp_path / "CapCut_Fonts"
    fonts.mkdir()
    real = fonts / "BeVietnamPro-Bold.ttf"
    real.write_bytes(b"x")
    outsider = tmp_path / "elsewhere.ttf"
    outsider.write_bytes(b"x")
    monkeypatch.setattr(assets, "FONT_DIR", fonts)

    assert assets.font_path("BeVietnamPro-Bold.ttf") == real.resolve()
    assert assets.font_path(str(outsider)) is None
    assert assets.font_path("../elsewhere.ttf") is None
