"""Which local ops a post-production node runs, and on what.

The wires decide the inputs, and the port on each wire decides its role.
Getting that wrong produces a video that renders fine and is simply wrong —
a merge missing clips, narration mixed in as background music — so each
pairing is pinned against the ports the shipped workflows actually use.

The other half of this contract lives in `test_postprod_contract.py`: that
the handler accepts what this module emits. Neither file is sufficient alone;
they were both green while the two modules spoke different languages.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flowboard.services import postprod_plan
from flowboard.services.postprod_plan import PREVIOUS, Upstream, ops_for


def node(node_type, *, media=None, medias=None, prompt=None, settings=None):
    data = {}
    if media:
        data["mediaId"] = media
    if medias is not None:
        data["mediaIds"] = medias
    if prompt:
        data["prompt"] = prompt
    if settings is not None:
        data["sourceSettings"] = settings
    return SimpleNamespace(id=1, type=node_type, data=data)


def up(node_obj, port):
    return Upstream(node_obj, port)


# ── merge ────────────────────────────────────────────────────────────────

def test_merge_joins_every_clip_wired_into_it():
    ups = [
        up(node("video", media="a"), "media"),
        up(node("video", media="b"), "media"),
    ]
    assert ops_for(node("merge_video"), ups) == [{"op": "concat", "clips": ["a", "b"]}]


def test_merge_expands_one_upstreams_variants():
    """Three of the nine shipped workflows wire ONE generation node into the
    merge. Reading a single media id per upstream saw one clip and did
    nothing — the clips it was meant to join were the variants."""
    ups = [up(node("video", medias=["v1", "v2", "v3", "v4"]), "media")]
    assert ops_for(node("merge_video"), ups) == [
        {"op": "concat", "clips": ["v1", "v2", "v3", "v4"]}
    ]


def test_a_blocked_variant_does_not_become_a_gap_in_the_merge():
    """`mediaIds` keeps a positional None where a variant was filtered."""
    ups = [up(node("video", medias=["v1", None, "v3"]), "media")]
    assert ops_for(node("merge_video"), ups)[0]["clips"] == ["v1", "v3"]


def test_merge_ignores_a_wire_that_is_not_the_clip_input():
    ups = [
        up(node("prompt", prompt="hi"), "text"),
        up(node("video", media="a"), "media"),
        up(node("video", media="b"), "media"),
    ]
    assert ops_for(node("merge_video"), ups)[0]["clips"] == ["a", "b"]


def test_merging_one_clip_does_nothing():
    ups = [up(node("video", media="a"), "media")]
    assert ops_for(node("merge_video"), ups) == []


def test_a_hand_drawn_wire_with_no_port_still_feeds_a_merge():
    """Edges drawn on the canvas carry no port name. Requiring one would make
    every hand-built board's post-production node inert."""
    ups = [up(node("video", media="a"), None), up(node("video", media="b"), None)]
    assert ops_for(node("merge_video"), ups)[0]["clips"] == ["a", "b"]


# ── voice vs music: the pairing that was wrong ───────────────────────────

def test_narration_on_the_voice_port_is_not_used_as_background_music():
    """The shipped `edit_video` nodes have a create_voice node on `voice`.
    Taking "the first upstream that produces audio" mixed the narration in as
    music at 0.9 volume."""
    ups = [
        up(node("video", media="clip"), "media"),
        up(node("create_voice", media="vo"), "voice"),
    ]
    ops = ops_for(node("edit_video", settings={"enable_bgm": True}), ups)
    assert ops == [], "the narration was picked up as a music track"


def test_background_music_comes_from_the_settings_not_a_wire():
    """No shipped workflow wires audio into a music input; the track is a
    filename in the node's settings."""
    ups = [up(node("video", media="clip"), "media")]
    ops = ops_for(
        node("edit_video", settings={"enable_bgm": True, "bgm_sample_file": "calm.mp3"}),
        ups,
    )
    assert [o["op"] for o in ops] == ["bgm"]
    assert ops[0]["track"] == "calm.mp3"


#: Verbatim from `tap_hoa.json` — the original author's own machine, a
#: directory that does not exist here. The FILENAME is in this machine's
#: `nhac_nen` library, which is what makes trimming the right move.
SHIPPED_DEAD_PATH = r"d:\ABCD\VEO3_GROK_NEW\data_general\nhac_nen\1. Nhạc.MP3"


def test_a_dead_absolute_music_path_falls_back_to_its_filename():
    """Trimming the dead directory is the difference between a working music
    pass and a guaranteed `missing_media` on three of the four shipped
    music references."""
    ups = [up(node("video", media="clip"), "media")]
    op = ops_for(
        node(
            "edit_video",
            settings={"enable_bgm": True, "audio_path": SHIPPED_DEAD_PATH},
        ),
        ups,
    )[0]
    assert op["track"] == "1. Nhạc.MP3"


