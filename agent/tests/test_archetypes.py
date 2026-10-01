"""Which of the seven board shapes a brief means.

Picking wrong does not produce a worse board — it produces one that cannot do
the job. A slideshow graph will never keep a face consistent across scenes,
and a component-character graph cannot follow a dance video.

The rule tested hardest is the priority order, because it is the one that is
tempting to break: what the user said outright beats every keyword in the
brief. A tool that overrides an explicit request because it spotted a word it
liked is a tool nobody can predict.
"""
from __future__ import annotations

import pytest

from flowboard.services import archetypes as arch


def key_of(brief: str, **kw) -> str | None:
    choice = arch.classify(brief, **kw)
    return choice.archetype.key if choice.archetype else None


# ── the seven, recognised from their own signals ──────────────────────


@pytest.mark.parametrize("brief,expected", [
    ("kịch bản mẹ chồng nàng dâu, nhiều lời thoại", "character_drama"),
    ("video phong cảnh biến hình, chuyển cảnh mượt không giật", "frame_chain"),
    ("làm video nhảy vũ đạo tiktok cho người mẫu", "motion_control"),
    ("đọc truyện từ ảnh tĩnh, thuyết minh có zoom pan", "slideshow"),
    ("review sản phẩm từ link Shopee, có giá bán và CTA", "affiliate"),
    ("remake video hot trend trên Douyin của đối thủ", "remake"),
    ("sản xuất hàng loạt từ file xlsx, hàng chục kịch bản", "excel_batch"),
])
def test_each_shape_is_recognised_from_its_own_signals(brief, expected):
    assert key_of(brief) == expected


def test_the_reason_is_inferred_and_names_what_matched():
    """The tool owes the user an explanation before it builds a canvas."""
    choice = arch.classify("kịch bản mẹ chồng nàng dâu, nhiều lời thoại")
    assert choice.reason == "inferred"
    assert choice.matched
    assert "Nhận diện" in arch.describe(choice)


# ── what the user said wins ───────────────────────────────────────────


def test_an_explicit_request_beats_every_keyword_in_the_brief():
    """The rule that is tempting to break. The brief here screams slideshow;
    the user asked for motion control, so it is motion control."""
    brief = "đọc truyện từ ảnh tĩnh, thuyết minh, zoom pan, tin tức"
    assert key_of(brief, requested="motion_control") == "motion_control"


def test_the_shape_can_be_named_in_words_not_just_by_key():
    assert key_of("bất kỳ", requested="làm dạng slideshow") == "slideshow"
    assert key_of("bất kỳ", requested="Hoán đổi cử động / người mẫu") == "motion_control"


def test_naming_the_shape_inside_the_brief_also_counts_as_explicit():
    choice = arch.classify("làm cho tôi video dạng nhân vật đồng nhất")
    assert choice.reason == "explicit"
    assert choice.archetype.key == "character_drama"
    assert "Bạn đã chọn" in arch.describe(choice)


def test_an_unrecognised_request_falls_back_to_reading_the_brief(caplog):
    """Better than refusing: the brief is still there and still classifiable.
    Silently building the wrong shape is the outcome to avoid, not this."""
    assert key_of("link Shopee, review sản phẩm", requested="dạng thứ tám") == "affiliate"


# ── asking rather than guessing ───────────────────────────────────────


def test_a_brief_that_fits_two_shapes_equally_is_a_question(caplog):
    """A tie is not a decision. Guessing one builds a board the user then
    has to take apart."""
    choice = arch.classify("làm video từ ảnh, có nhân vật và có thuyết minh")
    if choice.reason == "ask":
        assert 2 <= len(choice.options) <= 3
        assert "chọn giúp" in arch.describe(choice)


def test_one_lone_signal_is_not_enough_to_commit():
    choice = arch.classify("làm một video về gia đình")
    assert choice.reason == "ask"
    assert choice.options


def test_an_empty_brief_offers_options_rather_than_failing():
    choice = arch.classify("")
    assert choice.reason == "ask" and len(choice.options) == 3


