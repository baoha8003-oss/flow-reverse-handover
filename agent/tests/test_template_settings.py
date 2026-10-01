"""The template settings this build used to store and then ignore.

Every value below was read off the eleven workflow files on disk, not
invented: `scale` really is spelled `🔍 x1`, `thumbnail_position` really is
the string `end`, and the cover image really does arrive on a PORT
(`gen_image.image_out_1 -> edit_video.image_thumbnail`) rather than in a
settings path — which is where I would have guessed wrong.

The failure this closes is quiet by nature. An imported node kept the value
in `sourceSettings`, the board looked configured, and the run ignored it. So
the tests below mostly assert that a setting *reaches the op*, because that
is the step that was missing.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flowboard.services import node_settings as ns
from flowboard.services import postprod_plan as pp


def node(node_type="edit_video", **settings):
    return SimpleNamespace(type=node_type, data={"sourceSettings": settings})


def wire(node_obj, port):
    return pp.Upstream(node_obj, port)


def media(node_type="video", mid="m-1"):
    return SimpleNamespace(type=node_type, data={"mediaId": mid})


def ops(node_obj, upstream):
    return pp.ops_for(node_obj, upstream, assume_ready=True)


def op_named(node_obj, upstream, name):
    return [o for o in ops(node_obj, upstream) if o["op"] == name]


# ── scale: the multiplier the tool writes with an emoji ───────────────


@pytest.mark.parametrize("raw,expected", [
    ("🔍 x1", None),      # every shipped node says this
    ("🔍 x2", 2.0),
    ("x4", 4.0),
    (2, 2.0),
    (1, None),
    ("", None),
    ("hai lần", None),
    (16, None),           # out of range — a typo, not a request
])
def test_the_upscale_multiplier_is_read_through_its_decoration(raw, expected):
    assert ns.upscale_factor({"scale": raw}) == expected


def test_x1_adds_no_pass_at_all():
    """Twenty-six shipped nodes say `🔍 x1`. Treating that as "upscale by 1"
    would add a re-encode, and a quality loss, to every one of them."""
    assert op_named(node(scale="🔍 x1"), [wire(media(), "media")], "upscale") == []


def test_x2_asks_for_the_pass_without_a_separate_switch():
    """The packaged tool has no enable flag for this — asking for x2 IS
    asking for the pass."""
    found = op_named(node(scale="🔍 x2"), [wire(media(), "media")], "upscale")
    assert found and found[0]["targetScale"] == 2.0


def test_the_multiplier_is_a_resample_target_not_a_model_scale():
    """It used to emit `scale: 2`, which `upscale._validate` refuses: both
    bundled models upscale by exactly 4x and anything else "would return
    silently corrupted pixels". So the whole edit_video failed, and the
    multiplier path every packaged workflow writes could never run.

    The model keeps its native rate; the multiplier becomes the resample
    target, which is the one resample path the engine was designed around."""
    found = op_named(node(scale="x2"), [wire(media(), "media")], "upscale")
    assert found[0]["targetScale"] == 2.0
    assert "scale" not in found[0], "a model scale of 2 is rejected by the engine"


def test_an_explicit_target_height_wins_over_the_multiplier():
    found = op_named(
        node(scale="x2", upscale_resolution=2160), [wire(media(), "media")], "upscale"
    )
    assert found[0]["targetHeight"] == 2160
    assert "targetScale" not in found[0]


# ── the cover frame ───────────────────────────────────────────────────


def test_the_cover_image_comes_from_a_port_not_a_path():
    """Measured from `nguoi_que_new.json`. A settings path was the obvious
    guess and it is not what the file does."""
    found = op_named(
        node(enable_thumbnail=True, thumbnail_position="end", thumbnail_duration=0.2),
        [wire(media(), "media"), wire(media("image", "m-cover"), "image_thumbnail")],
        "thumbnail_insert",
    )
    assert found
    assert found[0]["image"] == "m-cover"
    assert found[0]["position"] == "end"
    assert found[0]["seconds"] == 0.2


def test_the_flag_without_an_image_adds_nothing():
    """A template that turns the cover on and never wires one is not an
    error — it is a board someone has not finished."""
    assert op_named(
        node(enable_thumbnail=True), [wire(media(), "media")], "thumbnail_insert"
    ) == []


def test_an_image_without_the_flag_is_not_appended():
    assert op_named(
        node(), [wire(media(), "media"), wire(media("image", "m-cover"), "image_thumbnail")],
        "thumbnail_insert",
    ) == []


def test_an_unnamed_wire_is_the_clip_not_a_cover():
    """`image_thumbnail` is strict. An unnamed wire into `edit_video` is the
    video being edited, and reading it as a cover would append the clip to
    itself."""
    assert op_named(
        node(enable_thumbnail=True), [wire(media(), None)], "thumbnail_insert"
    ) == []


def test_the_cover_goes_on_after_the_captions(monkeypatch):
    """Burned earlier it would carry a subtitle meant for the clip — on the
    one frame people screenshot."""
    from flowboard.services import stt

    # A speech-to-text source has to exist or the subtitle passes are
    # skipped and there is no ordering to check.
    monkeypatch.setattr(stt, "sources", lambda: ["local"])
    chain = ops(
        node(enable_thumbnail=True, enable_sub=True),
        [wire(media(), "media"), wire(media("image", "m-cover"), "image_thumbnail")],
    )
    names = [o["op"] for o in chain]
    assert "thumbnail_insert" in names and "subtitles" in names
    assert names.index("thumbnail_insert") > names.index("subtitles")


# ── narration speed ───────────────────────────────────────────────────


def test_a_fixed_voice_speed_retimes_the_voice_not_the_clip():
    """The narration is an audio file. `change_speed` applies a `setpts`
    video filter and would fail on a stream that is not there."""
    chain = ops(
        node(enable_voice=True, voice_speed=1.25),
        [wire(media(), "media"), wire(media("create_voice", "m-voice"), "voice")],
    )
    speed = [o for o in chain if o["op"] == "voice_speed"]
    assert speed and speed[0]["audio"] == "m-voice" and speed[0]["speed"] == 1.25


def test_speed_one_adds_no_pass():
    """Every shipped node says 1.0. A re-encode that changes nothing is
    still a re-encode."""
    chain = ops(
        node(enable_voice=True, voice_speed=1.0),
        [wire(media(), "media"), wire(media("create_voice", "m-voice"), "voice")],
    )
    assert not [o for o in chain if o["op"] == "voice_speed"]


def test_auto_speed_is_a_different_pass_not_a_number():
    """The ratio is not knowable until both durations are measured, so
    "fit it" cannot be expressed as a tempo."""
    chain = ops(
        node(enable_voice=True, auto_voice_speed=True, voice_speed=1.5),
        [wire(media(), "media"), wire(media("create_voice", "m-voice"), "voice")],
    )
    names = [o["op"] for o in chain]
    assert "fit_narration" in names
    assert "voice_speed" not in names, "asking for both would undo the first"


# ── background music offset ───────────────────────────────────────────


def test_the_music_offset_reaches_the_mix():
    """A track whose first twenty seconds are an intro is why this exists."""
    chain = ops(
        node(enable_bgm=True, bgm_sample_file="1. Nhạc.MP3", start_time=20),
        [wire(media(), "media")],
    )
    bgm = [o for o in chain if o["op"] == "bgm"]
    assert bgm and bgm[0]["trackStart"] == 20


def test_offset_zero_is_left_out_rather_than_sent():
    chain = ops(
        node(enable_bgm=True, bgm_sample_file="1. Nhạc.MP3", start_time=0),
        [wire(media(), "media")],
    )
    bgm = [o for o in chain if o["op"] == "bgm"]
    assert bgm and "trackStart" not in bgm[0]


def test_the_offset_is_music_only_not_narration():
    """`start_time` is where to start reading the TRACK. Applying it to a
    voice-over would cut the first words off the narration."""
    chain = ops(
        node(enable_voice=True, start_time=20),
        [wire(media(), "media"), wire(media("create_voice", "m-voice"), "voice")],
    )
    voice_mix = [o for o in chain if o["op"] == "bgm"]
    assert voice_mix and "trackStart" not in voice_mix[0]


# ── the remaining keys, parsed rather than dropped ────────────────────


@pytest.mark.parametrize("raw,expected", [
    ("ChatGPT", "openai"),
    ("Clone Voice", "clone"),
    ("Gemini", "gemini"),
    ("", None),
    ("something else", None),
])
def test_the_speech_engine_name_is_normalised(raw, expected):
    """Returned as an intent, not a decision about availability: "the
    template asked for ChatGPT" and "ChatGPT is configured" are different
    facts, and collapsing them hides which one is missing."""
    assert ns.tts_engine({"engine": raw}) == expected


def test_both_gemini_models_survive_their_decoration():
    """`⚡ API — gemini-3.5-flash` is how the tool writes it. The fallback
    is what turns a 503 into a retry instead of a failed node."""
    assert ns.gemini_models({
        "gemini_model": "⚡ API — gemini-3.5-flash",
        "fallback_gemini_model": "⚡ API — gemini-3.6-flash",
    }) == ("gemini-3.5-flash", "gemini-3.6-flash")


def test_a_missing_fallback_is_none_not_a_copy():
    assert ns.gemini_models({"gemini_model": "⚡ API — gemini-3.5-flash"})[1] is None


@pytest.mark.parametrize("key", ["path", "image_path", "import_excel_path"])
def test_every_local_path_key_is_read(key):
    """Three names for the same thing across three node types."""
    assert ns.local_path({key: "G:/x/y.xlsx"}) == "G:/x/y.xlsx"


def test_a_local_path_is_returned_not_opened():
    """A template is untrusted input. This module has no business touching
    the filesystem — the caller decides what it may read."""
    assert ns.local_path({"path": "/etc/passwd"}) == "/etc/passwd"


def test_a_blank_path_is_absent():
    """Three of the shipped `upload_media` nodes carry `path: ''`."""
    assert ns.local_path({"path": "   "}) is None


def test_the_prompt_template_switches_keep_random_as_a_word():
    """`selected_scene_id` ships as `random`. Coercing it to an int would
    turn the tool's own default into a crash."""
    assert ns.prompt_template_flags({
        "fashion_random_enabled": True,
        "op_lung_enabled": False,
        "selected_scene_id": "random",
    }) == {
        "fashion_random_enabled": True,
        "op_lung_enabled": False,
        "selected_scene_id": "random",
    }


