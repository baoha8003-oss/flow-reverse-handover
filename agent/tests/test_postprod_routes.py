"""Route-level tests for local post-production.

The heavy ffmpeg / RealESRGAN behavior is covered by test_postprod.py and
test_upscale.py against the real binaries. These tests cover what only the
HTTP layer can get wrong: path confinement, missing-capability handling, and
that a request actually reaches the service with the arguments it was given.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from flowboard.routes import postprod as routes
from flowboard.services import postprod, tts, upscale


@pytest.fixture(autouse=True)
def allow_tmp_inputs(monkeypatch, tmp_path):
    """Input paths are confined to the app's storage and asset roots.

    These tests feed fixture files from pytest's tmp_path, which is
    neither, so declare it an allowed root for the duration. The
    confinement itself is covered by test_postprod_input_containment.py.
    """
    monkeypatch.setenv("FLOWBOARD_INPUT_ROOTS", str(tmp_path))


def test_status_reports_capabilities(client):
    r = client.get("/api/postprod/status")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) >= {
        "assets",
        "ffmpegAvailable",
        "upscaleAvailable",
        "upscaleModels",
        "ttsKeyAvailable",
        "voices",
        "fonts",
        "bgm",
    }
    assert isinstance(body["voices"], list)
    assert isinstance(body["ffmpegAvailable"], bool)


def test_output_name_cannot_escape_storage(client, monkeypatch, tmp_path):
    """A crafted output name must not let a request write outside the
    render directory."""
    monkeypatch.setattr(routes, "OUTPUT_DIR", tmp_path / "renders")

    called: dict = {}

    def fake_trim(video, audio, dst):
        called["dst"] = dst
        return Path(dst)

    monkeypatch.setattr(postprod, "trim_to_audio", fake_trim)

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")

    r = client.post(
        "/api/postprod/fit-narration",
        json={
            "video": str(video),
            "audio": str(audio),
            "output": "../../escaped.mp4",
        },
    )
    assert r.status_code == 400
    assert "escapes storage" in r.json()["detail"]
    assert "dst" not in called


def test_missing_input_file_is_rejected_before_running_ffmpeg(client, tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "OUTPUT_DIR", tmp_path / "renders")
    called: dict = {}

    def fake_burn(*a, **k):
        called["ran"] = True
        raise AssertionError("should not reach the service")

    monkeypatch.setattr(postprod, "burn_subtitles", fake_burn)

    r = client.post(
        "/api/postprod/subtitles",
        json={
            "video": str(tmp_path / "nope.mp4"),
            "srt": str(tmp_path / "nope.srt"),
            "output": "out.mp4",
        },
    )
    assert r.status_code == 400
    assert "not found" in r.json()["detail"]
    assert called == {}


def test_narrate_requires_api_key(client, monkeypatch):
    monkeypatch.setattr(tts, "api_key_available", lambda: False)
    r = client.post("/api/postprod/narrate", json={"text": "xin chào"})
    assert r.status_code == 400
    assert "GEMINI_API_KEY" in r.json()["detail"]


def test_narrate_passes_voice_and_persona_through(client, monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "OUTPUT_DIR", tmp_path / "renders")
    monkeypatch.setattr(tts, "api_key_available", lambda: True)
    monkeypatch.setattr(postprod, "available", lambda: False)
    captured: dict = {}

    def fake_synth(text, *, voice, persona, out_path):
        captured.update(text=text, voice=voice, persona=persona)
        target = out_path or (tmp_path / "narration.wav")
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_bytes(b"RIFF")
        return Path(target)

    monkeypatch.setattr(tts, "synthesize", fake_synth)

    r = client.post(
        "/api/postprod/narrate",
        json={
            "text": "một hai ba",
            "voice": "Zephyr",
            "persona": "Giọng Nam Miền Bắc Trầm Triết Lý",
            "output": "scene1.wav",
        },
    )
    assert r.status_code == 200, r.text
    assert captured["voice"] == "Zephyr"
    assert captured["persona"] == "Giọng Nam Miền Bắc Trầm Triết Lý"
    assert captured["text"] == "một hai ba"
    assert r.json()["voice"] == "Zephyr"


def test_upscale_rejected_when_engine_missing(client, monkeypatch, tmp_path):
    monkeypatch.setattr(upscale, "available", lambda: False)
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    r = client.post(
        "/api/postprod/upscale",
        json={"source": str(src), "output": "out.mp4"},
    )
    assert r.status_code == 400
    assert "RealESRGAN" in r.json()["detail"]


def test_concat_normalizes_every_clip_before_joining(client, monkeypatch, tmp_path):
    """Clips from separate Flow calls differ in size/fps; concat is
    stream-copy only, so each clip must go through normalize first."""
    monkeypatch.setattr(routes, "OUTPUT_DIR", tmp_path / "renders")
    normalized: list[Path] = []

    def fake_normalize(src, dst, width, height, fps):
        Path(dst).parent.mkdir(parents=True, exist_ok=True)
        Path(dst).write_bytes(b"norm")
        normalized.append(Path(src))
        return Path(dst)

    def fake_concat(clips, dst):
        assert len(clips) == 2
        Path(dst).write_bytes(b"joined")
        return Path(dst)

    monkeypatch.setattr(postprod, "normalize", fake_normalize)
    monkeypatch.setattr(postprod, "concat", fake_concat)
    monkeypatch.setattr(postprod, "probe_duration", lambda p: 4.0)

    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a.write_bytes(b"a")
    b.write_bytes(b"b")

    r = client.post(
        "/api/postprod/concat",
        json={"clips": [str(a), str(b)], "output": "final.mp4"},
    )
    assert r.status_code == 200, r.text
    assert normalized == [a, b]
    assert r.json()["durationSeconds"] == 4.0
    # Intermediates must not be left behind next to the render.
    leftovers = list((tmp_path / "renders").glob(".final.norm*.mp4"))
    assert leftovers == []


def test_service_error_becomes_500_with_message(client, monkeypatch, tmp_path):
    monkeypatch.setattr(routes, "OUTPUT_DIR", tmp_path / "renders")

    def boom(*a, **k):
        raise postprod.PostProdError("ffmpeg exploded: bad codec")

    monkeypatch.setattr(postprod, "overlay_logo", boom)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    logo = tmp_path / "l.png"
    logo.write_bytes(b"x")

    r = client.post(
        "/api/postprod/logo",
        json={"video": str(video), "logo": str(logo), "output": "out.mp4"},
    )
    assert r.status_code == 500
    assert "ffmpeg exploded" in r.json()["detail"]
