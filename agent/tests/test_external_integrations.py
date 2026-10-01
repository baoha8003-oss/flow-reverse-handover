"""Video Clone, Affiliate and Grok.

All three take a URL or a credential from the caller, so what is covered here
is the part that would be dangerous to get wrong: refusing to fetch from the
local network, and never letting a stored key back out.
"""
from __future__ import annotations

import pytest

from flowboard.services import video_clone
from flowboard.services.llm import secrets


# ── SSRF: both URL-fetching features ─────────────────────────────────────

LOCAL_TARGETS = [
    "http://127.0.0.1:8101/api/health",
    "http://localhost/admin",
    "http://192.168.0.1/",
    "http://10.0.0.1/",
    "http://169.254.169.254/latest/meta-data/",  # cloud metadata
]


@pytest.mark.parametrize("url", LOCAL_TARGETS)
def test_video_clone_refuses_local_targets(url):
    """These endpoints fetch a caller-supplied URL from inside the user's
    network, which is the shape of an SSRF primitive."""
    with pytest.raises(video_clone.VideoCloneError):
        video_clone.validate_url(url)


@pytest.mark.parametrize("url", ["ftp://example.com/v.mp4", "file:///etc/passwd", ""])
def test_video_clone_refuses_non_http_schemes(url):
    with pytest.raises(video_clone.VideoCloneError):
        video_clone.validate_url(url)


def test_video_clone_accepts_a_public_url():
    assert video_clone.validate_url("https://example.com/watch?v=1").startswith("https://")


@pytest.mark.parametrize("url", LOCAL_TARGETS)
def test_affiliate_refuses_local_targets(client, url):
    r = client.post("/api/affiliate/product", json={"url": url})
    assert r.status_code == 400
    assert "public internet" in r.json()["detail"]


def test_affiliate_refuses_non_http_schemes(client):
    r = client.post("/api/affiliate/product", json={"url": "file:///etc/passwd"})
    assert r.status_code == 400


def test_affiliate_bounds_the_url_length(client):
    r = client.post("/api/affiliate/product", json={"url": "https://x.com/" + "a" * 3000})
    assert r.status_code == 422


# ── Grok ─────────────────────────────────────────────────────────────────
#
# Grok runs on the user's own grok.com session through the extension's page
# bridge — no API key. (An earlier version of this file asserted an api.x.ai
# key contract; mining the packaged tool showed it never touches that API.)


@pytest.fixture
def offline_bridge(monkeypatch):
    """No extension attached.

    `flow_client` is a module singleton and another test may have left a fake
    socket on it, which would otherwise let these calls sit waiting for a
    reply that never comes.
    """
    from flowboard.services.flow_client import flow_client

    monkeypatch.setattr(flow_client, "_ws", None, raising=False)
    return flow_client


def test_grok_status_reports_the_bridge(client, offline_bridge):
    body = client.get("/api/grok/status").json()
    assert body["bridgeReady"] is False
    assert isinstance(body["note"], str) and body["note"]


def test_grok_stores_no_api_key(client, offline_bridge):
    """The old key endpoint is gone; nothing here should write a credential."""
    # 404 (path removed entirely) or 405 (path kept, verb dropped) — either
    # means no credential was stored.
    assert client.put("/api/grok/key", json={"apiKey": "x"}).status_code in (404, 405)
    assert secrets.get_api_key("xai") is None


def test_grok_probe_fails_fast_when_no_bridge(client, offline_bridge):
    """A probe must answer quickly. It used to inherit the 5-minute
    generation timeout, which made a connectivity check block for 300s."""
    import time

    started = time.monotonic()
    r = client.post("/api/grok/probe")
    assert time.monotonic() - started < 30
    assert r.status_code in (409, 502)


def test_grok_generate_requires_a_prompt(client, offline_bridge):
    assert client.post("/api/grok/generate", json={"prompt": ""}).status_code == 422


def test_grok_generate_rejects_an_unknown_reference(client, offline_bridge):
    """A media id with no cached file cannot be uploaded, and saying so beats
    a failure from inside the bridge."""
    r = client.post(
        "/api/grok/generate",
        json={"prompt": "a cat", "ref_media_ids": ["0000-not-cached"]},
    )
    assert r.status_code == 400
    assert "cache" in r.json()["detail"].lower()


def test_grok_bounds_the_reference_count(client, offline_bridge):
    r = client.post(
        "/api/grok/generate",
        json={"prompt": "a cat", "ref_media_ids": [f"id{i}" for i in range(9)]},
    )
    assert r.status_code == 422
