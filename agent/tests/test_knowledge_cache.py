"""The cache two docstrings promised.

`prompt_checks.check` advertises "no I/O beyond reading the rule file once per
process" and `knowledge`'s module docstring says the index is cached by mtime.
Neither was true — every call re-read the markdown, including the 146 KiB Veo
guide — and the claim mattered because `check()` is meant to run before every
dispatch.

Two properties, and the second is why the key is not just the path: these files
belong to the packaged tool and the user edits them on their own schedule, so a
stale cache would serve last week's prompt rules until the server restarted.
"""
from __future__ import annotations

import pytest

from flowboard.services import knowledge


@pytest.fixture(autouse=True)
def _empty_cache():
    knowledge._CACHE.clear()
    yield
    knowledge._CACHE.clear()


def test_a_file_is_read_once(tmp_path, monkeypatch):
    f = tmp_path / "skill.md"
    f.write_text("## Mục\nnội dung\n", encoding="utf-8")

    reads: list[str] = []
    real_read_text = type(f).read_text

    def _counting(self, *a, **kw):
        reads.append(str(self))
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(type(f), "read_text", _counting)

    assert knowledge._read(f) == "## Mục\nnội dung\n"
    assert knowledge._read(f) == "## Mục\nnội dung\n"
    assert len(reads) == 1, reads


def test_an_edit_is_picked_up_without_a_restart(tmp_path):
    """Keyed on mtime and size, not on the path. These files are the packaged
    tool's and the user updates them independently of this app."""
    f = tmp_path / "skill.md"
    f.write_text("cũ", encoding="utf-8")
    assert knowledge._read(f) == "cũ"

    import os
    stat = f.stat()
    f.write_text("mới hơn", encoding="utf-8")
    os.utime(f, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    assert knowledge._read(f) == "mới hơn"


def test_a_missing_file_is_none_not_an_exception(tmp_path):
    assert knowledge._read(tmp_path / "nope.md") is None


def test_a_file_that_is_not_utf8_is_skipped_not_raised(tmp_path):
    """It used to raise `UnicodeDecodeError` straight through storyboard, idea,
    review and revise — past a function whose contract is "None, never an
    exception"."""
    f = tmp_path / "latin.md"
    f.write_bytes("phở bò".encode("utf-16"))
    assert knowledge._read(f) is None


def test_the_failure_is_cached_too(tmp_path, monkeypatch):
    """Otherwise a missing asset root means a stat call per prompt, forever."""
    f = tmp_path / "latin.md"
    f.write_bytes(b"\xff\xfe\x00bad")
    assert knowledge._read(f) is None
    assert knowledge._read(f) is None
    assert len(knowledge._CACHE) == 1


def test_the_cache_cannot_grow_without_bound(tmp_path):
    for i in range(knowledge._CACHE_MAX_FILES + 5):
        f = tmp_path / f"f{i}.md"
        f.write_text(str(i), encoding="utf-8")
        knowledge._read(f)
    assert len(knowledge._CACHE) <= knowledge._CACHE_MAX_FILES
