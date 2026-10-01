"""Settings that claim to control behaviour must actually control it.

``MULTI_VIDEO`` and ``RETRY_WITH_ERROR`` ship in the packaged tool's
config.json and are served through ``/api/settings``. Both used to be stored,
served, and then ignored: the worker read its slot count from an env var and
the retry budget from a hardcoded model default, so changing either in the UI
did nothing at all. These tests fail if either regresses to decoration.
"""
from __future__ import annotations

from flowboard.routes.requests import DEFAULT_MAX_ATTEMPTS, _retry_budget
from flowboard.worker.processor import _configured_concurrency


def test_multi_video_drives_worker_concurrency(client, monkeypatch):
    """Without an explicit env pin, the setting decides the slot count."""
    monkeypatch.delenv("FLOWBOARD_MAX_CONCURRENT", raising=False)

    client.put("/api/settings", json={"values": {"MULTI_VIDEO": 6}})
    assert _configured_concurrency() == 6

    # Teeth: a different value must produce a different answer, or this test
    # would still pass against a hardcoded constant.
    client.put("/api/settings", json={"values": {"MULTI_VIDEO": 3}})
    assert _configured_concurrency() == 3


def test_explicit_env_pin_beats_the_setting(client, monkeypatch):
    """conftest pins concurrency to 1 so the suite stays deterministic. That
    pin has to outrank the setting — otherwise every test using a bare
    WorkerController would quietly start running in parallel."""
    monkeypatch.setenv("FLOWBOARD_MAX_CONCURRENT", "1")
    client.put("/api/settings", json={"values": {"MULTI_VIDEO": 8}})
    assert _configured_concurrency() == 1


def test_unusable_multi_video_falls_back(monkeypatch):
    """A missing or wrong-typed setting must not take the worker down to
    zero slots (or crash it) — it falls back to the configured default."""
    from flowboard.config import MAX_CONCURRENT
    from flowboard.services import settings_store

    monkeypatch.delenv("FLOWBOARD_MAX_CONCURRENT", raising=False)
    for bogus in (None, "four", 0, -1, True):
        monkeypatch.setattr(settings_store, "get", lambda _k, v=bogus: v)
        assert _configured_concurrency() == MAX_CONCURRENT


def test_retry_with_error_sets_max_attempts(client):
    """The retry budget on a new request comes from the setting."""
    client.put("/api/settings", json={"values": {"RETRY_WITH_ERROR": 7}})
    row = client.post(
        "/api/requests", json={"type": "proxy", "params": {"marker": "x"}}
    ).json()
    assert row["max_attempts"] == 7

    client.put("/api/settings", json={"values": {"RETRY_WITH_ERROR": 1}})
    row = client.post(
        "/api/requests", json={"type": "proxy", "params": {"marker": "y"}}
    ).json()
    assert row["max_attempts"] == 1


def test_retry_budget_falls_back_when_unusable(monkeypatch):
    """Settings must never be able to block request creation."""
    from flowboard.services import settings_store

    for bogus in (None, "three", -2, True):
        monkeypatch.setattr(settings_store, "get", lambda _k, v=bogus: v)
        assert _retry_budget() == DEFAULT_MAX_ATTEMPTS

    def _boom(_k):
        raise RuntimeError("settings table missing")

    monkeypatch.setattr(settings_store, "get", _boom)
    assert _retry_budget() == DEFAULT_MAX_ATTEMPTS


def test_default_matches_the_packaged_tool():
    """config.json ships RETRY_WITH_ERROR: 3; the model default must agree so
    the fallback path and the wired path produce the same behaviour."""
    assert DEFAULT_MAX_ATTEMPTS == 3
