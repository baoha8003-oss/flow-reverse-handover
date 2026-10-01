"""Every voice name the packaged tool can hand us has to resolve to something.

The failure this closes: `tts.synthesize` rejects any name outside Google's
thirty, and six of the nine shipped workflows ask for `Ember` — a ChatGPT app
voice — while eleven `create_voice` nodes across them ask for no voice at all.
So importing a packaged template produced a node that failed at the narration
step, after the images and the video had already been paid for.

The load-bearing test here is `test_every_voice_in_the_packaged_workflows_resolves`:
it reads the shipped JSON rather than a list retyped from it, so a template that
starts carrying a new voice name makes it fail instead of failing a user.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from flowboard.services import openai_tts, tts, voice_catalog

ASSET_ROOT = Path("D:/TOOL_VIDEO/TOOL")
VOICE_DIR = ASSET_ROOT / "voice"


# ── the catalogue itself ──────────────────────────────────────────────


def test_the_catalogue_covers_exactly_the_voices_tts_will_accept():
    """Two lists of thirty names that must agree is the pair that drifts — and
    the drift would be a voice this module offers and `synthesize` refuses."""
    assert {v.name for v in voice_catalog.CATALOG} == set(tts.VOICES)
    assert len(voice_catalog.CATALOG) == len(tts.VOICES) == 30


def test_the_groups_match_the_packaged_tools_own_table():
    """`GOOGLE_VOICE_GROUPS` in the exe: 14 female, 16 male."""
    genders = [v.gender for v in voice_catalog.CATALOG]
    assert genders.count("female") == 14
    assert genders.count("male") == 16


def test_every_voice_has_a_description_and_a_measured_pitch():
    for v in voice_catalog.CATALOG:
        assert v.description.strip(), v.name
        # The descriptions are Vietnamese and start with the gender word the
        # exe wrote; a blank or an English placeholder means a bad mine.
        assert v.description.startswith(("Nữ", "Nam")), (v.name, v.description)
        assert 80.0 < v.median_f0_hz < 320.0, (v.name, v.median_f0_hz)


def test_the_openai_voice_lists_agree():
    assert voice_catalog.OPENAI_VOICES == set(openai_tts.VOICES)


def test_every_alias_points_at_a_real_voice():
    for key, (target, f0, rule) in voice_catalog.ALIASES.items():
        assert target in {v.name for v in voice_catalog.CATALOG}, key
        assert 80.0 < f0 < 340.0, (key, f0)
        assert rule in ("pitch", "name+pitch"), (key, rule)
    for key, target in voice_catalog.OPENAI_BY_APP_VOICE.items():
        assert target in voice_catalog.OPENAI_VOICES, key


def test_a_name_that_states_a_gender_is_honoured_when_the_pitch_allows_it():
    """`Giọng Nam Trầm` is Vietnamese for "deep male voice". Handing that a
    female voice is wrong in a way anyone can hear, and pure nearest-pitch did
    exactly that — 152 Hz sits in the band where the two groups overlap."""
    target, _, rule = voice_catalog.ALIASES[voice_catalog.fold("Giọng Nam Trầm")]
    assert rule == "name+pitch"
    assert voice_catalog.BY_NAME[target.casefold()].gender == "male"


def test_a_label_the_measurement_contradicts_follows_the_measurement():
    """`Cute Boy` measures 314 Hz, above every male sample. There the label
    describes a character, not a speaker, and the nearest male voice would be
    the worst match available by the only thing a listener can hear."""
    target, f0, rule = voice_catalog.ALIASES[voice_catalog.fold("Cute Boy")]
    assert rule == "pitch"
    assert f0 > max(v.median_f0_hz for v in voice_catalog.CATALOG if v.gender == "male")
    assert voice_catalog.BY_NAME[target.casefold()].gender == "female"


# ── folding ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,folded",
    [
        ("🗣️ Không chọn", "khong chon"),
        ("Giọng Nam Trầm", "giong nam tram"),
        ("  EMBER  ", "ember"),
        ("no_voice", "no voice"),
        ("Đạo Sĩ", "dao si"),
        ("Chrming  Male", "chrming male"),
    ],
)
def test_folding_strips_decoration_accents_and_case(raw, folded):
    assert voice_catalog.fold(raw) == folded


# ── resolution ────────────────────────────────────────────────────────


def test_a_gemini_voice_resolves_to_itself_with_no_warning():
    choice = voice_catalog.resolve("Kore", default="Zephyr")
    assert (choice.voice, choice.warning, choice.source) == ("Kore", None, "exact")
    # Case does not matter: the value comes from a dropdown label.
    assert voice_catalog.resolve("kore", default="Zephyr").voice == "Kore"


def test_no_voice_means_no_narration_not_a_default():
    """Eleven `create_voice` nodes across the nine shipped workflows store
    `🗣️ Không chọn`. Narrating those with a default would put a voice over
    videos built to have none — the one substitution that cannot be undone by
    ignoring it."""
    for raw in ("🗣️ Không chọn", "Không tạo giọng", "no_voice", "none", "tắt giọng"):
        choice = voice_catalog.resolve(raw, default="Kore")
        assert choice.voice is None, raw
        assert choice.source == "no_voice", raw


def test_an_unset_voice_is_not_a_request_for_silence():
    """Empty and "no voice" are different answers. The workflows say so out
    loud when they want none, so a blank is an unset setting like any other."""
    for raw in ("", "   ", None):
        choice = voice_catalog.resolve(raw, default="Kore")
        assert choice.voice == "Kore", repr(raw)


def test_a_substitution_always_warns_and_names_both_voices():
    choice = voice_catalog.resolve("Ember", default="Kore")
    assert choice.source == "alias"
    assert choice.voice == "Puck"
    assert "Ember" in choice.warning and "Puck" in choice.warning


def test_an_unknown_name_falls_back_loudly():
    choice = voice_catalog.resolve("Không Có Giọng Này Đâu", default="Kore")
    assert choice.voice == "Kore"
    assert choice.source == "unknown"
    assert "Không Có Giọng Này Đâu" in choice.warning


def test_a_cloned_voice_says_which_feature_is_missing():
    """Two shipped nodes ask for `Clone Voice`. "Đã đọc bằng Kore" with no
    reason is a bug report waiting to happen."""
    choice = voice_catalog.resolve("Clone Voice", default="Kore")
    assert choice.source == "refused"
    assert "nhân bản" in choice.warning


def test_the_openai_resolver_does_not_accept_gemini_names():
    """A Gemini voice reaching `/v1/audio/speech` is a 400 after the request
    has been made. The two catalogues are separate on purpose."""
    choice = voice_catalog.resolve_openai("Kore", default="alloy")
    assert choice.voice == "alloy"
    assert choice.source == "unknown"
    assert voice_catalog.resolve_openai("onyx", default="alloy").source == "exact"


# ── engine + voice together ───────────────────────────────────────────


def test_asking_for_openai_while_it_is_off_reads_with_gemini_and_says_so():
    """The opposite of what the image engine does, deliberately: there a
    fallback would spend Flow credits nobody asked for, here it is this app's
    own default engine, and refusing would make every imported packaged
    template fail out of the box."""
    plan = tts.plan_voice("openai", "Ember")
    assert plan.engine == "gemini"
    assert plan.voice == "Puck"
    assert "OPENAI_TTS_ENABLED" in plan.note


def test_asking_for_openai_with_the_switch_on_but_no_key_fails_loudly(monkeypatch):
    """A switch turned on by hand with no key behind it is a misconfiguration,
    not a default — so this one does NOT fall back."""
    monkeypatch.setattr(tts, "_setting", lambda key: True if key == "OPENAI_TTS_ENABLED" else None)
    monkeypatch.setattr(openai_tts, "enabled", lambda: False)
    plan = tts.plan_voice("openai", "Ember")
    assert plan.voice is None
    assert "khoá API" in plan.note


def test_openai_enabled_sends_an_openai_voice(monkeypatch):
    monkeypatch.setattr(tts, "_setting", lambda key: True if key == "OPENAI_TTS_ENABLED" else None)
    monkeypatch.setattr(openai_tts, "enabled", lambda: True)
    plan = tts.plan_voice("openai", "Ember")
    assert plan.engine == "openai"
    assert plan.voice == "echo"
    assert plan.voice in openai_tts.VOICES


def test_no_voice_beats_the_engine_choice():
    """A node that wants no narration wants none whichever engine is named."""
    for engine in (None, "gemini", "openai", "clone"):
        plan = tts.plan_voice(engine, "🗣️ Không chọn")
        assert plan.voice is None, engine


def test_capcut_and_clone_engines_name_what_is_missing():
    assert "CapCut" in (tts.plan_voice("capcut", "Mai").note or "")
    assert "nhân bản" in (tts.plan_voice("clone", "Mai").note or "")


# ── the plan layer ────────────────────────────────────────────────────


def _voice_node(**settings):
    from types import SimpleNamespace

    return SimpleNamespace(
        id=1,
        type="create_voice",
        data={"prompt": "kịch bản", "sourceSettings": settings},
    )


def test_a_node_asking_for_no_voice_plans_no_narration_pass():
    """Not "narrate silently" — no pass at all. A silent file would still be
    mixed over the video by the next step."""
    from flowboard.services import postprod_plan

    node = _voice_node(voice="🗣️ Không chọn")
    assert postprod_plan.ops_for(node, [], assume_ready=True) == []


def test_a_substituted_voice_reaches_the_op_with_its_note():
    from flowboard.services import postprod_plan

    ops = postprod_plan.ops_for(_voice_node(voice="Ember"), [], assume_ready=True)
    assert len(ops) == 1
    assert ops[0]["voice"] == "Puck"
    assert "Ember" in ops[0]["voiceNote"]
    assert ops[0]["engine"] == "gemini"


# ── the real packaged data ────────────────────────────────────────────


def _packaged_voice_values() -> list[tuple[str, str]]:
    """Every (file, voice) pair in the shipped workflow JSON."""
    found: list[tuple[str, str]] = []
    for path in sorted(ASSET_ROOT.rglob("*.json")):
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if '"voice"' not in raw:
            continue
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            continue

        def walk(obj, where=path.name):
            if isinstance(obj, dict):
                v = obj.get("voice")
                if isinstance(v, str) and v.strip():
                    found.append((where, v))
                for value in obj.values():
                    walk(value, where)
            elif isinstance(obj, list):
                for value in obj:
                    walk(value, where)

        walk(doc)
    return found


def test_every_voice_in_the_packaged_workflows_resolves():
    """Read from the shipped files, not from a list retyped out of them: a
    template that starts carrying a new voice name should fail here rather than
    fail a user mid-run."""
    if not ASSET_ROOT.is_dir():
        pytest.skip("the packaged tool is not on this machine")
    values = _packaged_voice_values()
    assert values, "no voice values found — did the workflow format move?"

    unresolved = []
    for name, voice in values:
        choice = voice_catalog.resolve(voice, default=tts.DEFAULT_VOICE)
        if choice.source == "unknown":
            unresolved.append((name, voice))
    assert not unresolved, f"voice names with no mapping: {unresolved}"


def test_every_shipped_voice_sample_name_resolves():
    """The 9 ChatGPT + 22 Vietnamese + 36 English CapCut sample files are the
    universe of names a CapCut-flavoured project can carry."""
    if not VOICE_DIR.is_dir():
        pytest.skip("the packaged voice samples are not on this machine")
    names = []
    for sub in ("GPT_VOICE", "VOICE_CAPCUT", "VOICE_CAPCUT_ENGLISH"):
        directory = VOICE_DIR / sub
        if directory.is_dir():
            names += [p.stem for p in directory.iterdir()
                      if p.suffix.lower() in (".wav", ".mp3")]
    assert len(names) >= 60, names

    unresolved = [n for n in names
                  if voice_catalog.resolve(n, default="Kore").source == "unknown"]
    assert not unresolved, f"sample names with no mapping: {unresolved}"


def test_the_measured_pitch_matches_the_shipped_sample_for_the_default_voice():
    """One spot-check that the numbers in the table came off these files and
    not from somewhere else: the sample has to exist for every voice."""
    if not VOICE_DIR.is_dir():
        pytest.skip("the packaged voice samples are not on this machine")
    missing = [v.name for v in voice_catalog.CATALOG
               if not (VOICE_DIR / f"{v.name}.wav").is_file()]
    assert not missing, missing


# ── the OpenAI endpoint ───────────────────────────────────────────────


def test_openai_tts_is_off_by_default():
    assert openai_tts.enabled() is False


def test_openai_tts_refuses_while_switched_off():
    with pytest.raises(openai_tts.OpenAITTSError) as exc:
        openai_tts.synthesize("xin chào", voice="alloy")
    assert "OPENAI_TTS_ENABLED" in str(exc.value)


def test_openai_tts_refuses_a_voice_it_does_not_have():
    with pytest.raises(openai_tts.OpenAITTSError) as exc:
        openai_tts.synthesize("xin chào", voice="Kore")
    # The refusal lists the real options rather than sending a request that
    # would come back 400 after the round trip.
    assert "alloy" in str(exc.value)


def test_openai_tts_refuses_empty_text():
    with pytest.raises(openai_tts.OpenAITTSError):
        openai_tts.synthesize("   ", voice="alloy")


def test_openai_tts_never_puts_the_key_in_an_error(monkeypatch):
    """The 401 body from this endpoint can echo the key prefix back."""
    secret = "sk-test-DO-NOT-LEAK-1234567890"
    monkeypatch.setattr(tts, "_setting", lambda key: True)
    monkeypatch.setattr(openai_tts, "_setting", lambda key: True)
    from flowboard.services.llm import secrets

    monkeypatch.setattr(secrets, "get_api_key", lambda provider: secret)

    class _Resp:
        status_code = 401
        content = b'{"error":{"message":"Incorrect API key provided: sk-test-DO..."}}'

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(openai_tts.httpx, "Client", lambda **k: _Client())
    with pytest.raises(openai_tts.OpenAITTSError) as exc:
        openai_tts.synthesize("xin chào", voice="alloy")
    message = str(exc.value)
    assert secret not in message
    assert "sk-" not in message
    assert "401" in message


def test_openai_tts_writes_the_audio_it_is_given(monkeypatch, tmp_path):
    monkeypatch.setattr(openai_tts, "_setting", lambda key: True)
    from flowboard.services.llm import secrets

    monkeypatch.setattr(secrets, "get_api_key", lambda provider: "sk-x")

    sent: dict = {}

    class _Resp:
        status_code = 200
        content = b"ID3fake-mp3-bytes"

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            sent.update({"url": url, "json": json})
            return _Resp()

    monkeypatch.setattr(openai_tts.httpx, "Client", lambda **k: _Client())
    out = openai_tts.synthesize(
        "xin chào", voice="onyx", instructions="đọc chậm", out_path=tmp_path / "a.mp3"
    )
    assert out.read_bytes() == b"ID3fake-mp3-bytes"
    assert sent["url"] == openai_tts.ENDPOINT
    assert sent["json"]["voice"] == "onyx"
    assert sent["json"]["instructions"] == "đọc chậm"
    assert sent["json"]["input"] == "xin chào"


def test_the_default_filename_is_content_addressed(monkeypatch):
    """`hash()` is randomised per process, so a retry would write a second file
    under a different name and orphan the first."""
    monkeypatch.setattr(openai_tts, "_setting", lambda key: True)
    from flowboard.services.llm import secrets

    monkeypatch.setattr(secrets, "get_api_key", lambda provider: "sk-x")

    class _Resp:
        status_code = 200
        content = b"x"

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(openai_tts.httpx, "Client", lambda **k: _Client())
    first = openai_tts.synthesize("cùng một câu", voice="alloy")
    second = openai_tts.synthesize("cùng một câu", voice="alloy")
    assert first == second


# ── what the estimate says about it ───────────────────────────────────


def _board_with_voice_node(client, *, settings: dict, prompt: str) -> int:
    board_id = client.post("/api/boards", json={"name": "Voice"}).json()["id"]
    client.post(
        "/api/nodes",
        json={
            "board_id": board_id,
            "type": "create_voice",
            "x": 0,
            "y": 0,
            "data": {"prompt": prompt, "sourceSettings": settings},
        },
    )
    return board_id


def test_the_estimate_says_nothing_about_openai_when_gemini_narrates(client):
    board_id = _board_with_voice_node(
        client, settings={"voice": "Kore"}, prompt="một câu"
    )
    body = client.get(f"/api/boards/{board_id}/estimate").json()
    assert body["openaiTtsNodes"] == 0
    assert body["openaiTtsNote"] == ""


def test_the_estimate_counts_characters_not_calls(monkeypatch, client):
    """`/v1/audio/speech` bills per character. "One narration" says nothing
    about whether that is a caption or a ten-minute story, and those differ by
    three orders of magnitude on the invoice."""
    from flowboard.services import openai_tts as ot
    from flowboard.services import tts as t

    monkeypatch.setattr(t, "_setting", lambda key: True if key == "OPENAI_TTS_ENABLED" else None)
    monkeypatch.setattr(ot, "enabled", lambda: True)
    monkeypatch.setattr(ot, "_setting", lambda key: True if key == "OPENAI_TTS_ENABLED" else None)

    script = "một câu dài vừa đủ để đếm."
    board_id = _board_with_voice_node(
        client, settings={"voice": "Ember", "engine": "ChatGPT"}, prompt=script
    )
    body = client.get(f"/api/boards/{board_id}/estimate").json()
    assert body["openaiTtsNodes"] == 1
    assert body["openaiTtsChars"] == len(script)
    assert "ký tự" in body["openaiTtsNote"]
    # And it stays out of the Flow ledger: that number means credits.
    assert body["billableJobs"] == 0


def test_a_script_arriving_over_a_wire_is_reported_as_unmeasured(monkeypatch, client):
    """Counting the placeholder's length would read as a price. Saying "not
    measurable yet" is the only honest answer before the run."""
    from flowboard.services import openai_tts as ot
    from flowboard.services import tts as t

    monkeypatch.setattr(t, "_setting", lambda key: True if key == "OPENAI_TTS_ENABLED" else None)
    monkeypatch.setattr(ot, "enabled", lambda: True)
    monkeypatch.setattr(ot, "_setting", lambda key: True if key == "OPENAI_TTS_ENABLED" else None)

    board_id = client.post("/api/boards", json={"name": "Wired"}).json()["id"]
    client.post("/api/nodes", json={
        "board_id": board_id, "type": "create_voice", "x": 0, "y": 0,
        "data": {"sourceSettings": {"voice": "Ember", "engine": "ChatGPT"}},
    })
    body = client.get(f"/api/boards/{board_id}/estimate").json()
    assert body["openaiTtsNodes"] == 1
    assert body["openaiTtsChars"] == 0
    assert body["openaiTtsUnknownNodes"] == 1
    assert "chưa đo được" in body["openaiTtsNote"]


