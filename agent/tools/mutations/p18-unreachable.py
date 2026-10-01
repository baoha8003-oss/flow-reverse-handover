"""Mutation run for P18: prose that lied, and a clip whose provenance was lost.

Two groups, and they are the same defect class one layer apart.

* **The backfill and the provenance rule.** `_settle_generation_node` only started
  recording `operationNames` / `sourceModelKey` at P16, so every earlier clip had
  a node that knew what it produced and not what produced it. The consequence was
  not cosmetic: a blank model key does not start with `abra_`, so an Omni clip
  read as "not Omni", the extend button was offered, and Flow accepts that submit
  and then fails it — after billing. The backfill makes the data true; the
  unknown-source refusal covers whatever the backfill cannot reach.
* **Prose that asserted what the code contradicts.** Six places justified a
  refusal with "the balance cannot be read" — true between the transport
  migration and P16.1, false since — and two of those went further and stated a
  price nobody measured. The behaviour was right everywhere; only the reasons
  were stale. These mutations pin the corrected claims so they cannot drift back.

Run from `agent/`: .venv/Scripts/python.exe tools/mutations/p18-unreachable.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Labels quote Vietnamese error copy, and a Windows console defaults to cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OPS = "flowboard/services/node_ops.py"
PROC = "flowboard/worker/processor.py"
SESSION = "flowboard/db/session.py"

T_BACKFILL = ["tests/test_backfill_render_ids.py"]
T_CLIP = ["tests/test_clip_ops_handlers.py"]

# (label, file, old, new, tests)
MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    # ── the money hole: provenance ─────────────────────────────────────
    (
        "extend accepts a clip whose source was never recorded",
        PROC,
        '    if not source_model_key.strip():',
        "    if False:",
        T_CLIP,
    ),
    (
        "the unknown-source refusal stops saying what to do about it",
        PROC,
        '            "extend_unknown_source: không có bản ghi clip này từ model nào — "\n'
        '            "chạy lại clip để có bản ghi, hoặc nối trong Flow"',
        '            "extend_unknown_source"',
        T_CLIP,
    ),
    (
        "a refused extend still spends a balance read",
        PROC,
        "    if not source_model_key.strip():\n"
        "        return {}, (",
        "    if not source_model_key.strip() and await _price_probe_start(get_flow_sdk()):\n"
        "        return {}, (",
        T_CLIP,
    ),
    (
        "upscale starts demanding provenance it does not need",
        PROC,
        '    aspect = params.get("aspect_ratio") or "VIDEO_ASPECT_RATIO_LANDSCAPE"\n'
        "\n"
        "    sdk = get_flow_sdk()\n"
        "    before = await _price_probe_start(sdk)",
        '    aspect = params.get("aspect_ratio") or "VIDEO_ASPECT_RATIO_LANDSCAPE"\n'
        '    if not str(params.get("source_model_key") or "").strip():\n'
        '        return {}, "missing_upscale_source"\n'
        "\n"
        "    sdk = get_flow_sdk()\n"
        "    before = await _price_probe_start(sdk)",
        T_CLIP,
    ),
    # ── the backfill ───────────────────────────────────────────────────
    (
        "the backfill stops carrying the model key, so Omni reads as not-Omni",
        OPS,
        '        if not data.get("sourceModelKey") and isinstance(key, str) and key:\n'
        '            patch["sourceModelKey"] = key',
        "        if False:\n"
        '            patch["sourceModelKey"] = key',
        T_BACKFILL,
    ),
    (
        "the backfill stops carrying the operation id, so upscale stays dark",
        OPS,
        '            patch["operationNames"] = ops',
        "            pass",
        T_BACKFILL,
    ),
    # The early `continue` on an already-filled node turned out to be a FAST PATH,
    # not the protection: removing it leaves the two per-field guards below, `patch`
    # comes out empty, and nothing is written. Equivalent mutation, recorded rather
    # than dressed up with a test for `scanned`. What actually protects a node's own
    # ids is the `not data.get(...)` clause on each field, so that is the anchor.
    (
        "the backfill overwrites the operation id a node already had",
        OPS,
        '            not data.get("operationNames")',
        "            True",
        T_BACKFILL,
    ),
    (
        "the backfill overwrites the model key a node already had",
        OPS,
        '        if not data.get("sourceModelKey") and isinstance(key, str) and key:',
        "        if isinstance(key, str) and key:",
        T_BACKFILL,
    ),
    (
        "an unfinished render is treated as a source of truth",
        OPS,
        '        .where(Request.status == "done")',
        '        .where(Request.status != "__never__")',
        T_BACKFILL,
    ),
    (
        "the oldest render wins instead of the newest",
        OPS,
        "        .order_by(Request.id)",
        "        .order_by(Request.id.desc())  # type: ignore[attr-defined]",
        T_BACKFILL,
    ),
    (
        "a list of nothing but nulls is recorded as an operation id",
        OPS,
        "            and any(isinstance(o, str) and o for o in ops)",
        "            and True",
        T_BACKFILL,
    ),
    (
        "an image render is mined for a video operation id",
        OPS,
        '    kinds = ("gen_video", "gen_video_text", "gen_video_omni", "poll_video")',
        '    kinds = ("gen_video", "gen_video_text", "gen_video_omni", "poll_video",\n'
        '             "gen_image")',
        T_BACKFILL,
    ),
    (
        "a recovered clip stops counting as a render",
        OPS,
        '"gen_video_omni", "poll_video")',
        '"gen_video_omni")',
        T_BACKFILL,
    ),
    (
        "the backfill commits on its own, breaking the module's contract",
        OPS,
        "        s.add(node)\n"
        '        counts["patched"] += 1\n'
        "    return counts",
        "        s.add(node)\n"
        "        s.commit()\n"
        '        counts["patched"] += 1\n'
        "    return counts",
        T_BACKFILL,
    ),
    # Dropped as equivalent: `if isinstance(row.node_id, int)` and the
    # `if node is None: continue` below cover the same state from two directions,
    # and SQLAlchemy's `session.get(Model, None)` already answers None rather than
    # raising. Breaking either alone changes nothing observable, so a test for it
    # would be a test of SQLAlchemy. The behaviour itself is still pinned by
    # `test_a_detached_row_is_skipped_rather_than_crashing`.
    (
        "start-up stops running the backfill at all",
        SESSION,
        "            counts = backfill_operation_ids(s)",
        "            counts = {'scanned': 0, 'patched': 0}",
        # A dedicated test asserts the call exists; see the runner note below.
        ["tests/test_backfill_is_wired_into_startup.py"],
    ),
]


def run(tests: list[str]) -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:randomly", *tests],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0


def main() -> int:
    only = sys.argv[1:] or None
    survived: list[str] = []
    for index, (label, path, old, new, tests) in enumerate(MUTATIONS, 1):
        if only and str(index) not in only:
            continue
        file = Path(path)
        raw = file.read_bytes()
        # The repo carries both line endings; rewriting a whole file's endings
        # would turn a one-line mutation into a whole-file diff.
        newline = b"\r\n" if raw.count(b"\r\n") else b"\n"
        target = old.encode("utf-8").replace(b"\n", newline)
        replacement = new.encode("utf-8").replace(b"\n", newline)
        if raw.count(target) != 1:
            print(f"{index:2d}. ANCHOR MISSED ({raw.count(target)}x): {label}")
            survived.append(f"{index}. {label} (anchor)")
            continue
        file.write_bytes(raw.replace(target, replacement))
        try:
            passed = run(tests)
        finally:
            file.write_bytes(raw)
        if passed:
            print(f"{index:2d}. SURVIVED: {label}")
            survived.append(f"{index}. {label}")
        else:
            print(f"{index:2d}. caught:   {label}")

    total = len(only) if only else len(MUTATIONS)
    print(f"\n{total - len(survived)}/{total} caught")
    for s in survived:
        print("  survived:", s)
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
