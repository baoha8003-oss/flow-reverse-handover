"""Two engines for one job, and the presets that could not be recovered.

Both halves are about being honest when the answer is partial.

**Upscaling** ships two binaries in the same folder. Reporting "unavailable"
because one of them is missing, while a working engine sits beside it, is a
capability lost to a check that was too narrow. But a silent swap is worse
than no swap: the two produce visibly different output, so which one ran has
to be visible.

**Text-art presets**: all thirty-eight names were recoverable from the binary;
only two sets of colour stops were. The tests below pin the reason that is a
refusal rather than a gap — two extraction rules each got one of the two known
presets right and the other wrong, so neither can be trusted for the
thirty-six whose answers nobody knows.
"""
from __future__ import annotations

import pytest

from flowboard.services import assets, postprod, upscale


# ── which upscale engine runs ─────────────────────────────────────────


def test_either_binary_is_enough_to_be_available(monkeypatch):
    """The check used to name one engine. On a machine where the Vulkan
    build is missing, upscaling reported itself unavailable while a working
    engine sat in the same folder."""
    monkeypatch.setattr(assets, "realesrgan_bin", lambda: None)
    monkeypatch.setattr(assets, "upscayl_bin", lambda: "C:/x/upscayl-bin.exe")
    monkeypatch.setattr(upscale, "models", lambda: ["realesrgan-x4plus"])
    assert upscale.available() is True
    assert upscale.engine_name() == "upscayl"


def test_neither_binary_means_unavailable(monkeypatch):
    monkeypatch.setattr(assets, "realesrgan_bin", lambda: None)
    monkeypatch.setattr(assets, "upscayl_bin", lambda: None)
    assert upscale.available() is False
    assert upscale.engine_name() is None


def test_a_binary_with_no_models_is_not_available(monkeypatch):
    monkeypatch.setattr(assets, "realesrgan_bin", lambda: "C:/x/realesrgan.exe")
    monkeypatch.setattr(assets, "upscayl_bin", lambda: None)
    monkeypatch.setattr(upscale, "models", lambda: [])
    assert upscale.available() is False


def test_realesrgan_is_preferred_when_both_are_present(monkeypatch):
    monkeypatch.setattr(assets, "realesrgan_bin", lambda: "C:/x/realesrgan.exe")
    monkeypatch.setattr(assets, "upscayl_bin", lambda: "C:/x/upscayl-bin.exe")
    monkeypatch.setattr(upscale, "models", lambda: ["realesrgan-x4plus"])
    assert upscale.engine_name() == "realesrgan"


def test_falling_back_is_logged_because_the_output_differs(monkeypatch, caplog):
    """A fallback nobody can see gets blamed on the model. "Why does this
    look different today" deserves a better answer than a shrug."""
    import logging

    monkeypatch.setattr(assets, "realesrgan_bin", lambda: None)
    monkeypatch.setattr(assets, "upscayl_bin", lambda: "C:/x/upscayl-bin.exe")
    monkeypatch.setattr(assets, "upscale_model_dir", lambda: assets.UPSCALE_DIR)
    with caplog.at_level(logging.WARNING):
        binary, _ = upscale._engine()
    assert "upscayl" in binary
    assert "upscayl" in caplog.text.lower()


def test_the_error_names_both_engines_when_neither_is_there(monkeypatch):
    monkeypatch.setattr(assets, "realesrgan_bin", lambda: None)
    monkeypatch.setattr(assets, "upscayl_bin", lambda: None)
    monkeypatch.setattr(assets, "upscale_model_dir", lambda: None)
    with pytest.raises(RuntimeError) as exc:
        upscale._engine()
    assert "realesrgan" in str(exc.value) and "upscayl" in str(exc.value)


# ── the preset names ──────────────────────────────────────────────────


def test_all_thirty_eight_names_are_present():
    """Mined from the ordered tuple in front of `TEXT_ART_PRESETS`. The
    module used to say thirty-seven, which was an estimate; the count
    settles it."""
    assert len(postprod.TEXT_ART_PRESET_NAMES) == 38
    assert postprod.TEXT_ART_PRESET_NAMES[0] == "Sunset Glow"
    assert postprod.TEXT_ART_PRESET_NAMES[-1] == "Cotton Candy"