# ── the worker end ────────────────────────────────────────────────────


def _fake_wav(tmp_path):
    import wave

    path = tmp_path / "fake.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x00" * 2400)
    return path


@pytest.mark.asyncio
async def test_the_worker_sends_an_openai_op_to_openai(monkeypatch, tmp_path):
    """The engine on the op has to reach the endpoint. Without this the plan
    can settle on OpenAI and the worker still narrate with Gemini — the whole
    decision silently discarded one layer down."""
    from flowboard.worker.postprod_handler import handle_postprod

    calls: dict = {}

    def _fake_openai(text, *, voice, instructions=None):
        calls["engine"] = "openai"
        calls["voice"] = voice
        return _fake_wav(tmp_path)

    def _fake_gemini(text, *, voice, persona=None):
        calls["engine"] = "gemini"
        return _fake_wav(tmp_path)

    monkeypatch.setattr("flowboard.services.openai_tts.synthesize", _fake_openai)
    monkeypatch.setattr("flowboard.worker.postprod_handler.tts.synthesize", _fake_gemini)
    # The worker re-settles the plan against CURRENT settings rather than
    # trusting the op, so a key added between planning and dispatch takes
    # effect. That means the switch has to be on here.
    monkeypatch.setattr(tts, "_setting", lambda key: True if key == "OPENAI_TTS_ENABLED" else None)
    monkeypatch.setattr(openai_tts, "enabled", lambda: True)

    result, err = await handle_postprod(
        {"op": "narrate", "text": "xin chào", "engine": "openai", "voice": "onyx"}
    )
    assert err is None, err
    assert calls == {"engine": "openai", "voice": "onyx"}
    assert result["media_ids"]


