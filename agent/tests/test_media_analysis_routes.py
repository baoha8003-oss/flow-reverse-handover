"""Routes added for the Idea / Analyze / Subtitle-Logo screens.

The heavy parts (ffmpeg filters, a live vision call) are covered elsewhere or
cost real quota. What only the HTTP layer can get wrong is covered here:
bounds on values that reach a filtergraph, path confinement, and refusing to
start work that cannot possibly succeed.
"""
from __future__ import annotations

import pytest

from flowboard.routes import postprod as routes


@pytest.fixture
def renders_dir(tmp_path, monkeypatch):
    out = tmp_path / "renders"
    out.mkdir()
    monkeypatch.setattr(routes, "OUTPUT_DIR", out)
    return out


@pytest.fixture
def a_video(tmp_path, monkeypatch):
    """A file that passes containment. Never actually decoded by these tests."""
    monkeypatch.setenv("FLOWBOARD_INPUT_ROOTS", str(tmp_path))
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00\x00\x00 ftypisom")
    return str(p)


# ── delogo ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "patch",
    [
        {"width": 0},      # a zero-width box is not a rectangle
        {"height": 0},
        {"x": -1},
        {"width": 99999},  # past any real frame
        {"y": 99999},
    ],
)
def test_delogo_rejects_impossible_geometry(client, renders_dir, a_video, patch):
    """These four numbers go into an ffmpeg filtergraph. Bounds are checked
    before ffmpeg is spawned so a bad box fails in milliseconds, not after a
    full decode."""
    body = {
        "video": a_video,
        "output": "out.mp4",
        "x": 10, "y": 10, "width": 100, "height": 50,
        **patch,
    }
    assert client.post("/api/postprod/delogo", json=body).status_code == 422


def test_delogo_confines_its_output(client, renders_dir, a_video):
    body = {
        "video": a_video, "output": "../escaped.mp4",
        "x": 1, "y": 1, "width": 10, "height": 10,
    }
    assert client.post("/api/postprod/delogo", json=body).status_code == 400
    assert not (renders_dir.parent / "escaped.mp4").exists()


def test_delogo_refuses_a_source_outside_the_allowed_roots(
    client, renders_dir, tmp_path, monkeypatch
):
    """Otherwise every render endpoint is an arbitrary-file-read primitive."""
    monkeypatch.setenv("FLOWBOARD_INPUT_ROOTS", str(tmp_path / "allowed"))
    (tmp_path / "allowed").mkdir()
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"x")
    r = client.post(
        "/api/postprod/delogo",
        json={
            "video": str(outside), "output": "out.mp4",
            "x": 1, "y": 1, "width": 10, "height": 10,
        },
    )
    assert r.status_code == 400
    assert "outside the allowed folders" in r.json()["detail"]


# ── transcribe ───────────────────────────────────────────────────────────

def test_transcribe_says_it_needs_a_key_before_doing_any_work(
    client, renders_dir, a_video, monkeypatch
):
    """conftest disables the packaged key list, so this is the no-key state.
    Failing here — before ffmpeg extracts a single frame of audio — is the
    difference between an instant, actionable message and a long wait."""
    r = client.post(
        "/api/postprod/transcribe", json={"video": a_video, "name": "out"}
    )
    assert r.status_code == 400
    assert "Gemini API key" in r.json()["detail"]


def test_transcribe_rejects_an_overlong_language_label(
    client, renders_dir, a_video
):
    r = client.post(
        "/api/postprod/transcribe",
        json={"video": a_video, "name": "out", "language": "x" * 200},
    )
    assert r.status_code == 422


# ── analyze video ────────────────────────────────────────────────────────

@pytest.mark.parametrize("frames", [1, 0, 13, 500])
def test_analyze_video_bounds_the_frame_count(client, a_video, frames):
    """Every frame is another image in the request — this is the cost dial,
    so it is bounded on both ends."""
    r = client.post("/api/vision/video", json={"video": a_video, "frames": frames})
    assert r.status_code == 422


def test_analyze_video_confines_its_source(client, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWBOARD_INPUT_ROOTS", str(tmp_path / "allowed"))
    (tmp_path / "allowed").mkdir()
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"x")
    r = client.post("/api/vision/video", json={"video": str(outside), "frames": 4})
    assert r.status_code == 400


# ── the four bundled analysis modes ──────────────────────────────────────

SCENE_JSON = """{
  "scenes": [
    {"scene_number": 1, "image_prompt": "hai que đứng", "veo_prompt": "que vẫy tay",
     "dialogue": "Chào bạn"},
    {"scene_number": 2, "image_prompt": "que chạy", "veo_prompt": "que chạy sang phải",
     "dialogue": ""}
  ],
  "vietnamese_script": "[CẢNH 1] ..."
}"""