def test_a_posix_music_path_is_trimmed_too():
    """One shipped node uses forward slashes: `F:/This PC/Downloads/...`."""
    ups = [up(node("video", media="clip"), "media")]
    op = ops_for(
        node(
            "add_bgm",
            settings={"audio_path": "F:/This PC/Downloads/nhac nen han.mp3"},
        ),
        ups,
    )[0]
    assert op["track"] == "nhac nen han.mp3"


def test_a_bare_filename_is_left_alone():
    ups = [up(node("video", media="clip"), "media")]
    op = ops_for(
        node("add_bgm", settings={"bgm_sample_file": "calm.mp3"}), ups
    )[0]
    assert op["track"] == "calm.mp3"


def test_the_sample_file_setting_wins_over_the_dead_absolute_path():
    """One shipped node carries both. The bare name is the one that works."""
    ups = [up(node("video", media="clip"), "media")]
    op = ops_for(
        node(
            "add_bgm",
            settings={
                "bgm_sample_file": "1. Nhạc.MP3",
                "audio_path": r"d:\ABCD\gone\other.mp3",
            },
        ),
        ups,
    )[0]
    assert op["track"] == "1. Nhạc.MP3"


def test_voice_and_music_are_two_separate_passes():
    ups = [
        up(node("video", media="clip"), "media"),
        up(node("create_voice", media="vo"), "voice"),
    ]
    ops = ops_for(
        node(
            "edit_video",
            settings={
                "enable_voice": True,
                "enable_bgm": True,
                "bgm_sample_file": "calm.mp3",
            },
        ),
        ups,
    )
    assert [o["op"] for o in ops] == ["bgm", "bgm"]
    assert ops[0]["track"] == "vo", "the voice pass must use the voice"
    assert ops[1]["track"] == "calm.mp3"
    assert ops[1]["video"] == PREVIOUS


def test_an_unnamed_wire_is_never_assumed_to_be_a_voice():
    """The strict half of the port rule. Guessing is the original bug."""
    ups = [
        up(node("video", media="clip"), "media"),
        up(node("create_voice", media="vo"), None),
    ]
    ops = ops_for(node("edit_video", settings={"enable_voice": True}), ups)
    assert ops == []


# ── align ────────────────────────────────────────────────────────────────

def test_align_pairs_the_clip_with_the_voice_by_port():
    ups = [
        up(node("video", media="clip"), "media"),
        up(node("create_voice", media="vo"), "voice"),
    ]
    assert ops_for(node("align_video_voice"), ups) == [
        {"op": "fit_narration", "video": "clip", "audio": "vo"}
    ]


def test_align_with_upscale_chains_onto_its_own_output():
    ups = [
        up(node("video", media="clip"), "media"),
        up(node("create_voice", media="vo"), "voice"),
    ]
    chain = ops_for(node("align_video_voice", settings={"upscale_2k_4k": True}), ups)
    assert chain[1]["op"] == "upscale"
    # The handler reads its input from `source`, not `video`.
    assert chain[1]["source"] == PREVIOUS


def test_align_without_a_voice_does_nothing_rather_than_running_silent():
    ups = [up(node("video", media="clip"), "media")]
    assert ops_for(node("align_video_voice"), ups) == []


# ── the rest ─────────────────────────────────────────────────────────────

def test_last_frame_reads_the_clip_on_its_video_port():
    ups = [up(node("video", media="clip"), "video")]
    assert ops_for(node("extract_last_frame"), ups) == [
        {"op": "last_frame", "video": "clip"}
    ]


def test_narration_text_comes_from_the_wire_on_the_text_port():
    ups = [up(node("prompt", prompt="kịch bản"), "text")]
    ops = ops_for(node("create_voice", settings={"voice": "Kore"}), ups)
    # `engine` is emitted even when it is the default one: the worker then
    # never has to guess which endpoint the plan meant.
    assert ops == [
        {"op": "narrate", "text": "kịch bản", "voice": "Kore", "engine": "gemini"}
    ]


def test_narration_falls_back_to_the_nodes_own_text():
    ops = ops_for(node("create_voice", prompt="tự gõ"), [])
    assert ops[0]["text"] == "tự gõ"


def test_narration_with_no_script_does_nothing():
    assert ops_for(node("create_voice"), []) == []


