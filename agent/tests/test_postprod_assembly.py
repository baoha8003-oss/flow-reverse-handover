"""Title, thumbnail and cut — the final-assembly steps.

These complete what the packaged tool calls `edit_video`: mining it showed the
node is an ffmpeg assembly step (encoder args, an atempo filter, a thumbnail
command), not a Flow operation. Concat/subtitles/logo/bgm/narration already
existed; these were the missing pieces.

Real ffmpeg runs live in test_postprod.py. What is covered here is the layer
that turns caller input into a filtergraph — where a stray character silently
truncates a caption or breaks the whole graph.
"""
from __future__ import annotations

import pytest

from flowboard.services import postprod
from flowboard.routes import postprod as routes


@pytest.fixture
def renders_dir(tmp_path, monkeypatch):
    out = tmp_path / "renders"
    out.mkdir()
    monkeypatch.setattr(routes, "OUTPUT_DIR", out)
    return out


@pytest.fixture
def a_video(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWBOARD_INPUT_ROOTS", str(tmp_path))
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00\x00\x00 ftypisom")
    return str(p)


# ── drawtext escaping ────────────────────────────────────────────────────

def test_colon_is_escaped():
    """drawtext reads `:` as its own option separator, so an unescaped colon
    in a title truncates the caption at that point."""
    assert postprod._drawtext_escape("Mùa thu: 2026") == "Mùa thu\\: 2026"


def test_apostrophe_is_escaped():
    """The option value is single-quoted; a bare apostrophe closes it early
    and breaks the whole filtergraph."""
    assert "\\'" in postprod._drawtext_escape("hôm nay's")


def test_backslash_is_escaped_first():
    """Escaping it after the others would double-escape what they added."""
    assert postprod._drawtext_escape("a\\b") == "a\\\\b"


def test_newlines_become_spaces():
    """A literal newline in the option string ends the filter early."""
    assert "\n" not in postprod._drawtext_escape("dòng 1\ndòng 2")


def test_plain_vietnamese_text_is_untouched():
    """Diacritics are ordinary characters here — escaping them would draw
    backslashes on the video."""
    assert postprod._drawtext_escape("Áo dài Hội An") == "Áo dài Hội An"


# ── title route ──────────────────────────────────────────────────────────

def test_title_requires_text(client, renders_dir, a_video):
    body = {"video": a_video, "output": "o.mp4", "text": ""}
    assert client.post("/api/postprod/title", json=body).status_code == 422


@pytest.mark.parametrize(
    "patch",
    [{"size": 0}, {"size": 900}, {"boxOpacity": 1.5}, {"boxOpacity": -0.1}, {"yRatio": 2}],
)
def test_title_bounds_its_numbers(client, renders_dir, a_video, patch):
    body = {"video": a_video, "output": "o.mp4", "text": "hi", **patch}
    assert client.post("/api/postprod/title", json=body).status_code == 422


def test_title_confines_its_output(client, renders_dir, a_video):
    body = {"video": a_video, "output": "../escaped.mp4", "text": "hi"}
    assert client.post("/api/postprod/title", json=body).status_code == 400
    assert not (renders_dir.parent / "escaped.mp4").exists()


def test_title_rejects_an_unknown_font(client, renders_dir, a_video):
    """Naming a font that isn't in the library must fail with something the
    user can act on, not an ffmpeg filtergraph error."""
    body = {
        "video": a_video,
        "output": "o.mp4",
        "text": "hi",
        "font": "definitely-not-a-real-font.ttf",
    }
    resp = client.post("/api/postprod/title", json=body)
    assert resp.status_code in (400, 500)
    assert "font" in resp.json()["detail"].lower()


# ── thumbnail route ──────────────────────────────────────────────────────

def test_thumbnail_forces_an_image_extension(client, renders_dir, a_video, monkeypatch):
    """`output: "cover"` must not produce a file ffmpeg will refuse to write."""
    seen: dict = {}

    def fake(src, dst, *, at_seconds, width):
        seen["dst"] = dst
        dst.write_bytes(b"\xff\xd8jpeg")
        return dst

    monkeypatch.setattr(postprod, "grab_thumbnail", fake)
    resp = client.post(
        "/api/postprod/thumbnail", json={"video": a_video, "output": "cover"}
    )
    assert resp.status_code == 200, resp.text
    assert str(seen["dst"]).endswith(".jpg")
    assert resp.json()["url"].endswith("cover.jpg")


def test_thumbnail_keeps_an_explicit_png(client, renders_dir, a_video, monkeypatch):
    def fake(src, dst, *, at_seconds, width):
        dst.write_bytes(b"\x89PNG")
        return dst

    monkeypatch.setattr(postprod, "grab_thumbnail", fake)
    resp = client.post(
        "/api/postprod/thumbnail", json={"video": a_video, "output": "cover.png"}
    )
    assert resp.json()["url"].endswith("cover.png")


@pytest.mark.parametrize("patch", [{"width": 4}, {"width": 99999}, {"atSeconds": -1}])
def test_thumbnail_bounds_its_numbers(client, renders_dir, a_video, patch):
    body = {"video": a_video, "output": "c.jpg", **patch}
    assert client.post("/api/postprod/thumbnail", json=body).status_code == 422


# ── cut route ────────────────────────────────────────────────────────────

# ── last frame (real ffmpeg) ─────────────────────────────────────────────

requires_ffmpeg = pytest.mark.skipif(
    not postprod.available(), reason="ffmpeg not in the asset library"
)


@requires_ffmpeg
def test_last_frame_really_is_the_last_one(tmp_path):
    """The whole point is chaining scenes: the last frame of clip N becomes
    the first frame of clip N+1. Returning the FIRST frame instead would look
    plausible in a thumbnail and produce a stuttering join.

    Proven with colour rather than a hash: a 3-second clip of red, then
    green, then blue. The last frame must be blue.
    """
    import subprocess

    from flowboard.services import assets

    ffmpeg = assets.ffmpeg_bin()
    clip = tmp_path / "rgb.mp4"
    subprocess.run(
        [
            ffmpeg, "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=320x240:d=1",
            "-f", "lavfi", "-i", "color=c=green:s=320x240:d=1",
            "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=1",
            "-filter_complex", "[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]",
            "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(clip),
        ],
        check=True,
    )

    def average_rgb(image) -> tuple[int, int, int]:
        out = subprocess.run(
            [
                ffmpeg, "-v", "error", "-i", str(image),
                "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ],
            capture_output=True,
        ).stdout
        return tuple(out[:3])

    red, green, blue = average_rgb(
        postprod.last_frame(clip, tmp_path / "last.png")
    )
    assert blue > 150, f"last frame is not blue: {(red, green, blue)}"
    assert red < 80, f"last frame looks like the FIRST frame: {(red, green, blue)}"


@requires_ffmpeg
def test_last_frame_honours_a_width(tmp_path):
    import subprocess

    from flowboard.services import assets

    clip = tmp_path / "c.mp4"
    subprocess.run(
        [
            assets.ffmpeg_bin(), "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=640x480:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
        ],
        check=True,
    )
    out = postprod.last_frame(clip, tmp_path / "f.png", width=160)
    dims = subprocess.run(
        [
            assets.ffprobe_bin(), "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width", "-of", "csv=p=0", str(out),
        ],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert dims.startswith("160")


def test_last_frame_forces_an_image_extension(client, renders_dir, a_video, monkeypatch):
    def fake(src, dst, *, width):
        dst.write_bytes(b"\xff\xd8jpeg")
        return dst

    monkeypatch.setattr(postprod, "last_frame", fake)
    resp = client.post(
        "/api/postprod/last-frame", json={"video": a_video, "output": "frame"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["url"].endswith("frame.jpg")
    # No project_id given → nothing was pushed to Flow.
    assert resp.json()["mediaId"] is None


def test_last_frame_rejects_a_bad_project_id(client, renders_dir, a_video, monkeypatch):
    """A malformed id would otherwise reach the Flow upload and fail there,
    after the frame has already been extracted."""
    def fake(src, dst, *, width):
        dst.write_bytes(b"\xff\xd8jpeg")
        return dst

    monkeypatch.setattr(postprod, "last_frame", fake)
    resp = client.post(
        "/api/postprod/last-frame",
        json={"video": a_video, "output": "f.jpg", "project_id": "not a real id!"},
    )
    assert resp.status_code == 400


# ── cut route ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("seconds", [0, -1, 999999])
def test_cut_bounds_the_segment_length(client, renders_dir, a_video, seconds):
    body = {"video": a_video, "seconds": seconds}
    assert client.post("/api/postprod/cut", json=body).status_code == 422


def test_cut_confines_the_piece_name(client, renders_dir, a_video):
    """The stem becomes a filename; `../x` must not place pieces elsewhere."""
    resp = client.post(
        "/api/postprod/cut",
        json={"video": a_video, "seconds": 5, "name": "../escaped"},
    )
    # Either refused outright, or the stem was reduced to its basename —
    # never written outside the renders folder.
    assert not list(renders_dir.parent.glob("escaped-*.mp4"))
    assert resp.status_code in (400, 500)