@pytest.fixture
def a_real_clip(tmp_path, monkeypatch):
    """A clip ffmpeg can actually sample frames from."""
    import subprocess

    from flowboard.services import assets, postprod

    if not postprod.available():
        pytest.skip("ffmpeg not available")
    monkeypatch.setenv("FLOWBOARD_INPUT_ROOTS", str(tmp_path))
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        [assets.ffmpeg_bin(), "-hide_banner", "-nostdin", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(clip)],
        capture_output=True, check=True,
    )
    return str(clip)


@pytest.fixture
def canned_model(monkeypatch):
    """Stub the vision call and record the system prompt it was given."""
    import flowboard.services.llm as llm_module

    seen: dict = {}

    def _install(reply: str) -> dict:
        async def _run(kind, prompt, *, system_prompt="", **kw):
            seen["system"] = system_prompt
            return reply

        monkeypatch.setattr(llm_module, "run_llm", _run)
        return seen

    return _install


def test_a_mode_label_selects_its_system_prompt(client, a_real_clip, canned_model):
    """The workflows carry the label with its emoji, so resolution has to
    survive the decoration — `nguoi_que_new` writes "🧍 Video Người Que"."""
    seen = canned_model(SCENE_JSON)
    r = client.post(
        "/api/vision/video",
        json={"video": a_real_clip, "frames": 2, "mode": "🧍 Video Người Que"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "stick_figure"
    assert "stick figure" in seen["system"].lower()


def test_the_scene_breakdown_comes_back_as_scenes(client, a_real_clip, canned_model):
    """The bundled prompts answer with scenes, not a paragraph. Reading only
    `description` and `prompt` would drop the entire useful answer."""
    canned_model(SCENE_JSON)
    r = client.post(
        "/api/vision/video",
        json={"video": a_real_clip, "frames": 2, "mode": "🧍 Video Người Que"},
    )
    body = r.json()
    assert [s["scene_number"] for s in body["scenes"]] == [1, 2]
    assert body["scenes"][0]["dialogue"] == "Chào bạn"
    assert body["script"].startswith("[CẢNH 1]")
    # These prompts carry no `description`, and an empty panel beside a full
    # answer reads as a failure.
    assert body["description"]
    assert body["prompt"] == "que vẫy tay"


def test_no_mode_keeps_the_plain_two_field_answer(client, a_real_clip, canned_model):
    canned_model('{"description": "một clip", "prompt": "a clip"}')
    r = client.post("/api/vision/video", json={"video": a_real_clip, "frames": 2})
    body = r.json()
    assert body["mode"] == "standard"
    assert body["scenes"] == []
    assert body["description"] == "một clip"


def test_output_that_is_not_json_is_handed_back_rather_than_lost(
    client, a_real_clip, canned_model
):
    """A model that ignored the format instruction still produced something
    worth reading."""
    canned_model("Clip này quay một con mèo.")
    r = client.post("/api/vision/video", json={"video": a_real_clip, "frames": 2})
    assert r.status_code == 200
    assert "con mèo" in r.json()["description"]


def test_a_scene_with_no_prompt_at_all_is_skipped(client, a_real_clip, canned_model):
    canned_model('{"scenes": [{"scene_number": 1}, {"image_prompt": "có ảnh"}]}')
    r = client.post(
        "/api/vision/video",
        json={"video": a_real_clip, "frames": 2, "mode": "TEXT"},
    )
    scenes = r.json()["scenes"]
    assert len(scenes) == 1 and scenes[0]["image_prompt"] == "có ảnh"


@pytest.mark.parametrize("count", [0, 41, -1])
def test_the_scene_count_is_bounded(client, a_video, count):
    """Each scene downstream becomes a paid dispatch, so the ceiling is a
    cost guard the same way `/api/prompt/idea`'s is."""
    r = client.post(
        "/api/vision/video",
        json={"video": a_video, "frames": 4, "scene_count": count},
    )
    assert r.status_code == 422


# ── idea → prompts ───────────────────────────────────────────────────────

def test_idea_requires_an_idea(client):
    assert client.post("/api/prompt/idea", json={"idea": "", "scene_count": 2}).status_code == 422


@pytest.mark.parametrize("count", [0, 21, -3])
def test_idea_bounds_the_scene_count(client, count):
    """Each scene becomes one paid video dispatch, so the ceiling is a cost
    guard, not just a sanity one."""
    r = client.post(
        "/api/prompt/idea", json={"idea": "a girl in Hanoi", "scene_count": count}
    )
    assert r.status_code == 422


def test_idea_reports_a_missing_provider_rather_than_hanging(client):
    """No provider is pinned in the test environment; the caller should be
    told to open settings instead of waiting on a dispatch that cannot run."""
    r = client.post(
        "/api/prompt/idea", json={"idea": "a girl in Hanoi", "scene_count": 2}
    )
    assert r.status_code == 502
    assert "provider" in r.json()["detail"].lower()
