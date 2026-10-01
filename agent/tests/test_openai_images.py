"""A second image engine, and the two ways it can quietly cost the user.

Flow drawing every frame is a single point of failure, so OpenAI is added
beside it. The tests that matter most here are not about drawing:

1. **Nothing turns itself on.** One path bills dollars per image, the other
   burns the ChatGPT plan quota the user is spending on code. A credential
   sitting on disk is not consent, so the switches are what decide.
2. **The Codex token never escapes.** It goes into an Authorization header
   and nowhere else — not a log line, not an exception, not a repr.
3. **A refused engine fails loudly.** Falling back to Flow when the user
   asked for OpenAI would spend Flow credits on a node whose owner chose
   otherwise, which is the expensive way to be helpful.
"""
from __future__ import annotations

import json
import logging

import httpx
import pytest

from flowboard.services import openai_images

#: Distinctive enough that finding it anywhere is unambiguous.
FAKE_TOKEN = "sk-codex-SECRETTOKENVALUE-do-not-leak-0123456789"
FAKE_ACCOUNT = "acct-SECRETACCOUNT-9876"

_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x04\x00\x00\x00\x06\x00"  # 1024 x 1536
    b"\x08\x06\x00\x00\x00"
    + b"\x00" * 300
)


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    """A Codex login on disk, with a token we can hunt for afterwards."""
    home = tmp_path / ".codex"
    home.mkdir()
    (home / "auth.json").write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "OPENAI_API_KEY": None,
                "tokens": {
                    "id_token": "id",
                    "access_token": FAKE_TOKEN,
                    "refresh_token": "refresh",
                    "account_id": FAKE_ACCOUNT,
                },
                "last_refresh": "2026-09-04T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


def settings(monkeypatch, **values):
    """Pin the settings this module reads. Anything unnamed is off/absent."""
    from flowboard.services import settings_store

    monkeypatch.setattr(settings_store, "get", lambda key: values.get(key.upper()))


def api_key(monkeypatch, value):
    from flowboard.services.llm import secrets

    monkeypatch.setattr(secrets, "get_api_key", lambda name: value)


class FakeResponse:
    """Enough of `httpx.Response` for the parsers under test."""

    def __init__(self, status_code=200, payload=None, text="{}"):
        self.status_code = status_code
        self._payload = payload
        self._text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


# ── nothing switches itself on ────────────────────────────────────────


def test_both_paths_are_off_out_of_the_box(monkeypatch, codex_home):
    """A key in the provider settings and a Codex login on disk are both
    present here. Neither is consent."""
    settings(monkeypatch)
    api_key(monkeypatch, "sk-real-platform-key")
    assert openai_images.api_available() is False
    assert openai_images.relay_available() is False
    assert openai_images.available() is False


def test_api_path_needs_a_real_key_not_a_chatgpt_login(monkeypatch, codex_home):
    """The ChatGPT OAuth login cannot reach api.openai.com. Users conflate
    the two constantly, so the switch alone must not be enough."""
    settings(monkeypatch, OPENAI_IMAGE_ENABLED=True)
    api_key(monkeypatch, None)
    assert openai_images.api_available() is False
    api_key(monkeypatch, "sk-real-platform-key")
    assert openai_images.api_available() is True


def test_relay_needs_a_codex_login(monkeypatch, tmp_path):
    settings(monkeypatch, OPENAI_IMAGE_RELAY_ENABLED=True)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "nothing-here"))
    assert openai_images.relay_available() is False


