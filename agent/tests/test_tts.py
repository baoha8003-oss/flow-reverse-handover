"""Tests for the Gemini TTS narration service.

No test here touches the network: every case that would call the API
monkeypatches ``httpx.Client.post`` and asserts on the captured request, so
the request shape (voice, response modality, key placement) is verified
without a key ever leaving the process.
"""
from __future__ import annotations

import base64
import json
import struct
import wave

import httpx
import pytest

from flowboard.services import assets
from flowboard.services import tts


FAKE_KEY = "AIzaSyFAKE-key-for-tests-0123456789"

# One 24 kHz mono frame per sample value — real signed 16-bit LE PCM.
PCM = struct.pack("<8h", 0, 1000, -1000, 32767, -32768, 250, -250, 0)


class _StubResponse:
    """Enough of ``httpx.Response`` for the paths the service exercises."""

    def __init__(self, status_code: int = 200, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text else json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _audio_payload(pcm: bytes = PCM, mime: str = "audio/L16;codec=pcm;rate=24000") -> dict:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "inlineData": {
                                "mimeType": mime,
                                "data": base64.b64encode(pcm).decode("ascii"),
                            }
                        }
                    ]
                }
            }
        ]
    }


@pytest.fixture
def captured(monkeypatch):
    """Stub ``httpx.Client.post``; records the call, returns audio."""
    calls: list[dict] = []

    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        calls.append({"url": url, "headers": headers or {}, "body": json})
        return _StubResponse(200, _audio_payload())

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    return calls