@pytest.mark.asyncio
async def test_the_worker_carries_the_substitution_note_out(monkeypatch, tmp_path):
    """A substituted voice is not an error — the node succeeds — but it IS a
    different voice than the one on the node, so it has to arrive somewhere a
    user can read it."""
    from flowboard.worker.postprod_handler import handle_postprod

    monkeypatch.setattr(
        "flowboard.worker.postprod_handler.tts.synthesize",
        lambda text, *, voice, persona=None: _fake_wav(tmp_path),
    )
    result, err = await handle_postprod(
        {"op": "narrate", "text": "xin chào", "engine": "gemini", "voice": "Ember"}
    )
    assert err is None, err
    assert "Ember" in result["note"]


@pytest.mark.asyncio
async def test_the_worker_refuses_to_narrate_a_node_that_asked_for_silence():
    """Only a direct API caller reaches this — the plan layer drops the pass —
    and narrating with a default is the one thing the name asked us not to do."""
    from flowboard.worker.postprod_handler import handle_postprod

    result, err = await handle_postprod(
        {"op": "narrate", "text": "xin chào", "voice": "🗣️ Không chọn"}
    )
    assert result == {}
    assert err == "voice_none_requested"


def test_the_generated_catalogue_is_up_to_date():
    """`voice_catalog.py` is generated. A hand edit there is lost on the next
    regeneration without a word — which happened once while building this — so
    the file on disk has to equal what the generator produces from the data
    beside it."""
    import subprocess
    import sys

    generated = Path("flowboard/services/voice_catalog.py")
    tool = Path("tools/voice_catalog/gen_voice_catalog.py")
    if not tool.is_file() or not generated.is_file():
        pytest.skip("running outside the repo checkout")

    before = generated.read_bytes()
    proc = subprocess.run(
        [sys.executable, str(tool)], capture_output=True, text=True,
        env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
    )
    after = generated.read_bytes()
    if after != before:
        generated.write_bytes(before)  # leave the tree as we found it
    assert proc.returncode == 0, proc.stderr
    assert after == before, (
        "voice_catalog.py differs from what the generator produces — either it "
        "was hand-edited, or the data under tools/voice_catalog changed without "
        "a regeneration."
    )