def test_layout_only_keys_are_read_but_deliberately_not_acted_on():
    """`batch_idx` and `preview_height` describe the packaged tool's own
    layout. This canvas has its own coordinates, so acting on them would
    move nodes the user placed — they are kept so a round-trip does not
    flatten someone's arrangement."""
    assert ns.batch_index({"batch_idx": 3}) == 3
    assert ns.preview_height({"preview_height": 420}) == 420
    assert ns.batch_index({"batch_idx": 0}) is None
    assert ns.preview_height({"preview_height": 99999}) is None


# ── the values the shipped workflows really carry ─────────────────────


@pytest.mark.parametrize("raw,height", [
    ("2K", 1440),
    ("4K", 2160),
    ("1K", 1080),
    ("1080p", 1080),
    (2160, 2160),
])
def test_the_resolution_strings_the_workflows_ship_are_understood(raw, height):
    """`upscale_resolution` is a STRING in every shipped workflow — `'2K'`,
    `'4K'`, `'None'`. The int-only check dropped all of them, so the handler's
    default of 4 applied a 4x enlargement to a node that had asked for 2K."""
    found = op_named(
        node(upscale_2k_4k=True, upscale_resolution=raw),
        [wire(media(), "media")],
        "upscale",
    )
    assert found[0]["targetHeight"] == height


