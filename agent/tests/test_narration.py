"""Long narration, one segment at a time.

Sent whole, a ten-minute story is one request away from being lost entirely.
The packaged tool splits it, retries pieces individually and keeps a manifest
so a single bad segment can be redone without paying for the other forty —
that structure is the good idea, and it is engine-agnostic.

Two decisions carry the weight, and both are about not producing something
that *sounds* fine:

* **Split at sentence boundaries, never inside one.** A synthesiser reads each
  piece as a complete utterance, so a cut mid-sentence puts a falling
  intonation and a breath in the middle of a thought.
* **Refuse to merge around a hole.** A narration missing one sentence sounds
  complete. Nobody listening can tell, and neither can the board.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from flowboard.services import narration


# ── splitting ─────────────────────────────────────────────────────────


def test_sentences_are_never_cut_in_half():
    text = " ".join(f"Câu số {i} dài vừa phải." for i in range(40))
    parts = narration.split_text(text, max_chars=120)
    assert len(parts) > 1
    for part in parts:
        assert part.endswith(".")


def test_short_text_stays_in_one_piece():
    assert narration.split_text("Một câu ngắn.") == ["Một câu ngắn."]


def test_a_single_sentence_longer_than_the_limit_is_left_whole():
    """An over-long request is a failure this module already handles. A
    mangled sentence is one it would have caused."""
    long_one = "Đây là một câu rất dài " * 40 + "."
    parts = narration.split_text(long_one, max_chars=100)
    assert len(parts) == 1
    assert len(parts[0]) > 100


def test_newlines_split_as_well_as_full_stops():
    parts = narration.split_text("Dòng một\nDòng hai\nDòng ba", max_chars=12)
    assert parts == ["Dòng một", "Dòng hai", "Dòng ba"]


@pytest.mark.parametrize("text", ["", "   ", "\n\n"])
def test_empty_text_yields_nothing(text):
    assert narration.split_text(text) == []


def test_planning_empty_text_is_an_error_not_an_empty_run():
    with pytest.raises(narration.NarrationError):
        narration.plan("   ")


def test_segments_are_numbered_in_reading_order():
    manifest = narration.plan("Một. Hai. Ba.", max_chars=6)
    assert [s.index for s in manifest.segments] == list(range(len(manifest.segments)))


# ── synthesising, and failing one piece at a time ─────────────────────


def _synth_ok(tmp_path):
    calls: list[str] = []

    def _synth(chunk: str) -> Path:
        calls.append(chunk)
        out = tmp_path / f"part-{len(calls)}.mp3"
        out.write_bytes(b"\x00" * 16)
        return out

    return _synth, calls


def test_every_segment_is_synthesised(tmp_path):
    synth, calls = _synth_ok(tmp_path)
    manifest = narration.synthesize_manifest(
        narration.plan("Một. Hai. Ba.", max_chars=6), synth
    )
    assert len(calls) == len(manifest.segments)
    assert all(s.status == "done" for s in manifest.segments)


def test_a_flaky_segment_is_retried_rather_than_losing_the_run(tmp_path):
    attempts = {"n": 0}

    def _synth(chunk: str) -> Path:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("mạng chập")
        out = tmp_path / f"p{attempts['n']}.mp3"
        out.write_bytes(b"\x00")
        return out

    manifest = narration.synthesize_manifest(narration.plan("Một câu."), _synth)
    assert manifest.segments[0].status == "done"
    assert manifest.segments[0].attempts == 2


def test_one_dead_segment_does_not_stop_the_others(tmp_path):
    """A narration with one hole and a manifest saying which is far more
    useful than an exception and nothing."""
    def _synth(chunk: str) -> Path:
        if "Hai" in chunk:
            raise RuntimeError("đoạn này hỏng")
        out = tmp_path / f"{abs(hash(chunk))}.mp3"
        out.write_bytes(b"\x00")
        return out

    manifest = narration.synthesize_manifest(
        narration.plan("Một. Hai. Ba.", max_chars=6), _synth
    )
    statuses = [s.status for s in manifest.segments]
    assert "failed" in statuses and "done" in statuses
    assert len(manifest.failed) == 1


def test_a_dead_segment_keeps_its_reason(tmp_path):
    def _synth(chunk: str) -> Path:
        raise RuntimeError("hết quota")

    manifest = narration.synthesize_manifest(narration.plan("Một câu."), _synth)
    assert "hết quota" in manifest.segments[0].error


def test_giving_up_takes_the_configured_number_of_attempts(tmp_path):
    calls = {"n": 0}

    def _synth(chunk: str) -> Path:
        calls["n"] += 1
        raise RuntimeError("luôn hỏng")

    narration.synthesize_manifest(narration.plan("Một câu."), _synth, max_attempts=2)
    assert calls["n"] == 2


# ── regenerating one segment ──────────────────────────────────────────


def test_rerunning_leaves_the_finished_segments_alone(tmp_path):
    """They cost money and time. Redoing them to fix a neighbour is exactly
    the waste the manifest exists to prevent."""
    synth, calls = _synth_ok(tmp_path)
    manifest = narration.synthesize_manifest(
        narration.plan("Một. Hai. Ba.", max_chars=6), synth
    )
    before = len(calls)

    narration.synthesize_manifest(manifest, synth)
    assert len(calls) == before, "a finished segment was re-synthesised"


def test_a_named_segment_can_be_redone_on_its_own(tmp_path):
    synth, calls = _synth_ok(tmp_path)
    manifest = narration.synthesize_manifest(
        narration.plan("Một. Hai. Ba.", max_chars=6), synth
    )
    before = len(calls)

    narration.synthesize_manifest(manifest, synth, only={1})
    assert len(calls) == before + 1


# ── the manifest survives a round trip ────────────────────────────────


def test_the_manifest_reads_back_as_it_was_written(tmp_path):
    synth, _ = _synth_ok(tmp_path)
    manifest = narration.synthesize_manifest(
        narration.plan("Một. Hai.", max_chars=6, pause_s=0.5), synth
    )
    path = manifest.write(tmp_path / "segments.json")

    reloaded = narration.Manifest.read(path)
    assert len(reloaded.segments) == len(manifest.segments)
    assert reloaded.pause_s == 0.5
    assert [s.text for s in reloaded.segments] == [s.text for s in manifest.segments]
    assert all(s.status == "done" for s in reloaded.segments)


def test_a_corrupt_manifest_is_an_error_not_an_empty_one(tmp_path):
    bad = tmp_path / "segments.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(narration.NarrationError):
        narration.Manifest.read(bad)


# ── merging ───────────────────────────────────────────────────────────


def test_merging_around_a_hole_is_refused(tmp_path, monkeypatch):
    """A narration missing one sentence sounds complete. Nobody listening
    can tell, and neither can the board."""
    def _synth(chunk: str) -> Path:
        if "Hai" in chunk:
            raise RuntimeError("hỏng")
        out = tmp_path / f"{abs(hash(chunk))}.mp3"
        out.write_bytes(b"\x00")
        return out

    manifest = narration.synthesize_manifest(
        narration.plan("Một. Hai. Ba.", max_chars=6), _synth
    )
    with pytest.raises(narration.NarrationError) as exc:
        narration.merge(manifest, tmp_path / "out.mp3")
    assert "thiếu câu" in str(exc.value)


def test_a_complete_manifest_merges_in_order(tmp_path, monkeypatch):
    from flowboard.services import postprod

    joined: dict = {}

    def _fake_concat(parts, dst, *, pause_s=0.0):
        joined["parts"] = [Path(p).name for p in parts]
        joined["pause"] = pause_s
        Path(dst).write_bytes(b"\x00")
        return Path(dst)

    monkeypatch.setattr(postprod, "concat_audio", _fake_concat)

    synth, _ = _synth_ok(tmp_path)
    manifest = narration.synthesize_manifest(
        narration.plan("Một. Hai. Ba.", max_chars=6, pause_s=0.4), synth
    )
    out = narration.merge(manifest, tmp_path / "out.mp3")

    assert joined["parts"] == ["part-1.mp3", "part-2.mp3", "part-3.mp3"]
    assert joined["pause"] == 0.4
    assert manifest.output == str(out)


def test_an_empty_manifest_cannot_be_merged(tmp_path):
    with pytest.raises(narration.NarrationError):
        narration.merge(narration.Manifest(), tmp_path / "out.mp3")


# ── quoted dialogue is most of what this narrates ─────────────────────


def test_a_sentence_ending_inside_quotes_still_splits():
    """The period goes BEFORE the closing quote, so a lookbehind on the
    terminator alone never matched. On a dialogue-heavy script — most of what
    this app narrates — that meant no segmentation at all: one request, one
    timeout, the whole story lost."""
    text = 'Anh nói: "Tôi không biết." Rồi anh đi ra. Cô ấy gọi: "Đợi đã!" Không ai nghe.'
    parts = narration.split_text(text, max_chars=30)
    assert len(parts) > 1, parts


def test_the_closing_quote_stays_with_the_sentence_it_closes():
    """Consuming it would hand the synthesiser an unbalanced quotation."""
    parts = narration.split_text(
        'Anh nói: "Tôi không biết." Rồi anh đi.', max_chars=26
    )
    assert parts[0].endswith('."'), parts


@pytest.mark.parametrize("closer", ['"', "'", "”", "’", "»", ")", "]"])
def test_every_closer_the_scripts_use(closer):
    parts = narration.split_text(f"Một câu.{closer} Hai câu.", max_chars=12)
    assert len(parts) == 2, parts
    assert parts[0].endswith(closer)


def test_an_abbreviation_is_not_a_sentence_end():
    """`TP.HCM` has no space after the dot, which is what keeps it whole — and
    the wider pattern must not change that."""
    parts = narration.split_text("TP.HCM là một thành phố lớn.", max_chars=100)
    assert parts == ["TP.HCM là một thành phố lớn."]


# ── the manifest survives the failure it exists for ───────────────────


def test_the_manifest_is_written_as_the_run_goes(tmp_path):
    """Written at the end it was never written on the one run that needed it:
    `merge` refuses a manifest with holes, so a failed segment returned an
    error and the finished segments became files nothing pointed at."""
    written: list[int] = []

    def _synth(chunk: str) -> Path:
        if "Hai" in chunk:
            raise RuntimeError("đoạn này hỏng")
        out = tmp_path / f"{abs(hash(chunk))}.mp3"
        out.write_bytes(b"\x00")
        return out

    manifest_path = tmp_path / "segments.json"

    def _progress(m) -> None:
        m.write(manifest_path)
        written.append(len([s for s in m.segments if s.status == "done"]))

    manifest = narration.synthesize_manifest(
        narration.plan("Một. Hai. Ba.", max_chars=6), _synth, on_progress=_progress
    )

    assert len(manifest.failed) == 1
    assert manifest_path.is_file(), "the record of what was paid for is missing"
    import json

    saved = json.loads(manifest_path.read_text(encoding="utf-8"))
    done = [s for s in saved["segments"] if s["status"] == "done"]
    assert len(done) == 2
    assert all(s["path"] for s in done), "a finished segment has no file to reuse"
    # Called after every segment, not only at the end.
    assert written == [1, 1, 2], written


def test_no_progress_callback_is_fine(tmp_path):
    synth, _ = _synth_ok(tmp_path)
    manifest = narration.synthesize_manifest(
        narration.plan("Một. Hai.", max_chars=6), synth
    )
    assert all(s.status == "done" for s in manifest.segments)
