"""The agent has no authentication, so CORS is the only thing standing
between a page the user happens to visit and an API that spends Flow
credits and writes files. These tests pin that boundary.
"""
from __future__ import annotations

import pytest


def _preflight(client, origin: str):
    return client.options(
        "/api/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
        },
    )


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",  # vite preview
        # Some setups resolve localhost to IPv6; the browser then sends this
        # form and a v4-only regex fails preflight with no obvious cause.
        "http://[::1]:5173",
        "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
    ],
)
def test_local_ui_and_extension_are_allowed(client, origin):
    r = _preflight(client, origin)
    assert r.status_code == 200, r.text
    assert r.headers.get("access-control-allow-origin") == origin


@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://evil.example",
        # Lookalikes that a naive substring/prefix check would wave through.
        "https://localhost.evil.example",
        "http://127.0.0.1.evil.example",
        "https://evil.example/?x=http://localhost:5173",
    ],
)
def test_arbitrary_websites_are_refused(client, origin):
    """A hostile page must not get an allow-origin header back.

    Without this, any site open in the user's browser could POST
    /api/requests and burn their Flow credits, or overwrite renders.
    """
    r = _preflight(client, origin)
    assert r.headers.get("access-control-allow-origin") is None


def test_credentials_are_not_granted_cross_origin(client):
    """No route reads cookies; granting credentials would only widen
    what an allowed-but-hostile context could do with an ambient session."""
    r = _preflight(client, "http://localhost:5173")
    assert r.headers.get("access-control-allow-credentials") is None


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_every_verb_the_app_uses_survives_preflight(client, method):
    """A missing verb in allow_methods breaks that route from the browser
    at the preflight, not the call — PUT (/api/llm/config) is the trap."""
    r = client.options(
        "/api/llm/config",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": method,
        },
    )
    assert r.status_code == 200, r.text
    allowed = (r.headers.get("access-control-allow-methods") or "").upper()
    assert method in allowed, f"{method} missing from {allowed!r}"


def test_callback_secret_header_is_accepted_for_the_extension(client):
    """The bridge delivers Flow responses with X-Callback-Secret; if the
    preflight refuses that header the whole generation path dies."""
    r = client.options(
        "/api/ext/callback",
        headers={
            "Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-callback-secret",
        },
    )
    assert r.status_code == 200, r.text
    allowed = (r.headers.get("access-control-allow-headers") or "").lower()
    assert "x-callback-secret" in allowed