def test_the_names_are_unique():
    names = [n.lower() for n in postprod.TEXT_ART_PRESET_NAMES]
    assert len(set(names)) == len(names)


@pytest.mark.parametrize("name", ["Ice Frost", "ice frost", "  COTTON CANDY  "])
def test_a_real_preset_is_recognised_however_it_is_written(name):
    assert postprod.is_known_text_art_preset(name) is True


@pytest.mark.parametrize("name", ["Ice Frosty", "", None, "None", "chưa có"])
def test_a_name_nobody_ships_is_not_recognised(name):
    assert postprod.is_known_text_art_preset(name) is False


def test_every_reproduced_gradient_is_also_a_real_preset_name():
    """A gradient keyed to a name the tool does not have would never be
    selected — dead data that reads as support."""
    for key in postprod.TEXT_ART_GRADIENTS:
        assert postprod.is_known_text_art_preset(key), key


def test_the_two_recovered_gradients_keep_their_measured_stops():
    """`gold luxury` and `sunset glow` are the cross-check that showed the
    other extraction rules were unreliable. If either drifts, the evidence
    that justified refusing the rest is gone."""
    assert postprod.TEXT_ART_GRADIENTS["sunset glow"] == (
        "#ff4500", "#ff8c00", "#ffdf00",
    )
    assert postprod.TEXT_ART_GRADIENTS["gold luxury"] == (
        "#3d2b00", "#ffe891", "#d4af37", "#8a6f27",
    )


def test_the_unrecovered_presets_are_not_quietly_given_a_nearby_gradient():
    """Substituting a similar one would look like it worked, and the user
    would ship a video with the wrong colours believing otherwise."""
    for name in postprod.TEXT_ART_PRESET_NAMES:
        if name.lower() in postprod.TEXT_ART_GRADIENTS:
            continue
        assert postprod._text_art_gradient(name) is None, name


# ── what the render says about a preset it cannot draw ────────────────


def _art(effect: str) -> postprod.TextArt:
    return postprod.TextArt(text="Xin chào", font="Arial", size=40, art_effect=effect)


def test_a_known_preset_says_the_build_cannot_draw_it(tmp_path, monkeypatch):
    """Sends the reader to the right place: this is a limitation here, not
    a mistake in their workflow."""
    notes = _notes_for(_art("Ice Frost"), tmp_path, monkeypatch)
    assert any("tool gốc" in n for n in notes), notes


def test_an_unknown_name_says_it_does_not_exist(tmp_path, monkeypatch):
    """A different sentence, because it points at a probable misspelling
    rather than at this build."""
    notes = _notes_for(_art("Ice Frosty"), tmp_path, monkeypatch)
    assert any("Không có hiệu ứng" in n for n in notes), notes


def test_no_effect_produces_no_complaint(tmp_path, monkeypatch):
    assert _notes_for(_art("None"), tmp_path, monkeypatch) == []


def _notes_for(art, tmp_path, monkeypatch) -> list[str]:
    """Build the filter graph without running ffmpeg.

    The notes are decided while the graph is assembled, so the render never
    has to happen — and these tests then need no ffmpeg, no clip, and no
    seconds.
    """
    import json
    import subprocess

    def _fake_run(args, *, what, timeout=None):
        # ffprobe is asked for the frame size, ffmpeg is asked to render, and
        # both go through this one helper — so one stub covers the pair and
        # the test needs no ffmpeg, no clip and no seconds.
        payload = ""
        if any("ffprobe" in str(a) for a in args):
            payload = json.dumps(
                {"streams": [{"codec_type": "video", "width": 720, "height": 1280}]}
            )
        return subprocess.CompletedProcess(args, 0, stdout=payload, stderr="")

    monkeypatch.setattr(postprod, "_run", _fake_run)
    monkeypatch.setattr(postprod, "probe_duration", lambda p: 5.0)

    src = tmp_path / "in.mp4"
    src.write_bytes(b"\x00" * 64)
    dst = tmp_path / "out.mp4"
    dst.write_bytes(b"\x00" * 64)   # the renderer validates its own output
    _, notes = postprod.overlay_text_art(src, dst, [art])
    return notes