def test_the_title_text_comes_from_the_title_port():
    """`aspect_texts` in the shipped files is a list of STYLE dicts whose
    `text` is the placeholder `{title}`, never the title itself. Reading the
    list as a string meant the title pass never fired at all.

    The styled pass draws it now — see test_aspect_convert.py — but the
    invariant this test exists for is unchanged: whichever pass runs, the
    words come from the node's `title` port."""
    ups = [
        up(node("video", media="clip"), "media"),
        up(node("prompt", prompt="TIÊU ĐỀ"), "title"),
    ]
    ops = ops_for(
        node("edit_video", settings={"aspect_texts": [{"text": "{title}"}]}), ups
    )
    assert [o["op"] for o in ops] == ["text_art"]
    assert ops[0]["texts"][0]["text"] == "TIÊU ĐỀ"


def test_a_title_with_no_styling_takes_the_plain_pass():
    """The other half of the pair: with no `aspect_texts` there is nothing
    to style, and the plain drawtext pass is what draws the title."""
    ups = [
        up(node("video", media="clip"), "media"),
        up(node("prompt", prompt="TIÊU ĐỀ"), "title"),
    ]
    ops = ops_for(node("edit_video", settings={}), ups)
    assert [o["op"] for o in ops] == ["title"]
    assert ops[0]["text"] == "TIÊU ĐỀ"


def test_a_prompt_on_another_port_is_not_the_title():
    ups = [
        up(node("video", media="clip"), "media"),
        up(node("prompt", prompt="nội dung"), "text"),
    ]
    assert ops_for(node("edit_video", settings={}), ups) == []


# ── flags ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [True, 1, "1", "true", "True", "yes", "on"])
def test_flags_written_the_exes_many_ways_all_read_as_on(raw):
    ups = [up(node("video", media="clip"), "media")]
    ops = ops_for(
        node("edit_video", settings={"enable_bgm": raw, "audio_path": "x.mp3"}), ups
    )
    assert len(ops) == 1


@pytest.mark.parametrize("raw", [False, 0, "0", "false", "", None])
def test_flags_that_mean_off_stay_off(raw):
    ups = [up(node("video", media="clip"), "media")]
    ops = ops_for(
        node("edit_video", settings={"enable_bgm": raw, "audio_path": "x.mp3"}), ups
    )
    assert ops == []


def test_a_boolean_is_not_mistaken_for_a_volume():
    """`True` is an int in Python; passed through it becomes a volume of 1.0."""
    ups = [up(node("video", media="clip"), "media")]
    op = ops_for(
        node(
            "edit_video",
            settings={"enable_bgm": True, "audio_path": "x.mp3", "bgm_volume": True},
        ),
        ups,
    )[0]
    assert "bgmVolume" not in op


def test_mute_wins_over_an_explicit_original_volume():
    """Both appear in the shipped templates and they contradict; the mute
    flag is the explicit instruction."""
    ups = [up(node("video", media="clip"), "media")]
    op = ops_for(
        node(
            "add_bgm",
            settings={"audio_path": "x.mp3", "orig_volume": 0.8, "mute_origin": True},
        ),
        ups,
    )[0]
    assert op["origVolume"] == 0.0


# ── chain ordering ───────────────────────────────────────────────────────

def test_only_the_first_pass_reads_the_source_clip():
    ups = [
        up(node("video", media="clip"), "media"),
        up(node("create_voice", media="vo"), "voice"),
    ]
    chain = ops_for(
        node(
            "edit_video",
            settings={
                "enable_voice": True,
                "enable_bgm": True,
                "bgm_sample_file": "m.mp3",
                "upscale_2k_4k": True,
            },
        ),
        ups,
    )
    assert chain[0]["video"] == "clip"
    for step in chain[1:]:
        assert step.get("video", step.get("source")) == PREVIOUS


def test_the_first_enabled_pass_reads_the_source_whichever_one_it_is():
    ups = [up(node("video", media="clip"), "media")]
    chain = ops_for(node("edit_video", settings={"upscale_2k_4k": True}), ups)
    assert chain[0]["source"] == "clip"


def test_edit_video_with_nothing_enabled_does_nothing():
    ups = [up(node("video", media="clip"), "media")]
    assert ops_for(node("edit_video", settings={"enable_sub": False}), ups) == []


def test_edit_video_with_no_clip_does_nothing():
    assert ops_for(node("edit_video", settings={"upscale_2k_4k": True}), []) == []


# ── deliberate gaps ──────────────────────────────────────────────────────

# -- sync_image_voice: a still image, zoomed over the narration ----------

def test_sync_image_voice_pairs_the_still_with_the_voice():
    """The exe's own filter is `zoompan`; the clip runs as long as the
    narration. `media` carries a still image here, not a clip."""
    ups = [
        up(node("image", media="still"), "media"),
        up(node("create_voice", media="vo"), "voice"),
    ]
    ops = ops_for(
        node(
            "sync_image_voice",
            settings={
                "effect": "Zoom In",
                "zoom_speed": 0.002,
                "aspect_ratio": "16:9",
            },
        ),
        ups,
    )
    assert len(ops) == 1
    assert ops[0]["op"] == "ken_burns"
    assert ops[0]["image"] == "still"
    assert ops[0]["audio"] == "vo"
    assert ops[0]["zoomIn"] is True
    assert ops[0]["zoomSpeed"] == 0.002
    assert (ops[0]["width"], ops[0]["height"]) == (1920, 1080)


