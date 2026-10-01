"""Mutation run for the two operations that act on a clip already paid for.

Upscale and extend differ from a generation in what a bug costs. A broken
generation costs the generation; these can cost the ORIGINAL, or bill for a
render Flow then refuses. So the mutations are grouped by what each one would
cost if it shipped:

* **The original clip.** An upscale that writes `mediaId` replaces the file the
  user paid for, with no undo. That is the single property this feature turns on.
* **A billed render that cannot succeed.** Extending from a media id instead of
  the previous extension's OPERATION id is accepted by Flow and then fails
  NOT_FOUND. Same for an Omni source, which Flow greys out. Both are paid for.
* **Spending that was never asked for.** The extension defaults to the free lane;
  4K upscale is refused outright because two captures disagree about its tier
  slot and 4K costs 50 credits.
* **A price claimed rather than measured.** Neither of these has a measured
  price. "Could not measure" must never come out as zero, because zero is the
  number that would let a lane be called free.

Run from `agent/`: .venv/Scripts/python.exe tools/mutations/p17-clip-ops.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Labels quote Vietnamese error copy, and a Windows console defaults to cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SDK = "flowboard/services/flow_sdk.py"
PROC = "flowboard/worker/processor.py"
CREDITS = "flowboard/services/flow_credits.py"

T_OPS = ["tests/test_clip_ops_handlers.py"]
T_CREDITS = ["tests/test_flow_credits.py"]

# (label, file, old, new, tests)
MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    # ── the original clip ──────────────────────────────────────────────
    (
        "the upscale result is labelled as a generation, so it lands on mediaId",
        PROC,
        '        result["op_kind"] = "upscale"',
        '        result["op_kind"] = "gen_video"',
        T_OPS,
    ),
    # ── a billed render that cannot succeed ────────────────────────────
    (
        "a later extension starts from the clip instead of the previous operation",
        PROC,
        "    source = previous_operation_id or clone_media_id or media_id",
        "    source = clone_media_id or media_id",
        T_OPS,
    ),
    (
        "the first extension starts from the clip instead of the scene clone",
        PROC,
        "    source = previous_operation_id or clone_media_id or media_id",
        "    source = previous_operation_id or media_id",
        T_OPS,
    ),
    (
        "a second scene is made for a clip that already has one",
        PROC,
        "    if not scene_id:\n"
        "        try:\n"
        "            scene = await sdk.create_scene(project_id, [media_id], aspect=aspect)",
        "    if True:\n"
        "        try:\n"
        "            scene = await sdk.create_scene(project_id, [media_id], aspect=aspect)",
        T_OPS,
    ),
    (
        "a scene that came back without an id is extended into anyway",
        PROC,
        '        if not scene_id:\n            return {}, "extend_scene_returned_no_id"',
        '        if False:\n            return {}, "extend_scene_returned_no_id"',
        T_OPS,
    ),
    (
        "the source model key stops reaching the SDK, so Omni is never refused",
        PROC,
        "        source_model_key=source_model_key, paid_lane=paid_lane,",
        '        source_model_key="", paid_lane=paid_lane,',
        T_OPS,
    ),
    (
        "an Omni clip is only refused AFTER a scene has been written to Flow",
        PROC,
        "    if flow_sdk_module.is_omni_model_key(source_model_key):",
        "    if False:",
        T_OPS,
    ),
    (
        "the early Omni check is case-sensitive, so a stored key slips past it",
        SDK,
        '    return (model_key or "").strip().lower().startswith("abra_")',
        '    return (model_key or "").startswith("abra_")',
        T_OPS,
    ),
    (
        "the early refusal stops naming the clip",
        PROC,
        'f"clip này là “{source_model_key}” (Omni) nên nút nối bị mờ"',
        '"clip này là Omni nên nút nối bị mờ"',
        T_OPS,
    ),
    (
        "the scene is forgotten when the submit fails, so a retry makes another",
        PROC,
        "        return {**dispatch, **scene_out}, str(dispatch[\"error\"])[:200]",
        "        return dict(dispatch), str(dispatch[\"error\"])[:200]",
        T_OPS,
    ),
    (
        "the upscale derives the operation id from the media id",
        PROC,
        "        operation_id.strip(), media_id.strip(), project_id,\n"
        "        aspect=aspect, resolution=resolution.strip(),",
        "        media_id.strip(), media_id.strip(), project_id,\n"
        "        aspect=aspect, resolution=resolution.strip(),",
        T_OPS,
    ),
    (
        "the upscale swaps the operation id and the media id in the payload",
        SDK,
        "                operation_id, media_id, project_id,",
        "                media_id, operation_id, project_id,",
        T_OPS,
    ),
    (
        "an upscale submit with an unreadable id reports success",
        SDK,
        '        if not upscaled_id:',
        "        if False:",
        T_OPS,
    ),
    (
        "a failed upscale claims it started the operation it was given",
        SDK,
        '            return _dispatch_error([], _error_text(exc)[:200])\n'
        "        if not upscaled_id:",
        '            return _dispatch_error([operation_id], _error_text(exc)[:200])\n'
        "        if not upscaled_id:",
        T_OPS,
    ),
    # ── spending that was never asked for ─────────────────────────────
    (
        "any truthy value buys the paid extension lane",
        PROC,
        '    paid_lane = params.get("paid_lane") is True',
        '    paid_lane = bool(params.get("paid_lane"))',
        T_OPS,
    ),
    (
        "4K upscale is dispatched from the handler on a guessed tier (50 credits)",
        PROC,
        '    if not isinstance(resolution, str) or not resolution.strip():\n'
        '        resolution = "1080p"',
        '    resolution = "4k"',
        T_OPS,
    ),
    (
        "the upscale mints a captcha the payload has no slot for",
        SDK,
        "            result = await self._client.batch_rpc(fb.RPC_UPSCALE, freq, None, timeout=120)",
        "            result = await self._client.batch_rpc(\n"
        "                fb.RPC_UPSCALE, freq, fb.CAPTCHA_VIDEO, timeout=120)",
        T_OPS,
    ),
    (
        "an upscale with no operation id dispatches with a blank one",
        SDK,
        '            return _dispatch_error([], "missing_upscale_operation_id")',
        "            pass",
        T_OPS,
    ),
    # ── a price claimed rather than measured ───────────────────────────
    (
        "an unmeasurable run reports zero credits spent",
        CREDITS,
        "    if before is None or after is None:\n        return None",
        "    if before is None or after is None:\n        return 0",
        T_CREDITS + T_OPS,
    ),
    (
        "a stale balance is used as the end of the measurement",
        CREDITS,
        "    after = fresh()\n    if before is None or after is None:",
        "    after, _age = last_known()\n    if before is None or after is None:",
        T_CREDITS,
    ),
    (
        "a balance that went up is reported as a negative price",
        CREDITS,
        "    return delta if delta >= 0 else None",
        "    return delta",
        T_CREDITS,
    ),
    (
        "the price observation stops naming which lane it measured",
        PROC,
        '            result, before, "extend " + ("paid" if paid_lane else "free") + " lane"',
        '            result, before, "extend"',
        T_OPS,
    ),
    (
        "the price observation stops naming which resolution it measured",
        PROC,
        '        _price_probe_end(result, before, f"upscale {resolution.strip()}")',
        '        _price_probe_end(result, before, "upscale")',
        T_OPS,
    ),
    (
        "the observation is presented without its can't-know caveat",
        PROC,
        '        f"{label}: số dư giảm {spent} credit trong lần chạy này — chỉ là giá của "\n'
        '        "thao tác nếu lúc đó không có render nào khác"',
        'f"{label}: giá là {spent} credit"',
        T_OPS,
    ),
    (
        "the balance is re-read even when the meter is already fresh",
        PROC,
        "    known = flow_credits.fresh()\n    if known is not None:\n        return known",
        "    known = None\n    if known is not None:\n        return known",
        T_OPS,
    ),
    (
        "the refreshed balance is read and then discarded",
        PROC,
        "    try:\n        return (await sdk.get_credits()).get(\"credits\")",
        '    try:\n        await sdk.get_credits()\n        return None',
        T_OPS,
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
