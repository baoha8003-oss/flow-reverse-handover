"""Which model serves each step, and what is broken.

Dispatch deliberately refuses to substitute providers: `run_llm` fails loudly
when the pinned one cannot serve, so the user always knows which model made
their work. The price of that choice is that a dead provider has no other way
to announce itself — without this view, the first symptom of a dead CLI is a
failed board run half an hour later. That is the gap `/api/llm/health` fills,
and these tests hold it to it.

The Gemini CLI dying on this machine (`IneligibleTierError … migrate to
Antigravity`) is the concrete case behind every assertion here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from flowboard.services.llm import registry, secrets


@pytest.fixture
def tmp_secrets_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "secrets.json"
    monkeypatch.setenv("FLOWBOARD_SECRETS_PATH", str(p))
    return p


class _Fake:
    def __init__(self, name, *, available=True, vision=True, audio=False, auth="oauth"):
        self.name = name
        self.supports_vision = vision
        self.supports_audio = audio
        self._available = available
        self._auth = auth

    async def is_available(self) -> bool:
        return self._available

    async def auth_mode(self) -> str:
        return self._auth

    async def run(self, prompt, **kw) -> str:
        return "ok"


@pytest.fixture
def fakes(monkeypatch):
    providers = {
        "claude": _Fake("claude"),
        "gemini": _Fake("gemini", audio=True),
        "openai": _Fake("openai"),
    }
    monkeypatch.setattr(registry, "_PROVIDERS", providers)
    return providers


def _health(client) -> dict:
    resp = client.get("/api/llm/health")
    assert resp.status_code == 200, resp.text
    return resp.json()


# ── pinned features ───────────────────────────────────────────────────


def test_a_pinned_feature_reports_its_provider(client, tmp_secrets_path, fakes):
    secrets.set_feature_provider("vision", "claude")
    row = next(f for f in _health(client)["features"] if f["feature"] == "vision")
    assert (row["provider"], row["ok"]) == ("claude", True)


def test_a_dead_pinned_provider_is_reported_not_hidden(client, tmp_secrets_path, fakes):
    """The whole point. Dispatch will refuse rather than substitute, so this
    view has to be where the user finds out."""
    fakes["gemini"]._available = False
    secrets.set_feature_provider("planner", "gemini")
    row = next(f for f in _health(client)["features"] if f["feature"] == "planner")
    assert row["ok"] is False
    assert row["reason"] == "provider unavailable"


def test_an_unpinned_feature_says_so(client, tmp_secrets_path, fakes):
    row = next(f for f in _health(client)["features"] if f["feature"] == "vision")
    assert row["ok"] is False and row["reason"] == "not pinned"


def test_a_typo_in_the_pin_is_named_as_such(client, tmp_secrets_path, fakes):
    """A hand-edited secrets.json with `claud3` in it. "unknown provider"
    points at the typo; "unavailable" would send the user to reinstall a CLI
    that was never the problem."""
    secrets.write({"activeProviders": {"planner": "claud3"}})
    row = next(f for f in _health(client)["features"] if f["feature"] == "planner")
    assert row["ok"] is False and row["reason"] == "unknown provider"


# ── internal chains ───────────────────────────────────────────────────


def test_every_internal_feature_reports_who_would_serve_it(client, tmp_secrets_path, fakes):
    internal = {f["feature"]: f for f in _health(client)["internal"]}
    assert set(internal) == set(registry.DEFAULT_CHAINS)
    for feature, row in internal.items():
        assert row["ok"] is True
        assert row["servedBy"] in row["chain"]


def test_a_chain_reports_the_next_provider_when_the_first_is_down(
    client, tmp_secrets_path, fakes
):
    fakes["claude"]._available = False
    row = next(f for f in _health(client)["internal"] if f["feature"] == "revise")
    assert row["servedBy"] == "openai", "revise prefers claude, then openai"
    assert row["ok"] is True


def test_a_chain_with_nothing_alive_is_not_ok(client, tmp_secrets_path, fakes):
    for fake in fakes.values():
        fake._available = False
    for row in _health(client)["internal"]:
        assert row["ok"] is False and row["servedBy"] is None


# ── audio, the capability with one supplier ───────────────────────────


def test_audio_is_reported_separately(client, tmp_secrets_path, fakes):
    """Subtitles and karaoke timing go through the one provider that takes
    audio. Folding that into a general "providers are fine" reading is how
    a user ends up asking why subtitles stopped when everything looks green."""
    body = _health(client)
    assert body["audio"] == {"providers": ["gemini"], "ok": True}


def test_audio_goes_down_with_its_only_supplier(client, tmp_secrets_path, fakes):
    fakes["gemini"]._available = False
    body = _health(client)
    assert body["audio"]["ok"] is False
    # …while text work is still fine, which is exactly the state that makes
    # a single "AI is working" indicator misleading.
    assert any(f["ok"] for f in body["internal"])


def test_image_generation_reads_as_off_not_broken(client, tmp_secrets_path, fakes):
    """Off is the correct resting state: Flow draws every image unless
    someone deliberately switched OpenAI on. Reporting that as a fault would
    train the reader to ignore this panel."""
    body = _health(client)
    assert body["images"]["ok"] is False
    assert body["images"]["sources"] == []
    # The model is still named, because it is what WOULD be used and the
    # answer to "what happens if I turn this on" belongs here.
    assert body["images"]["model"]


def test_image_generation_names_the_order_once_it_is_on(
    client, tmp_secrets_path, fakes, monkeypatch
):
    from flowboard.services import openai_images

    monkeypatch.setattr(openai_images, "api_available", lambda: True)
    monkeypatch.setattr(openai_images, "relay_available", lambda: True)
    body = _health(client)
    assert body["images"]["ok"] is True
    assert body["images"]["sources"] == ["api-key", "relay"]


def test_capabilities_are_reported_per_provider(client, tmp_secrets_path, fakes):
    caps = {p["name"]: p["capabilities"] for p in _health(client)["providers"]}
    assert caps["gemini"]["audio"] is True
    assert caps["claude"]["audio"] is False
    assert all(c["text"] for c in caps.values())


def test_a_probe_that_raises_does_not_break_the_panel(client, tmp_secrets_path, fakes):
    """A provider whose availability check throws must read as down, not
    take the whole health view with it — this endpoint is what the user
    opens *because* something is already wrong."""
    async def _boom() -> bool:
        raise RuntimeError("probe exploded")

    fakes["claude"].is_available = _boom
    body = _health(client)
    claude = next(p for p in body["providers"] if p["name"] == "claude")
    assert claude["available"] is False


# ── installed is not signed in ────────────────────────────────────────


def test_a_signed_out_cli_is_not_reported_as_ok(client, tmp_secrets_path, fakes):
    """`is_available()` probes the binary with `--version`, which answers for
    a CLI nobody has logged into. This panel exists so a dead login is seen
    BEFORE a board run finds it, and it was reporting `ok: true`."""
    from flowboard.services.llm import cli_auth

    fakes["claude"]._auth = cli_auth.NONE
    secrets.set_feature_provider("planner", "claude")
    row = next(f for f in _health(client)["features"] if f["feature"] == "planner")
    assert row["ok"] is False
    assert row["reason"] == "not signed in"


def test_a_signed_out_provider_does_not_serve_an_internal_chain(
    client, tmp_secrets_path, fakes
):
    from flowboard.services.llm import cli_auth

    fakes["claude"]._auth = cli_auth.NONE
    row = next(f for f in _health(client)["internal"] if f["feature"] == "revise")
    assert row["servedBy"] != "claude"