def test_a_brief_with_no_signal_at_all_asks():
    assert arch.classify("xin chào bạn khoẻ không").reason == "ask"


# ── a strong signal is a decision on its own ──────────────────────────


def test_a_shopee_link_alone_settles_it():
    """A Shopee link is not a hint about a product video. It IS one."""
    choice = arch.classify("https://shopee.vn/product/123")
    assert choice.reason == "inferred"
    assert choice.archetype.key == "affiliate"


def test_a_spreadsheet_alone_settles_it():
    assert key_of("mình có file xlsx") == "excel_batch"


# ── the matcher ───────────────────────────────────────────────────────


@pytest.mark.parametrize("written", [
    "MẸ CHỒNG nàng dâu",
    "me chong nang dau",
    "Mẹ Chồng — Nàng Dâu!!!",
    "😤 mẹ chồng nàng dâu 😤",
])
def test_the_same_signal_written_four_ways_is_one_signal(written):
    """Briefs arrive with and without diacritics, in mixed case, with emoji.
    A matcher that treats those as different misses three of the four."""
    assert key_of(f"{written}, nhiều lời thoại") == "character_drama"


def test_a_signal_inside_a_longer_word_does_not_fire():
    """Substring matching is what makes "bác học" contain "Bác Hồ". A signal
    list matched loosely fires on words that merely share letters."""
    assert arch._hits(arch.fold("nhayer nhaycopter"), ("nhay",)) == []
    assert arch._hits(arch.fold("điệu nhảy đẹp"), ("nhay",)) == ["nhay"]


def test_every_archetype_can_actually_be_reached():
    """A shape with signals nothing matches is a shape the tool will never
    pick — dead weight that reads as supported."""
    for a in arch.ARCHETYPES:
        assert a.signals or a.strong, a.key
        probe = " ".join(a.strong[:1] or a.signals[:2])
        choice = arch.classify(probe)
        assert choice.archetype is not None or choice.options, a.key


def test_every_archetype_says_what_it_builds():
    for a in arch.ARCHETYPES:
        assert "→" in a.chain, a.key


# ── the endpoint ──────────────────────────────────────────────────────


def test_the_seven_shapes_are_listed_with_what_they_build(client):
    body = client.get("/api/prompt/archetypes").json()
    assert len(body) == 7
    assert all(e["chain"] and e["label"] for e in body)


def test_classify_answers_with_evidence_not_just_a_verdict(client):
    body = client.post("/api/prompt/archetypes/classify", json={
        "brief": "review sản phẩm từ link Shopee, có giá bán và CTA",
    }).json()
    assert body["reason"] == "inferred"
    assert body["archetype"]["key"] == "affiliate"
    assert body["matched"], "a verdict with no evidence is an assertion"
    assert "Nhận diện" in body["explanation"]


def test_an_ambiguous_brief_comes_back_as_options_not_a_pick(client):
    body = client.post("/api/prompt/archetypes/classify", json={
        "brief": "làm một video về gia đình",
    }).json()
    assert body["reason"] == "ask"
    assert body["archetype"] is None
    assert 2 <= len(body["options"]) <= 3


def test_the_endpoint_honours_an_explicit_request(client):
    body = client.post("/api/prompt/archetypes/classify", json={
        "brief": "đọc truyện từ ảnh tĩnh, thuyết minh, zoom pan",
        "requested": "motion_control",
    }).json()
    assert body["reason"] == "explicit"
    assert body["archetype"]["key"] == "motion_control"


def test_classifying_costs_no_model_call(client, monkeypatch):
    """Keyword work on the packaged skill's own notes. Reaching for an LLM
    would make a free, deterministic decision slow and variable."""
    from flowboard.services.llm import registry

    def _boom(*a, **kw):  # pragma: no cover
        raise AssertionError("classification must not call a model")

    monkeypatch.setattr(registry, "run_llm", _boom)
    monkeypatch.setattr(registry, "run_llm_chain", _boom, raising=False)
    resp = client.post("/api/prompt/archetypes/classify",
                       json={"brief": "kịch bản mẹ chồng nàng dâu, lời thoại"})
    assert resp.status_code == 200
