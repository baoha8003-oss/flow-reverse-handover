"""The shared Gemini key pool.

The packaged tool ships a list of keys and rotates through them because
Gemini's free tier rate-limits per key. This build previously read only the
first line, so a user with eight keys got the quota of one — and the LLM layer
could not see the keys at all.
"""
from __future__ import annotations

import pytest

from flowboard.services import gemini_keys


@pytest.fixture(autouse=True)
def clean_pool(monkeypatch):
    """Every test starts from a known, isolated pool.

    conftest disables the packaged key file suite-wide; these tests point the
    override at their own file when they want to exercise it.
    """
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("FLOWBOARD_GEMINI_KEY_FILE", "")
    monkeypatch.setattr(gemini_keys, "_from_secrets", lambda: [])
    gemini_keys.reset_cooldowns()
    yield
    gemini_keys.reset_cooldowns()


def _key_file(tmp_path, monkeypatch, text: str):
    p = tmp_path / "keys.txt"
    p.write_text(text, encoding="utf-8")
    monkeypatch.setenv("FLOWBOARD_GEMINI_KEY_FILE", str(p))
    return p


# ── loading ──────────────────────────────────────────────────────────────

def test_reads_every_line_not_just_the_first(tmp_path, monkeypatch):
    """The whole point: eight keys must mean eight keys."""
    _key_file(tmp_path, monkeypatch, "k1\nk2\nk3\n")
    assert gemini_keys.all_keys() == ["k1", "k2", "k3"]
    assert gemini_keys.count() == 3


def test_blank_lines_and_whitespace_are_ignored(tmp_path, monkeypatch):
    _key_file(tmp_path, monkeypatch, "\n  k1  \n\n\tk2\n   \n")
    assert gemini_keys.all_keys() == ["k1", "k2"]


def test_a_bom_does_not_corrupt_the_first_key(tmp_path, monkeypatch):
    """The file is typed by hand on Windows, so it arrives BOM-first."""
    p = tmp_path / "keys.txt"
    p.write_text("k1\nk2\n", encoding="utf-8-sig")
    monkeypatch.setenv("FLOWBOARD_GEMINI_KEY_FILE", str(p))
    assert gemini_keys.all_keys() == ["k1", "k2"]


def test_malformed_keys_are_dropped(tmp_path, monkeypatch):
    """A key rides in an HTTP header. Handing httpx a key with a space makes
    it raise with the raw value inside its own message, which then lands in
    the log — so these never leave this module."""
    _key_file(tmp_path, monkeypatch, "good1\nhas space\ngood2\n")
    assert gemini_keys.all_keys() == ["good1", "good2"]


def test_env_wins_over_the_file(tmp_path, monkeypatch):
    _key_file(tmp_path, monkeypatch, "from-file\n")
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    assert gemini_keys.all_keys()[0] == "from-env"
    assert "from-file" in gemini_keys.all_keys()


def test_duplicates_collapse(tmp_path, monkeypatch):
    """The same key from two sources is still one key's worth of quota."""
    _key_file(tmp_path, monkeypatch, "dup\nother\n")
    monkeypatch.setenv("GEMINI_API_KEY", "dup")
    assert gemini_keys.all_keys() == ["dup", "other"]


def test_missing_file_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWBOARD_GEMINI_KEY_FILE", str(tmp_path / "nope.txt"))
    assert gemini_keys.all_keys() == []
    assert gemini_keys.available() is False


def test_empty_override_disables_the_file(tmp_path, monkeypatch):
    """How the test suite keeps its hands off the developer's real keys."""
    real = _key_file(tmp_path, monkeypatch, "k1\n")
    assert gemini_keys.count() == 1
    monkeypatch.setenv("FLOWBOARD_GEMINI_KEY_FILE", "")
    assert gemini_keys.count() == 0
    assert real.exists()  # still there, just not consulted


# ── rotation ─────────────────────────────────────────────────────────────

def test_next_key_rotates_instead_of_always_returning_the_first(
    tmp_path, monkeypatch
):
    """Always handing back key #1 until it dies wastes the pool's purpose."""
    _key_file(tmp_path, monkeypatch, "k1\nk2\nk3\n")
    got = [gemini_keys.next_key() for _ in range(6)]
    assert got == ["k1", "k2", "k3", "k1", "k2", "k3"]


def test_an_exhausted_key_is_skipped(tmp_path, monkeypatch):
    _key_file(tmp_path, monkeypatch, "k1\nk2\n")
    gemini_keys.mark_exhausted("k1")
    assert [gemini_keys.next_key() for _ in range(4)] == ["k2", "k2", "k2", "k2"]


def test_cooldown_expires(tmp_path, monkeypatch):
    _key_file(tmp_path, monkeypatch, "k1\nk2\n")
    gemini_keys.mark_exhausted("k1")
    assert gemini_keys.next_key() == "k2"
    monkeypatch.setattr(gemini_keys, "COOLDOWN_S", 0.0)
    gemini_keys.mark_exhausted("k1")  # re-mark with a zero cooldown
    assert "k1" in {gemini_keys.next_key() for _ in range(4)}


def test_all_exhausted_still_returns_a_key(tmp_path, monkeypatch):
    """When every key is resting, the caller should get the real API error —
    'no key configured' would be a lie, because the user does have keys."""
    _key_file(tmp_path, monkeypatch, "k1\nk2\n")
    gemini_keys.mark_exhausted("k1")
    gemini_keys.mark_exhausted("k2")
    assert gemini_keys.next_key() in {"k1", "k2"}


def test_next_key_is_none_with_no_keys():
    assert gemini_keys.next_key() is None


# ── redaction ────────────────────────────────────────────────────────────

def test_redact_removes_keys_from_a_message():
    msg = "failed talking to host with AIzaSECRETVALUE123"
    assert "AIzaSECRETVALUE123" not in gemini_keys.redact(msg, "AIzaSECRETVALUE123")


def test_redact_ignores_short_or_missing_values():
    """A short string would match half the message; leave it alone."""
    assert gemini_keys.redact("abc", "x") == "abc"
    assert gemini_keys.redact("abc", None) == "abc"
