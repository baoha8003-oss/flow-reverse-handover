"""What a tab remembers between sessions.

The packaged tool keeps one state file per tab — `affiliate_state.json` with 25
keys, `data_clone.json` with 13 — and they are **global**, not per project: the
files carry a `project_index` / `last_project` INSIDE them rather than being
stored per project. So this is one row per tab, which is also the smaller thing
to build.

Free-form on purpose. A tab's state is the shape of its own form, and an
allowlist here would mean editing this module every time a field is added —
which is how `settings_store`'s allowlist earns its keep for settings that
CHANGE BEHAVIOUR, and how it would only get in the way for a text box someone
typed in. What is enforced instead is size: a tab may not turn the disk into
its scratch space.

Not for anything secret. This is typed-in form state — a URL, a note, a
selected ratio — and it is written to plain JSON under `storage/`.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from flowboard.config import STORAGE_DIR

logger = logging.getLogger(__name__)

#: Serialised size ceiling for one tab. The packaged files are a few KB; this
#: is room to grow and still refuses a tab that tries to store media in it.
MAX_BYTES = 64 * 1024

#: Keys a tab may keep. Enough for the packaged tool's 25, with headroom.
MAX_KEYS = 64

_TAB_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")


class TabStateError(ValueError):
    pass


def _dir() -> Path:
    return STORAGE_DIR / "tab-state"


def _path(tab: str) -> Path:
    if not _TAB_RE.match(tab or ""):
        # The name arrives in a URL and becomes a filename; anything outside
        # this shape is either a typo or an attempt to write elsewhere.
        raise TabStateError(f"tên tab không hợp lệ: {tab!r}")
    return _dir() / f"{tab}.json"


def load(tab: str) -> dict[str, Any]:
    """This tab's state, or an empty dict. Never raises for a missing file."""
    path = _path(tab)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        # A corrupt file costs the tab its memory, not its function.
        logger.warning("tab_state: %s unreadable (%s)", path, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def save(tab: str, state: dict[str, Any]) -> dict[str, Any]:
    """Replace this tab's state. Returns what was stored.

    Replace rather than merge: a form that cleared a field would otherwise
    keep the old value forever, and "why is this box still filled in" is a
    worse bug than a lost keystroke.
    """
    if not isinstance(state, dict):
        raise TabStateError("state phải là một object")
    if len(state) > MAX_KEYS:
        raise TabStateError(f"quá {MAX_KEYS} khoá cho một tab")
    payload = json.dumps(state, ensure_ascii=False)
    if len(payload.encode("utf-8")) > MAX_BYTES:
        raise TabStateError(
            f"state quá lớn ({len(payload.encode('utf-8'))} B > {MAX_BYTES} B) — "
            f"tab chỉ nên nhớ những ô người dùng gõ, không nhớ dữ liệu"
        )

    path = _path(tab)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)
    return state


def clear(tab: str) -> bool:
    path = _path(tab)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:  # pragma: no cover — depends on the host
        logger.warning("tab_state: cannot clear %s (%s)", path, exc)
        return False


def tabs() -> list[str]:
    """Every tab that has saved something."""
    directory = _dir()
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.json"))