@pytest.fixture(autouse=True)
def _isolate_key_env(monkeypatch):
    """Never let a real ambient key influence resolution-order assertions."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("FLOWBOARD_TTS_MODEL", raising=False)


@pytest.fixture
def empty_data_dir(monkeypatch, tmp_path):
    """Point the service at a data dir with no key file and no personas."""
    monkeypatch.setattr(assets, "DATA_DIR", tmp_path)
    tts.clear_persona_cache()
    yield tmp_path
    tts.clear_persona_cache()


# ── voices ────────────────────────────────────────────────────────────────


def test_voices_has_thirty_unique_names():
    assert len(tts.VOICES) == 30
    assert len(set(tts.VOICES)) == 30


def test_list_voices_reports_sample_availability(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "VOICE_SAMPLE_DIR", tmp_path)
    (tmp_path / "Kore.wav").write_bytes(b"RIFF")

    voices = tts.list_voices()
    assert [v["name"] for v in voices] == list(tts.VOICES)

    by_name = {v["name"]: v for v in voices}
    assert by_name["Kore"]["hasSample"] is True
    assert by_name["Kore"]["sample"] == str(tmp_path / "Kore.wav")
    assert by_name["Puck"]["hasSample"] is False
    assert by_name["Puck"]["sample"] is None


@pytest.mark.skipif(
    not assets.VOICE_SAMPLE_DIR.is_dir(),
    reason="asset library not installed on this machine",
)
def test_canonical_voices_match_installed_samples():
    """The local .wav previews are named after the voices — keep them in sync."""
    assert set(tts.VOICES) == set(assets.list_voice_samples())


def test_synthesize_rejects_unknown_voice():
    with pytest.raises(tts.TTSError, match="unknown Gemini TTS voice"):
        tts.synthesize("xin chào", voice="Bartholomew", api_key=FAKE_KEY)


# ── personas ──────────────────────────────────────────────────────────────


def test_load_personas_reads_asset_file(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "DATA_DIR", tmp_path)
    tts.clear_persona_cache()
    (tmp_path / "voice_styles.json").write_text(
        json.dumps(
            [
                {"title": "Giọng Nam Miền Bắc", "description": "Trầm ấm, chậm rãi."},
                {"title": "Giọng Nữ Miền Nam", "description": "Nhẹ nhàng, tươi sáng."},
                {"title": "no description here"},
                "not an object",
            ]
        ),
        encoding="utf-8",
    )

    personas = tts.load_personas()
    assert [p["title"] for p in personas] == ["Giọng Nam Miền Bắc", "Giọng Nữ Miền Nam"]
    assert personas[0]["description"] == "Trầm ấm, chậm rãi."
    tts.clear_persona_cache()


def test_load_personas_tolerates_missing_file(empty_data_dir):
    assert tts.load_personas() == []
    assert tts.find_persona("anything") is None


def test_load_personas_tolerates_corrupt_file(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "DATA_DIR", tmp_path)
    tts.clear_persona_cache()
    (tmp_path / "voice_styles.json").write_text("{not json", encoding="utf-8")
    assert tts.load_personas() == []
    tts.clear_persona_cache()


def test_load_personas_result_is_not_shared_with_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "DATA_DIR", tmp_path)
    tts.clear_persona_cache()
    (tmp_path / "voice_styles.json").write_text(
        json.dumps([{"title": "A", "description": "d"}]), encoding="utf-8"
    )
    first = tts.load_personas()
    first[0]["description"] = "mutated"
    assert tts.load_personas()[0]["description"] == "d"
    tts.clear_persona_cache()


def test_find_persona_is_case_insensitive(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "DATA_DIR", tmp_path)
    tts.clear_persona_cache()
    (tmp_path / "voice_styles.json").write_text(
        json.dumps([{"title": "Ông Lão Kể Chuyện", "description": "Khàn nhẹ."}]),
        encoding="utf-8",
    )
    found = tts.find_persona("  ông lão kể chuyện ")
    assert found is not None and found["description"] == "Khàn nhẹ."
    assert tts.find_persona("Bà Lão") is None
    tts.clear_persona_cache()


def test_persona_description_prepended_to_script(captured, tmp_path, empty_data_dir):
    persona = {"title": "Ông Lão", "description": "Giọng nam lớn tuổi, chậm rãi."}
    tts.synthesize(
        "Hôm nay trời đẹp.",
        persona=persona,
        api_key=FAKE_KEY,
        out_path=tmp_path / "out.wav",
    )

    sent = captured[0]["body"]["contents"][0]["parts"][0]["text"]
    assert sent == "Read in this voice: Giọng nam lớn tuổi, chậm rãi.\n\nHôm nay trời đẹp."
    assert sent.index("Giọng nam lớn tuổi") < sent.index("Hôm nay trời đẹp")


def test_persona_accepted_by_title(captured, monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "DATA_DIR", tmp_path)
    tts.clear_persona_cache()
    (tmp_path / "voice_styles.json").write_text(
        json.dumps([{"title": "Kể Chuyện", "description": "Ấm áp, gần gũi."}]),
        encoding="utf-8",
    )

    tts.synthesize("Nội dung.", persona="Kể Chuyện", api_key=FAKE_KEY,
                   out_path=tmp_path / "out.wav")
    sent = captured[0]["body"]["contents"][0]["parts"][0]["text"]
    assert sent.startswith("Read in this voice: Ấm áp, gần gũi.")
    tts.clear_persona_cache()


def test_unmatched_persona_becomes_free_text_delivery_notes(empty_data_dir):
    """A persona string that names no library entry IS the delivery note —
    the route documents "persona title, or free-text delivery notes".
    Raising turned a valid request into a 500 that read as a server fault."""
    assert tts.style_text("Nội dung.", "chậm rãi, ấm áp") == (
        "Read in this voice: chậm rãi, ấm áp\n\nNội dung."
    )


def test_no_persona_sends_script_verbatim(captured, tmp_path, empty_data_dir):
    tts.synthesize("Chỉ nội dung.", api_key=FAKE_KEY, out_path=tmp_path / "out.wav")
    assert captured[0]["body"]["contents"][0]["parts"][0]["text"] == "Chỉ nội dung."


def test_blank_persona_title_means_no_persona(empty_data_dir):
    """An unselected dropdown sends "" — that is no persona, not a bad one."""
    assert tts.style_text("Nội dung.", "") == "Nội dung."
    assert tts.style_text("Nội dung.", None) == "Nội dung."


# ── request shape ─────────────────────────────────────────────────────────


def test_request_shape_and_key_placement(captured, tmp_path, empty_data_dir):
    tts.synthesize(
        "Xin chào.", voice="Zephyr", api_key=FAKE_KEY, out_path=tmp_path / "out.wav"
    )

    call = captured[0]
    assert call["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash-preview-tts:generateContent"
    )
    # The key belongs in the header — a URL lands in logs and proxies.
    assert call["headers"]["x-goog-api-key"] == FAKE_KEY
    assert FAKE_KEY not in call["url"]

    config = call["body"]["generationConfig"]
    assert config["responseModalities"] == ["AUDIO"]
    prebuilt = config["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]
    assert prebuilt["voiceName"] == "Zephyr"


def test_model_override_via_env(captured, monkeypatch, tmp_path, empty_data_dir):
    monkeypatch.setenv("FLOWBOARD_TTS_MODEL", "gemini-2.5-pro-preview-tts")
    tts.synthesize("Xin chào.", api_key=FAKE_KEY, out_path=tmp_path / "out.wav")
    assert captured[0]["url"].endswith("gemini-2.5-pro-preview-tts:generateContent")


def test_model_argument_wins_over_env(captured, monkeypatch, tmp_path, empty_data_dir):
    monkeypatch.setenv("FLOWBOARD_TTS_MODEL", "from-env")
    tts.synthesize(
        "Xin chào.", model="explicit-model", api_key=FAKE_KEY, out_path=tmp_path / "out.wav"
    )
    assert captured[0]["url"].endswith("explicit-model:generateContent")


def test_empty_text_never_calls_the_api(captured, empty_data_dir):
    with pytest.raises(tts.TTSError, match="text is empty"):
        tts.synthesize("   ", api_key=FAKE_KEY)
    assert captured == []


# ── pcm → wav ─────────────────────────────────────────────────────────────


def test_synthesize_writes_playable_wav(captured, tmp_path, empty_data_dir):
    out = tmp_path / "narration.wav"
    result = tts.synthesize("Xin chào.", api_key=FAKE_KEY, out_path=out)

    assert result == out
    assert out.read_bytes()[:4] == b"RIFF"
    assert out.read_bytes()[8:12] == b"WAVE"

    with wave.open(str(out), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 24000
        assert wav.getnframes() == len(PCM) // 2
        assert wav.readframes(wav.getnframes()) == PCM


def test_rate_is_taken_from_the_reported_mime_type(monkeypatch, tmp_path, empty_data_dir):
    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        return _StubResponse(200, _audio_payload(mime="audio/L16;codec=pcm;rate=16000"))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    out = tts.synthesize("Xin chào.", api_key=FAKE_KEY, out_path=tmp_path / "out.wav")

    with wave.open(str(out), "rb") as wav:
        assert wav.getframerate() == 16000


def test_missing_rate_falls_back_to_24k(monkeypatch, tmp_path, empty_data_dir):
    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        return _StubResponse(200, _audio_payload(mime="audio/L16"))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    out = tts.synthesize("Xin chào.", api_key=FAKE_KEY, out_path=tmp_path / "out.wav")

    with wave.open(str(out), "rb") as wav:
        assert wav.getframerate() == 24000


def test_write_wav_rejects_misaligned_pcm(tmp_path):
    with pytest.raises(tts.TTSError, match="not a whole number"):
        tts.write_wav(b"\x01\x02\x03", tmp_path / "odd.wav", rate=24000)


def test_write_wav_creates_missing_parent_dirs(tmp_path):
    target = tmp_path / "deep" / "nested" / "out.wav"
    tts.write_wav(PCM, target, rate=24000)
    assert target.is_file()


def test_response_without_audio_part_raises(monkeypatch, tmp_path, empty_data_dir):
    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        return _StubResponse(
            200,
            {"candidates": [{"content": {"parts": [{"text": "sorry"}]},
                             "finishReason": "SAFETY"}]},
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    with pytest.raises(tts.TTSError, match="no audio part"):
        tts.synthesize("Xin chào.", api_key=FAKE_KEY, out_path=tmp_path / "out.wav")


def test_blocked_prompt_surfaces_block_reason(monkeypatch, tmp_path, empty_data_dir):
    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        return _StubResponse(200, {"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}})

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    with pytest.raises(tts.TTSError, match="PROHIBITED_CONTENT"):
        tts.synthesize("Xin chào.", api_key=FAKE_KEY, out_path=tmp_path / "out.wav")


# ── api key resolution ────────────────────────────────────────────────────


def test_key_resolution_prefers_explicit_argument(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "DATA_DIR", tmp_path)
    (tmp_path / "gemini_api_key.txt").write_text("from-file", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "from-gemini-env")
    monkeypatch.setenv("GOOGLE_API_KEY", "from-google-env")

    assert tts.resolve_api_key("  explicit  ") == "explicit"


def test_key_resolution_prefers_gemini_env_over_google_env(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "DATA_DIR", tmp_path)
    (tmp_path / "gemini_api_key.txt").write_text("from-file", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "from-gemini-env")
    monkeypatch.setenv("GOOGLE_API_KEY", "from-google-env")

    assert tts.resolve_api_key() == "from-gemini-env"


def test_key_resolution_falls_back_to_google_env(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "DATA_DIR", tmp_path)
    (tmp_path / "gemini_api_key.txt").write_text("from-file", encoding="utf-8")
    monkeypatch.setenv("GOOGLE_API_KEY", "from-google-env")

    assert tts.resolve_api_key() == "from-google-env"


def test_key_resolution_falls_back_to_asset_file(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "DATA_DIR", tmp_path)
    (tmp_path / "gemini_api_key.txt").write_text("\n\n  from-file  \n", encoding="utf-8")

    assert tts.resolve_api_key() == "from-file"
    assert tts.api_key_available() is True


def test_key_resolution_returns_none_when_nothing_configured(empty_data_dir):
    assert tts.resolve_api_key() is None
    assert tts.api_key_available() is False


def test_blank_explicit_key_falls_through_to_env(monkeypatch, tmp_path):
    monkeypatch.setattr(assets, "DATA_DIR", tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "from-gemini-env")
    assert tts.resolve_api_key("   ") == "from-gemini-env"


def test_synthesize_without_key_raises_before_any_request(captured, empty_data_dir):
    with pytest.raises(tts.TTSError, match="no Gemini API key"):
        tts.synthesize("Xin chào.")
    assert captured == []


# ── the key never leaks ───────────────────────────────────────────────────


def test_http_error_message_carries_api_message_but_not_the_key(
    monkeypatch, tmp_path, empty_data_dir
):
    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        return _StubResponse(
            400,
            {"error": {"message": f"API key not valid: {FAKE_KEY}", "code": 400}},
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    with pytest.raises(tts.TTSError) as excinfo:
        tts.synthesize("Xin chào.", api_key=FAKE_KEY, out_path=tmp_path / "out.wav")

    message = str(excinfo.value)
    assert "API key not valid" in message
    assert "HTTP 400" in message
    assert FAKE_KEY not in message
    assert "***" in message


def test_transport_error_message_excludes_the_key(monkeypatch, tmp_path, empty_data_dir):
    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        raise httpx.ConnectError(f"connection refused while sending {FAKE_KEY}")

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    with pytest.raises(tts.TTSError) as excinfo:
        tts.synthesize("Xin chào.", api_key=FAKE_KEY, out_path=tmp_path / "out.wav")

    assert FAKE_KEY not in str(excinfo.value)
    assert "connection refused" in str(excinfo.value)


def test_missing_key_error_names_the_file_not_the_key(empty_data_dir):
    with pytest.raises(tts.TTSError) as excinfo:
        tts.synthesize("Xin chào.")
    assert "gemini_api_key.txt" in str(excinfo.value)


def test_success_log_never_contains_the_key(captured, caplog, tmp_path, empty_data_dir):
    with caplog.at_level("DEBUG"):
        tts.synthesize("Xin chào.", api_key=FAKE_KEY, out_path=tmp_path / "out.wav")
    # Assert the line was actually emitted first — otherwise "key not in an
    # empty log" would pass while proving nothing.
    assert "tts:" in caplog.text and "out.wav" in caplog.text
    assert FAKE_KEY not in caplog.text


# ── files the user edits by hand on Windows ───────────────────────────────


def test_key_file_saved_with_a_bom_still_yields_a_clean_key(monkeypatch, tmp_path):
    """Notepad writes UTF-8 with a BOM; \\ufeff survives .strip()."""
    monkeypatch.setattr(assets, "DATA_DIR", tmp_path)
    (tmp_path / "gemini_api_key.txt").write_text(FAKE_KEY, encoding="utf-8-sig")

    key = tts.resolve_api_key()
    assert key == FAKE_KEY
    assert not key.startswith("﻿")


def test_persona_file_saved_with_a_bom_still_parses(monkeypatch, tmp_path):
    """A BOM used to make json.loads fail, silently emptying the persona list."""
    monkeypatch.setattr(assets, "DATA_DIR", tmp_path)
    tts.clear_persona_cache()
    (tmp_path / "voice_styles.json").write_text(
        json.dumps([{"title": "Kể Chuyện", "description": "Ấm áp."}]),
        encoding="utf-8-sig",
    )

    personas = tts.load_personas()
    assert [p["title"] for p in personas] == ["Kể Chuyện"]
    tts.clear_persona_cache()


def test_unsendable_key_raises_ttserror_without_leaking_it(captured, monkeypatch, tmp_path):
    """A UTF-16 key file used to reach httpx, which echoed the raw bytes back."""
    monkeypatch.setattr(assets, "DATA_DIR", tmp_path)
    (tmp_path / "gemini_api_key.txt").write_text(FAKE_KEY, encoding="utf-16-le")

    with pytest.raises(tts.TTSError) as excinfo:
        tts.synthesize("Xin chào.", out_path=tmp_path / "out.wav")

    message = str(excinfo.value)
    assert "cannot be sent in a header" in message
    # Neither the mangled form nor any recognisable slice of the key.
    assert "AIzaSy" not in message
    assert "\x00" not in message
    assert captured == []


def test_key_with_a_non_ascii_character_is_refused_before_the_request(captured, empty_data_dir):
    with pytest.raises(tts.TTSError, match="cannot be sent in a header"):
        tts.synthesize("Xin chào.", api_key="AIzaSy padded-key", out_path=None)
    assert captured == []


# ── audio format is honoured, not assumed ─────────────────────────────────


def test_stereo_mime_type_is_framed_as_two_channels(monkeypatch, tmp_path, empty_data_dir):
    stereo = struct.pack("<8h", 0, 1000, -1000, 500, 32767, -32768, 250, -250)

    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        return _StubResponse(
            200, _audio_payload(stereo, mime="audio/L16;codec=pcm;rate=48000;channels=2")
        )

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    out = tts.synthesize("Xin chào.", api_key=FAKE_KEY, out_path=tmp_path / "out.wav")

    with wave.open(str(out), "rb") as wav:
        assert wav.getnchannels() == 2
        assert wav.getframerate() == 48000
        # 8 samples over 2 channels is 4 frames, not 8 — a wrong channel count
        # here would halve or double the clip's duration.
        assert wav.getnframes() == 4
        assert wav.readframes(4) == stereo


def test_unsupported_bit_depth_is_refused_not_written_as_noise(
    monkeypatch, tmp_path, empty_data_dir
):
    """24-bit PCM framed at 16-bit would be noise at 1.5x the duration."""
    def fake_post(self, url, *, headers=None, json=None, **kwargs):
        return _StubResponse(200, _audio_payload(mime="audio/L24;codec=pcm;rate=48000"))

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    out = tmp_path / "out.wav"
    with pytest.raises(tts.TTSError, match="unsupported audio format"):
        tts.synthesize("Xin chào.", api_key=FAKE_KEY, out_path=out)
    assert not out.exists()


def test_output_path_with_spaces_and_vietnamese_name(captured, tmp_path, empty_data_dir):
    """Narration lands beside asset files whose names look like this."""
    out = tmp_path / "Giọng Nữ" / "Bản thu 1.2 - lời dẫn.wav"
    result = tts.synthesize("Xin chào.", api_key=FAKE_KEY, out_path=out)

    assert result == out and out.is_file()
    with wave.open(str(out), "rb") as wav:
        assert wav.readframes(wav.getnframes()) == PCM