def test_a_corrupt_auth_file_is_not_a_login(monkeypatch, tmp_path):
    home = tmp_path / ".codex"
    home.mkdir()
    (home / "auth.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))
    settings(monkeypatch, OPENAI_IMAGE_RELAY_ENABLED=True)
    assert openai_images.relay_available() is False


@pytest.mark.asyncio
async def test_no_path_configured_says_what_to_switch_on(monkeypatch):
    settings(monkeypatch)
    api_key(monkeypatch, None)
    with pytest.raises(openai_images.ImageGenError) as exc:
        await openai_images.generate("một con mèo")
    assert "Cài đặt" in str(exc.value)


# ── the token stays in the header ─────────────────────────────────────


@pytest.mark.asyncio
async def test_the_codex_token_never_reaches_a_log_or_an_error(
    monkeypatch, codex_home, caplog
):
    """The keystone. An auth failure is exactly when a server likes to echo
    what it was sent, and exactly when someone pastes the log into a chat."""
    settings(monkeypatch, OPENAI_IMAGE_RELAY_ENABLED=True)
    api_key(monkeypatch, None)

    async def _post(self, url, **kwargs):
        # The token IS supposed to be here, and nowhere else.
        assert kwargs["headers"]["authorization"] == f"Bearer {FAKE_TOKEN}"
        return httpx.Response(
            401,
            json={"error": {"message": "token expired"}},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(openai_images.ImageGenError) as exc:
            await openai_images.generate("một con mèo")

    assert FAKE_TOKEN not in str(exc.value)
    assert FAKE_ACCOUNT not in str(exc.value)
    assert FAKE_TOKEN not in caplog.text
    assert FAKE_ACCOUNT not in caplog.text
    # The one thing the user can act on has to survive the redaction.
    assert "codex login" in str(exc.value)


def test_the_image_repr_does_not_dump_the_bytes():
    """A megabyte of pixels in a log line is how a log file becomes useless."""
    img = openai_images.GeneratedImage(data=b"\x89PNG" * 1000, mime="image/png", source="relay")
    assert "PNG" not in repr(img)
    assert "4000" in repr(img)


# ── which path runs, and in what order ────────────────────────────────


def test_api_key_leads_by_default(monkeypatch, codex_home):
    """The relay costs no new money but burns the plan quota the user is
    coding with, 3-5x a text turn. That is not a default to pick for them."""
    settings(monkeypatch, OPENAI_IMAGE_ENABLED=True, OPENAI_IMAGE_RELAY_ENABLED=True)
    api_key(monkeypatch, "sk-real")
    assert openai_images.sources() == ["api-key", "relay"]


def test_the_order_can_be_flipped_for_whoever_prefers_quota(monkeypatch, codex_home):
    settings(
        monkeypatch,
        OPENAI_IMAGE_ENABLED=True,
        OPENAI_IMAGE_RELAY_ENABLED=True,
        OPENAI_IMAGE_PREFER_RELAY=True,
    )
    api_key(monkeypatch, "sk-real")
    assert openai_images.sources() == ["relay", "api-key"]


@pytest.mark.asyncio
async def test_a_dead_path_falls_through_to_the_other(monkeypatch, codex_home):
    settings(
        monkeypatch,
        OPENAI_IMAGE_ENABLED=True,
        OPENAI_IMAGE_RELAY_ENABLED=True,
        OPENAI_IMAGE_PREFER_RELAY=True,
    )
    api_key(monkeypatch, "sk-real")
    seen: list[str] = []

    async def _post(self, url, **kwargs):
        seen.append(url)
        if "chatgpt.com" in url:
            return httpx.Response(500, json={}, request=httpx.Request("POST", url))
        import base64

        return httpx.Response(
            200,
            json={"data": [{"b64_json": base64.b64encode(_PNG).decode()}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    out = await openai_images.generate("một con mèo")
    assert out.source == "api-key"
    assert len(seen) == 2, "the relay must actually have been tried first"


@pytest.mark.asyncio
async def test_every_path_failing_names_all_of_them(monkeypatch, codex_home):
    settings(monkeypatch, OPENAI_IMAGE_ENABLED=True, OPENAI_IMAGE_RELAY_ENABLED=True)
    api_key(monkeypatch, "sk-real")

    async def _post(self, url, **kwargs):
        return httpx.Response(500, json={}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    with pytest.raises(openai_images.ImageGenError) as exc:
        await openai_images.generate("một con mèo")
    assert "api-key" in str(exc.value) and "relay" in str(exc.value)


# ── size, model, price ────────────────────────────────────────────────


@pytest.mark.parametrize("aspect,size", [
    ("IMAGE_ASPECT_RATIO_PORTRAIT", "1024x1536"),
    ("IMAGE_ASPECT_RATIO_LANDSCAPE", "1536x1024"),
    ("IMAGE_ASPECT_RATIO_SQUARE", "1024x1024"),
    (None, "1024x1024"),
    ("SOMETHING_ELSE", "1024x1024"),
])
def test_aspect_becomes_an_openai_size(aspect, size):
    assert openai_images.size_for(aspect) == size


def test_the_default_model_is_not_one_that_dies_in_december(monkeypatch):
    """OpenAI's deprecation table shuts down the whole gpt-image-1 family on
    2026-12-01. Defaulting to one of those just schedules the outage."""
    settings(monkeypatch)
    assert openai_images.model_name() == "gpt-image-2"
    assert not openai_images.model_name().startswith("gpt-image-1")


def test_the_model_is_a_setting_so_it_can_outlive_this_code(monkeypatch):
    settings(monkeypatch, OPENAI_IMAGE_MODEL="gpt-image-3")
    assert openai_images.model_name() == "gpt-image-3"


def test_a_nonsense_quality_falls_back_rather_than_being_sent(monkeypatch):
    settings(monkeypatch, OPENAI_IMAGE_QUALITY="ultra")
    assert openai_images.quality() == "low"


def test_a_published_price_and_an_estimated_one_are_distinguishable(monkeypatch):
    """Both are numbers; only one is OpenAI's. A caller that cannot tell
    them apart will quote a third-party guess as a price."""
    settings(monkeypatch, OPENAI_IMAGE_MODEL="gpt-image-1-mini")
    official = openai_images.price_range(size="1024x1024")
    settings(monkeypatch, OPENAI_IMAGE_MODEL="gpt-image-2")
    derived = openai_images.price_range(size="1024x1024")
    assert official is not None and official[2] is True
    assert derived is not None and derived[2] is False


def test_a_tall_image_costs_more_than_a_square_one(monkeypatch):
    settings(monkeypatch, OPENAI_IMAGE_MODEL="gpt-image-1-mini")
    square = openai_images.price_range(size="1024x1024")
    tall = openai_images.price_range(size="1024x1536")
    assert tall[0] > square[0]


def test_an_unknown_model_has_no_price_rather_than_a_made_up_one(monkeypatch):
    settings(monkeypatch, OPENAI_IMAGE_MODEL="something-new")
    assert openai_images.price_range() is None


# ── reading the response ──────────────────────────────────────────────


def test_api_response_yields_the_image():
    import base64

    resp = FakeResponse(payload={"data": [{"b64_json": base64.b64encode(_PNG).decode()}]})
    img = openai_images._image_from_api_body(resp, "api-key")
    assert img.mime == "image/png"
    assert img.data == _PNG


def test_a_url_only_response_names_the_setting_that_fixes_it(monkeypatch):
    """Only the retired dall-e models answered this way. A generic parse
    error would send the reader looking in the wrong place."""
    settings(monkeypatch, OPENAI_IMAGE_MODEL="dall-e-3")
    resp = FakeResponse(payload={"data": [{"url": "https://example.com/a.png"}]})
    with pytest.raises(openai_images.ImageGenError) as exc:
        openai_images._image_from_api_body(resp, "api-key")
    assert "OPENAI_IMAGE_MODEL" in str(exc.value)


def test_relay_response_is_found_however_deeply_it_is_nested():
    """This endpoint has no published schema, so pinning the parser to one
    field path is how it breaks silently after a backend reshuffle."""
    import base64

    b64 = base64.b64encode(_PNG).decode()
    resp = FakeResponse(payload={
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "image_generation_call", "result": b64},
        ]
    })
    assert openai_images._image_from_relay_body(resp, "relay").data == _PNG


def test_a_decoy_string_ahead_of_the_image_does_not_hide_it():
    """`result` and `data` are ordinary field names in this payload, so a
    status string can sit in front of the picture. Stopping at the first
    candidate reports "no image" about a response that contained one — and
    the user is charged either way."""
    import base64

    b64 = base64.b64encode(_PNG).decode()
    resp = FakeResponse(payload={
        "output": [
            {"type": "status", "result": "completed", "data": "ok"},
            {"type": "image_generation_call", "result": b64},
        ]
    })
    assert openai_images._image_from_relay_body(resp, "relay").data == _PNG


def test_a_response_with_no_image_at_all_still_fails():
    resp = FakeResponse(payload={"output": [{"result": "completed", "data": "ok"}]})
    with pytest.raises(openai_images.ImageGenError):
        openai_images._image_from_relay_body(resp, "relay")


def test_trying_every_candidate_is_bounded(monkeypatch):
    """Trying all candidates instead of just the first is what makes the
    parser tolerant — and is also what a payload full of strings could turn
    into thousands of base64 decodes. The ceiling is the difference."""
    calls = {"n": 0}
    real = openai_images._decode

    def _counted(b64, source):
        calls["n"] += 1
        return real(b64, source)

    monkeypatch.setattr(openai_images, "_decode", _counted)
    resp = FakeResponse(payload={"output": [{"result": f"junk-{i}"} for i in range(200)]})
    with pytest.raises(openai_images.ImageGenError):
        openai_images._image_from_relay_body(resp, "relay")
    assert calls["n"] <= openai_images._MAX_IMAGE_CANDIDATES


def test_bytes_that_are_not_an_image_are_rejected():
    import base64

    resp = FakeResponse(payload={"data": [{"b64_json": base64.b64encode(b"x" * 400).decode()}]})
    with pytest.raises(openai_images.ImageGenError) as exc:
        openai_images._image_from_api_body(resp, "api-key")
    assert "ảnh" in str(exc.value)


def test_an_oversized_image_is_refused_before_it_reaches_the_uploader():
    import base64

    huge = _PNG + b"\x00" * (openai_images.MAX_IMAGE_BYTES + 1)
    with pytest.raises(openai_images.ImageGenError) as exc:
        openai_images._decode(base64.b64encode(huge).decode(), "api-key")
    assert "vượt trần" in str(exc.value)


@pytest.mark.asyncio
async def test_an_empty_prompt_costs_nothing_to_reject(monkeypatch, codex_home):
    settings(monkeypatch, OPENAI_IMAGE_RELAY_ENABLED=True)

    async def _post(self, url, **kwargs):  # pragma: no cover — must not run
        raise AssertionError("an empty prompt must not reach the network")

    monkeypatch.setattr(httpx.AsyncClient, "post", _post)
    with pytest.raises(openai_images.ImageGenError):
        await openai_images.generate("   ")
