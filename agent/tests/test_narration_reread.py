"""Reading the failed segments again, and not paying for the finished ones.

The capability this closes was supposed to exist already. A long script is
narrated segment by segment and a manifest records which segments were bought —
built precisely so that one failure out of forty would cost one segment to fix.

It could never happen. The manifest was keyed on the request id of the run that
wrote it; every re-run of a node mints a new request row; and `postprod` errors
classify terminal, so the worker never retried in-row either. The record was
written and never read.

So the tests that matter here count synthesiser calls. Everything else — the
guards, the route, the preview — exists to stop a partial read from splicing new
audio into the wrong place, which is the only way this feature can be worse than
re-reading everything.
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

import pytest

from flowboard.services import narration
from flowboard.worker.postprod_handler import handle_postprod

#: Long enough that `_op_narrate` takes the segmenting branch rather than the
#: one-shot one. Three sentences, each its own segment.
SENTENCES = [
    "Ngày xưa có một người tiều phu sống ở chân núi. " * 12,
    "Mỗi sáng anh ta lên rừng đốn củi rồi mang xuống chợ bán. " * 12,
    "Một hôm anh gặp một ông lão ngồi bên bờ suối. " * 12,
]
SCRIPT = "".join(SENTENCES)


def _wav(path: Path) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 2400)
    return path


@pytest.fixture
def synth(monkeypatch, tmp_path):
    """A stubbed voice that counts what it was asked to read.

    Gemini TTS bills a real key, so the suite never calls it — the established
    pattern in `test_postprod_worker.py`. Counting is the point: the whole
    feature is "how many segments did we pay for".
    """
    calls: list[str] = []
    fail_on: set[int] = set()
    #: Segment text -> its index, so a failure can be pinned to a SEGMENT rather
    #: than to a call number. Keyed on the call number, `synthesize_manifest`'s own
    #: retry would land on a different number and quietly succeed — the segment
    #: would never end up failed, and the test would be asserting nothing.
    index_of = {seg.text: seg.index for seg in narration.plan(SCRIPT).segments}

    def _fake(text, *, voice, persona=None, **_kw):
        calls.append(text)
        if index_of.get(text) in fail_on:
            raise RuntimeError("giọng đọc hỏng")
        return _wav(tmp_path / f"seg-{len(calls)}-{abs(hash(text)) % 9999}.wav")

    monkeypatch.setattr(
        "flowboard.worker.postprod_handler.tts.synthesize", _fake
    )
    # Patched on the module itself: `_op_narrate` imports `narration` inside the
    # function, so there is no attribute on the handler to reach through.
    monkeypatch.setattr(
        narration, "merge", lambda manifest, out: _wav(Path(out).with_suffix(".wav"))
    )
    return type("Synth", (), {"calls": calls, "fail_on": fail_on})()


def _manifest_path(request_id: int) -> Path:
    from flowboard.worker.postprod_handler import _narration_manifest_path

    path = _narration_manifest_path({"__request_id": request_id})
    assert path is not None
    return path


async def _narrate(request_id: int, **extra) -> tuple[dict, str | None]:
    params = {
        "op": "narrate", "text": SCRIPT, "voice": "Kore",
        "__request_id": request_id, **extra,
    }
    return await handle_postprod(params)


# ── the money rule ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_reread_speaks_only_the_failed_segment(synth):
    """The one test this feature exists for."""
    synth.fail_on.add(1)
    _out, err = await _narrate(9001)
    assert err is not None and err.startswith("narrate_failed_segments")
    planned_count = len(narration.plan(SCRIPT).segments)
    assert planned_count > 1, "the fixture script must segment, or this tests nothing"
    # Distinct texts, not call count: the failing segment is attempted
    # MAX_ATTEMPTS times, so calls exceed segments on any run with a hole.
    assert len(set(synth.calls)) == planned_count

    synth.fail_on.clear()
    synth.calls.clear()
    _out, err = await _narrate(9002, onlySegments=[1], manifestRequestId=9001)
    assert err is None, err
    # One call, not three. Before this, the retry re-read the whole script.
    assert len(synth.calls) == 1


@pytest.mark.asyncio
async def test_the_reread_speaks_the_right_segment(synth):
    """Reading segment 0's text into segment 1's slot would produce a narration
    that sounds finished and says the wrong thing twice."""
    synth.fail_on.add(1)
    await _narrate(9011)
    planned = narration.plan(SCRIPT)
    wanted = planned.segments[1].text

    synth.fail_on.clear()
    synth.calls.clear()
    await _narrate(9012, onlySegments=[1], manifestRequestId=9011)
    assert synth.calls == [wanted]


@pytest.mark.asyncio
async def test_the_reread_updates_the_original_record(synth):
    """`manifestRequestId` is the whole mechanism: without it the re-read starts a
    new, empty record and the paid segments become files nothing points at."""
    last = len(narration.plan(SCRIPT).segments) - 1
    synth.fail_on.add(last)
    await _narrate(9021)
    original = _manifest_path(9021)
    assert original.exists()

    synth.fail_on.clear()
    await _narrate(9022, onlySegments=[last], manifestRequestId=9021)

    data = json.loads(original.read_text(encoding="utf-8"))
    assert data["failedCount"] == 0
    assert not _manifest_path(9022).exists()


# ── the guards ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_missing_record_does_not_fall_back_to_the_whole_script(synth):
    """Falling back would spend more than the user asked for, silently."""
    _out, err = await _narrate(9031, onlySegments=[1], manifestRequestId=8999)
    assert err == "narrate_manifest_missing"
    assert synth.calls == []


@pytest.mark.asyncio
async def test_an_edited_script_refuses_even_when_the_count_matches(synth):
    """Compared text by text. A swapped sentence keeps the segment count, and a
    count check would splice the new line's audio into the old line's slot."""
    synth.fail_on.add(1)
    await _narrate(9041)
    synth.fail_on.clear()
    synth.calls.clear()

    edited = SCRIPT.replace("người tiều phu", "người thợ rèn")
    _out, err = await handle_postprod({
        "op": "narrate", "text": edited, "voice": "Kore",
        "__request_id": 9042, "onlySegments": [1], "manifestRequestId": 9041,
    })
    assert err == "narrate_manifest_stale"
    assert synth.calls == []


@pytest.mark.asyncio
async def test_a_changed_voice_refuses(synth):
    """Segment 7 in a different voice, in the middle of a finished narration."""
    synth.fail_on.add(1)
    await _narrate(9051)
    synth.fail_on.clear()
    synth.calls.clear()

    _out, err = await handle_postprod({
        "op": "narrate", "text": SCRIPT, "voice": "Achernar",
        "__request_id": 9052, "onlySegments": [1], "manifestRequestId": 9051,
    })
    assert err is not None and err.startswith("narrate_voice_changed")
    assert "Kore" in err
    assert synth.calls == []


@pytest.mark.asyncio
async def test_an_index_the_record_does_not_have_is_refused(synth):
    synth.fail_on.add(1)
    await _narrate(9061)
    synth.fail_on.clear()
    synth.calls.clear()
    _out, err = await _narrate(9062, onlySegments=[99], manifestRequestId=9061)
    assert err is not None and err.startswith("narrate_segment_unknown")
    assert synth.calls == []


@pytest.mark.asyncio
async def test_a_record_written_before_the_voice_field_is_allowed_through(synth):
    """Refusing every older narration would be a wall. The route reports
    `voiceKnown: false` instead, so the user is told rather than blocked."""
    synth.fail_on.add(1)
    await _narrate(9071)
    path = _manifest_path(9071)
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("voice", None)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    synth.fail_on.clear()
    synth.calls.clear()
    _out, err = await _narrate(9072, onlySegments=[1], manifestRequestId=9071)
    assert err is None, err
    assert len(synth.calls) == 1


@pytest.mark.asyncio
async def test_a_partial_read_never_takes_the_one_shot_path(synth):
    """A short script normally skips the manifest entirely. Asked for a partial
    read it must refuse, not narrate the whole thing as one request."""
    _out, err = await handle_postprod({
        "op": "narrate", "text": "một câu ngắn.", "voice": "Kore",
        "__request_id": 9081, "onlySegments": [0], "manifestRequestId": 9081,
    })
    assert err == "narrate_manifest_missing"
    assert synth.calls == []


@pytest.mark.asyncio
async def test_an_empty_segment_list_is_an_ordinary_full_narration(synth):
    """"Re-read nothing" is not a request anyone makes on purpose, and treating it
    as one would merge whatever the record happened to hold."""
    _out, err = await _narrate(9091, onlySegments=[])
    assert err is None, err
    assert len(synth.calls) == len(narration.plan(SCRIPT).segments)


# ── the manifest field ────────────────────────────────────────────────


def test_the_record_round_trips_the_voice_and_engine(tmp_path):
    manifest = narration.plan(SCRIPT)
    manifest.voice = "Kore"
    manifest.engine = "gemini"
    path = manifest.write(tmp_path / "m.json")
    back = narration.Manifest.read(path)
    assert back.voice == "Kore"
    assert back.engine == "gemini"


def test_an_older_record_reads_the_voice_as_unknown_not_as_a_default(tmp_path):
    """"Read by Kore" and "nobody wrote it down" must not be the same answer when
    the next step is deciding whether a re-read changes voices mid-narration."""
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"segments": [], "pauseSeconds": 0.4}), encoding="utf-8")
    assert narration.Manifest.read(path).voice is None