def test_the_zoom_direction_comes_from_the_decorated_effect_string():
    """The shipped value carries an emoji prefix, so the direction is matched
    on the word inside rather than on the whole string."""
    ups = [
        up(node("image", media="still"), "media"),
        up(node("create_voice", media="vo"), "voice"),
    ]
    out = ops_for(node("sync_image_voice", settings={"effect": "Zoom Out"}), ups)
    assert out[0]["zoomIn"] is False


@pytest.mark.parametrize(
    "aspect,size",
    [("16:9", (1920, 1080)), ("9:16", (1080, 1920)),
     ("1:1", (1080, 1080)), (None, (1920, 1080))],
)
def test_the_frame_size_follows_the_aspect_setting(aspect, size):
    ups = [
        up(node("image", media="still"), "media"),
        up(node("create_voice", media="vo"), "voice"),
    ]
    op = ops_for(node("sync_image_voice", settings={"aspect_ratio": aspect}), ups)[0]
    assert (op["width"], op["height"]) == size


def test_sync_image_voice_without_a_voice_does_nothing():
    """Its length comes from the narration; with no audio there is no clip."""
    ups = [up(node("image", media="still"), "media")]
    assert ops_for(node("sync_image_voice"), ups) == []


def test_analyze_video_is_not_faked_as_a_local_op():
    """It reads a video with Gemini. Returning an ffmpeg op would make the
    node report success having done something else entirely."""
    ups = [up(node("video", media="clip"), "video")]
    assert ops_for(node("analyze_video"), ups) == []


def test_an_unknown_node_type_runs_nothing():
    ups = [up(node("video", media="clip"), "media")]
    assert ops_for(node("some_future_type"), ups) == []


# "every postprod node type is handled" used to sit here as a hardcoded set
# equality. It cost a manual edit on every new type and, when it failed, said
# only that the set had changed — never which of the eight registries the new
# type was missing from.
#
# `tests/test_node_type_registration.py` now derives that check from
# `_POSTPROD_NODE_TYPES` itself and asserts each registry separately, across
# both languages, so a new type is covered without anyone remembering to.


def test_the_previous_placeholder_cannot_collide_with_a_media_id():
    from flowboard.services.media import is_valid_media_id

    assert not is_valid_media_id(PREVIOUS)
    assert postprod_plan.PREVIOUS == "__previous__"


# ── watermark: two methods, and the exe defaults to the other one ────────

LOGO_BOX = {
    "veo_logo_x_pct": 78.0,
    "veo_logo_y_pct": 2.0,
    "veo_logo_w_pct": 20.0,
    "veo_logo_h_pct": 10.0,
}


def _logo_chain(extra):
    return ops_for(
        node("edit_video", settings={
            "enable_remove_veo_logo": True, **LOGO_BOX, **extra,
        }),
        [up(node("video", media="clip"), "media")],
    )


def test_the_default_method_is_zoom_because_that_is_what_the_exe_uses():
    """`veo_logo_method: "zoom"` is the value in the shipped workflow. The
    first version of this build only had delogo, which blurs instead of
    cropping — a visibly different result under the same setting."""
    op = _logo_chain({})[0]
    assert op["op"] == "zoom_logo"


def test_the_zoom_percent_is_carried_across():
    op = _logo_chain({"veo_logo_zoom_percent": 110})[0]
    assert op["zoomPercent"] == 110.0


def test_delogo_is_still_available_when_asked_for():
    op = _logo_chain({"veo_logo_method": "delogo"})[0]
    assert op["op"] == "delogo"
    assert "zoomPercent" not in op


def test_the_box_reaches_both_methods_as_percentages():
    """Pixels cannot be computed here — the frame size is not known until
    the handler probes the file."""
    for method in ("zoom", "delogo"):
        op = _logo_chain({"veo_logo_method": method})[0]
        assert op["xPct"] == 78.0
        assert op["heightPct"] == 10.0


def test_no_box_means_no_pass_whichever_method():
    """A guessed rectangle crops or blurs the wrong part of the frame."""
    for method in ("zoom", "delogo"):
        ops = ops_for(
            node("edit_video", settings={
                "enable_remove_veo_logo": True, "veo_logo_method": method,
            }),
            [up(node("video", media="clip"), "media")],
        )
        assert ops == []
