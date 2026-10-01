"""Settings: the whitelist is the security boundary. These tests pin that
credential/account keys never appear on GET and are refused on PUT, and
that the UPPER/lower duplicate keys in config.json don't drop a value.
"""
from __future__ import annotations

import json

import pytest

from flowboard.services import settings_store


@pytest.fixture
def temp_config(monkeypatch, tmp_path):
    """Point settings_store at a hand-built config.json containing both a
    credential key and a lower-cased duplicate of a whitelisted key."""
    cfg = tmp_path / "config.json"
    cfg.write_text(
        json.dumps(
            {
                "VIDEO_ASPECT_RATIO": "9:16",
                "video_output_dir": "D:/OUT",  # lower-case only — must still be found
                "MULTI_VIDEO": 4,
                "account1": {"cookie": "secret", "access_token": "ya29.secret"},
                "account1_token": "ya29.secret",
                "grok_account": {"type_account": "SUPER"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings_store, "CONFIG_PATH", cfg)
    return cfg


def test_get_never_exposes_account_or_token_keys(client, temp_config):
    r = client.get("/api/settings")
    assert r.status_code == 200, r.text
    body = r.json()
    for leaked in ("account1", "account1_token", "grok_account"):
        assert leaked not in body
    # And no value in the payload smells like a captured token.
    assert "ya29.secret" not in json.dumps(body)


def test_lowercase_config_key_is_not_dropped(client, temp_config):
    """config.json ships `video_output_dir` (lower) with no UPPER twin here;
    canonicalization must still surface it as VIDEO_OUTPUT_DIR."""
    body = client.get("/api/settings").json()
    assert body.get("VIDEO_OUTPUT_DIR") == "D:/OUT"


def test_put_rejects_credential_keys(client, temp_config):
    r = client.put(
        "/api/settings",
        json={"values": {"account1_token": "hacked", "grok_account": {"x": 1}}},
    )
    assert r.status_code == 400
    assert "not settable" in r.json()["detail"]
    # Nothing was written for the rejected keys.
    assert "account1_token" not in settings_store.get_all()


def test_put_accepts_whitelisted_key_and_get_reflects_it(client, temp_config):
    r = client.put("/api/settings", json={"values": {"MULTI_VIDEO": 2}})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == ["MULTI_VIDEO"]
    assert client.get("/api/settings").json()["MULTI_VIDEO"] == 2
    # Reset restores the config.json default.
    client.post("/api/settings/reset", json={"keys": ["MULTI_VIDEO"]})
    assert client.get("/api/settings").json()["MULTI_VIDEO"] == 4


def test_put_case_insensitive_key_writes_canonical(client, temp_config):
    """A lower-case whitelisted key on PUT lands on the canonical UPPER."""
    r = client.put("/api/settings", json={"values": {"multi_video": 3}})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == ["MULTI_VIDEO"]
    assert client.get("/api/settings").json()["MULTI_VIDEO"] == 3


def test_defaults_endpoint_ignores_overrides(client, temp_config):
    client.put("/api/settings", json={"values": {"MULTI_VIDEO": 1}})
    assert client.get("/api/settings/defaults").json()["MULTI_VIDEO"] == 4
    assert client.get("/api/settings").json()["MULTI_VIDEO"] == 1


def test_put_is_all_or_nothing_when_a_key_is_rejected(client, temp_config):
    """A 400 must mean nothing landed. Committing the valid half and THEN
    raising left the client's state disagreeing with the server's — a client
    that reverts on error, or retries, now writes over a value it never saw."""
    assert client.get("/api/settings").json()["MULTI_VIDEO"] == 4

    r = client.put(
        "/api/settings",
        json={"values": {"MULTI_VIDEO": 9, "account1_token": "hacked"}},
    )
    assert r.status_code == 400
    # The valid key in the same payload was NOT applied.
    assert client.get("/api/settings").json()["MULTI_VIDEO"] == 4


def test_put_rejects_out_of_range_and_wrong_typed_values(client, temp_config):
    """Whitelisted key, unusable value: MULTI_VIDEO is a dispatch multiplier
    and the path keys become write targets, so 'any JSON for any whitelisted
    key' is not a safe contract."""
    for bad in ({"MULTI_VIDEO": 9999}, {"MULTI_VIDEO": "four"}, {"AUTO_UPSCALE": "yes"}):
        r = client.put("/api/settings", json={"values": bad})
        assert r.status_code == 400, f"{bad} should be refused: {r.text}"
    # A sane value still goes through.
    assert client.put("/api/settings", json={"values": {"MULTI_VIDEO": 2}}).status_code == 200


def test_config_json_with_a_bom_still_loads(client, monkeypatch, tmp_path):
    """The desktop tool owns config.json and may rewrite it with a BOM.
    Reading it as plain utf-8 raises, the error is swallowed, and EVERY
    setting silently reverts to BASELINE with nothing to explain why."""
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"MULTI_VIDEO": 7}), encoding="utf-8-sig")
    monkeypatch.setattr(settings_store, "CONFIG_PATH", cfg)

    assert client.get("/api/settings").json()["MULTI_VIDEO"] == 7


def test_container_values_are_size_bounded(client, temp_config):
    """PROJECTS is a list, and a list bypassed the string length bound
    entirely — a megabyte of JSON could be parked in a setting."""
    ok = client.put("/api/settings", json={"values": {"PROJECTS": ["a", "b"]}})
    assert ok.status_code == 200, ok.text

    huge = client.put(
        "/api/settings", json={"values": {"PROJECTS": ["x" * 200] * 100}}
    )
    assert huge.status_code == 400
    assert "too large" in huge.json()["detail"]
    # The rejected write left the earlier value intact (all-or-nothing).
    assert client.get("/api/settings").json()["PROJECTS"] == ["a", "b"]