@pytest.mark.parametrize("raw", ["None", "", None, "auto"])
def test_no_resolution_falls_back_to_the_model_scale(raw):
    found = op_named(
        node(upscale_2k_4k=True, upscale_resolution=raw),
        [wire(media(), "media")],
        "upscale",
    )
    assert found and "targetHeight" not in found[0]


def test_a_remembered_resolution_is_not_a_request():
    """Counted on the shipped workflows: four nodes carry `'2K'` with
    `upscale_2k_4k` still False. The value is what the dropdown last showed,
    not an instruction — treating it as one would re-encode every clip on those
    boards for nothing."""
    assert op_named(
        node(upscale_resolution="2K"), [wire(media(), "media")], "upscale"
    ) == []


# ── the narration passes ──────────────────────────────────────────────


def _voice_chain(**settings):
    base = {"enable_voice": True}
    base.update(settings)
    return ops(
        node(**base),
        [wire(media(), "media"), wire(media("audio", "m-voice"), "voice")],
    )


def test_the_mix_takes_the_video_not_the_retimed_narration():
    """`source()` means "the previous step's output", and after a voice_speed
    pass that output is AUDIO. The mix took it as its video input, so every
    edit_video with a voice speed died at ffmpeg."""
    chain = _voice_chain(voice_speed=1.25)
    speed = next(op for op in chain if op["op"] == "voice_speed")
    mix = next(op for op in chain if op["op"] == "bgm")
    assert speed["speed"] == 1.25
    assert mix["video"] != "__previous__", mix
    assert mix["track"] == "__previous__", "the narration must be the retimed one"


def test_fitting_the_narration_does_not_then_lay_it_over_itself():
    """`fit_narration` returns the clip with the narration already in it."""
    chain = _voice_chain(auto_voice_speed=True)
    assert [op["op"] for op in chain].count("bgm") == 0, chain


@pytest.mark.parametrize("speed", [5.0, 0.1, -1])
def test_an_impossible_voice_speed_is_skipped_not_dispatched(speed):
    """`atempo` takes 0.5–2.0 a stage and this build chains two. Forwarding 5.0
    failed the whole node; `video_speed` has been range-checked all along."""
    chain = _voice_chain(voice_speed=speed)
    assert not [op for op in chain if op["op"] == "voice_speed"]


def test_a_speed_of_one_adds_no_pass():
    chain = _voice_chain(voice_speed=1.0)
    assert not [op for op in chain if op["op"] == "voice_speed"]