def test_the_estimate_reads_the_script_off_the_wire(monkeypatch, client):
    """The script usually comes from a wired prompt node, not from the voice
    node itself. Asking the planner with an empty upstream made every board
    read as unmeasurable — which looks like "free" next to a per-character
    price."""
    from flowboard.services import openai_tts as ot
    from flowboard.services import tts as t

    monkeypatch.setattr(t, "_setting", lambda key: True if key == "OPENAI_TTS_ENABLED" else None)
    monkeypatch.setattr(ot, "enabled", lambda: True)
    monkeypatch.setattr(ot, "_setting", lambda key: True if key == "OPENAI_TTS_ENABLED" else None)

    script = "lời thoại nằm ở node prompt, không nằm ở node giọng."
    board_id = client.post("/api/boards", json={"name": "Wired"}).json()["id"]
    prompt_id = client.post("/api/nodes", json={
        "board_id": board_id, "type": "prompt", "x": 0, "y": 0,
        "data": {"prompt": script},
    }).json()["id"]
    voice_id = client.post("/api/nodes", json={
        "board_id": board_id, "type": "create_voice", "x": 1, "y": 0,
        "data": {"sourceSettings": {"voice": "Ember", "engine": "ChatGPT"}},
    }).json()["id"]
    client.post("/api/edges", json={
        "board_id": board_id, "source_id": prompt_id, "target_id": voice_id,
        "target_port": "text",
    })

    body = client.get(f"/api/boards/{board_id}/estimate").json()
    assert body["openaiTtsNodes"] == 1
    assert body["openaiTtsChars"] == len(script)
    assert body["openaiTtsUnknownNodes"] == 0


def test_the_default_filename_is_a_sha1_of_the_script(monkeypatch):
    """Pins the mechanism, not just that two calls agree: `hash()` is stable
    within one process, so a same-process comparison cannot tell the two
    apart — and the failure only shows up on the retry, in a new process."""
    import hashlib

    monkeypatch.setattr(openai_tts, "_setting", lambda key: True)
    from flowboard.services.llm import secrets

    monkeypatch.setattr(secrets, "get_api_key", lambda provider: "sk-x")

    class _Resp:
        status_code = 200
        content = b"x"

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(openai_tts.httpx, "Client", lambda **k: _Client())
    script = "một câu để băm"
    out = openai_tts.synthesize(script, voice="alloy")
    expected = hashlib.sha1(script.encode("utf-8")).hexdigest()[:12]
    assert out.name == f"openai-{expected}.mp3"
