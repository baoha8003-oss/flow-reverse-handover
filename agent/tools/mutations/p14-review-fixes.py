"""Mutation run for the 20/09 parallel-review fixes.

Each entry breaks exactly one decision made while closing a finding from
`plans/reports/review-260920-1215-p13-parallel-audit.md`, and names the tests
that must go red. A surviving mutation means the fix is not actually pinned —
which is precisely how the review's findings shipped in the first place: nine of
the guards it examined were phantom, including one whose own fixture was the
failure scenario.

Run from `agent/`: .venv/Scripts/python.exe tools/mutations/p14-review-fixes.py
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
EXEC = "flowboard/services/pipeline_executor.py"
EST = "flowboard/routes/estimate.py"
CLIENT = "flowboard/services/flow_client.py"
BATCH = "flowboard/services/flow_batch.py"
MODELS = "flowboard/routes/models.py"
NODES = "flowboard/services/node_settings.py"

# (label, file, old, new, tests)
MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    (
        "an unset lane on the character port folds onto the Veo default again",
        SDK,
        '    lane = (quality or "").strip().lower() or None\n    if lane and lane != "omni":\n        plan = resolve_r2v_plan(lane)',
        '    lane = (quality or "").strip().lower() or DEFAULT_VIDEO_QUALITY\n    if lane and lane != "omni":\n        plan = resolve_r2v_plan(lane)',
        ["tests/test_board_estimate.py", "tests/test_money_safety.py"],
    ),
    (
        "the worker stops asking the resolver the quote asks",
        PROC,
        "    plan = resolve_character_port_plan(lane, duration_s, resolution)",
        '    plan = {"model_key": omni_model_key("references", duration_s, resolution),\n            "family": "omni", "error": None}',
        ["tests/test_money_safety.py", "tests/test_motion_control.py"],
    ),
    (
        "the estimate re-derives the character lane instead of using the resolver",
        EST,
        "    if has_characters:\n        # Asked first and asked through the resolver",
        "    if False:\n        # Asked first and asked through the resolver",
        ["tests/test_board_estimate.py"],
    ),
    (
        "the price comes from the image-to-video table for every family again",
        EST,
        "    if has_characters:\n        # Priced through the family that will actually run.",
        "    if False:\n        # Priced through the family that will actually run.",
        ["tests/test_board_estimate.py"],
    ),
    (
        "text-to-video is priced from the image-to-video table again",
        EST,
        "    if not has_start_frame:\n        # Text-to-video has its own key table.",
        "    if False:\n        # Text-to-video has its own key table.",
        ["tests/test_node_settings.py"],
    ),
    (
        "a Veo clip stops saying its length control did nothing",
        EST,
        '        f" Làn Veo luôn render 8 giây — {duration}s đã chọn không có tác dụng."',
        '        ""',
        ["tests/test_board_estimate.py"],
    ),
    (
        "the text-to-video variant count loses its ceiling again",
        PROC,
        "    variant_count = clamp_variant_count(params.get(\"variant_count\"))\n\n    sdk = get_flow_sdk()",
        "    variant_count = params.get(\"variant_count\") or 1\n\n    sdk = get_flow_sdk()",
        ["tests/test_t2v.py"],
    ),
    (
        "the quote stops counting the variants text-to-video will submit",
        EST,
        "        if node.type == \"video\" and not has_start_frame_wire(\n            upstream_of.get(node.id, ())\n        ):",
        "        if False:",
        ["tests/test_board_estimate.py"],
    ),
    (
        "the re-poll comparison always answers 'same render'",
        EXEC,
        "        if was != now:\n            return False",
        "        if False:\n            return False",
        ["tests/test_repoll.py"],
    ),
    (
        "a field the old row never stored counts as a change (and bills again)",
        EXEC,
        "        if was is None or now is None:\n            continue",
        "        if False:\n            continue",
        ["tests/test_repoll.py"],
    ),
    (
        "the executor stops telling the re-poll what it would dispatch",
        EXEC,
        "            repoll = _unresolved_video_operations(nid, current=repoll_shape)",
        '            repoll = _unresolved_video_operations(nid, current={"prompt": prompt})',
        ["tests/test_repoll.py"],
    ),
    (
        "the slot map is not carried into the poll",
        EXEC,
        '            "open_slots": open_slots,',
        '            "open_slots": [],',
        ["tests/test_repoll.py"],
    ),
    (
        "the poll result is not rebuilt against the original slots",
        PROC,
        "    if isinstance(slots, list) and isinstance(landed, list) and slots:",
        "    if False:",
        ["tests/test_repoll.py"],
    ),
    (
        "the shared settle writes a null mediaId again",
        EXEC,
        '    media_id = media_ids[0] if media_ids else None\n    patch: dict[str, Any] = {"mediaIds": media_ids}\n    if media_id:',
        '    media_id = media_ids[0] if media_ids else None\n    patch: dict[str, Any] = {"mediaIds": media_ids}\n    if media_ids:',
        ["tests/test_repoll.py"],
    ),
    (
        "media orphaned on a finished row is abandoned, so RUN Lỗi bills again",
        EXEC,
        "            if any(landed) and not _node_already_has(node_id, landed):",
        "            if False:",
        ["tests/test_repoll.py"],
    ),
    (
        "adoption fires even when the node has the media, so a re-run cannot run",
        EXEC,
        "            if any(landed) and not _node_already_has(node_id, landed):",
        "            if any(landed):",
        ["tests/test_repoll.py"],
    ),
    (
        "OpenAI failures reach the Flow account breaker again",
        PROC,
        '_NON_FLOW_REQUEST_TYPES: frozenset[str] = frozenset(\n    {"gen_image_openai", "postprod"}\n)',
        '_NON_FLOW_REQUEST_TYPES: frozenset[str] = frozenset({"gen_image_openai"})',
        ["tests/test_worker_retry.py"],
    ),
    (
        "the HTTP status is dropped before the retry policy sees it",
        CLIENT,
        '        if not result.get("error") and isinstance(status, int) and status >= 400:\n            result = {**result, "error": f"API_{status}"}',
        '        if False:\n            result = {**result, "error": f"API_{status}"}',
        ["tests/test_flow_client.py"],
    ),
    (
        "the image path builds its own error string again, cutting Flow's code",
        SDK,
        '                + "; ".join(_error_text(e)[:80] for e in failures.values())',
        '                + "; ".join(str(e)[:80] for e in failures.values())',
        ["tests/test_flow_sdk.py"],
    ),
    (
        "the reference builder ignores the key it is handed again",
        BATCH,
        "    if model:\n        model = str(model)\n    else:",
        "    if False:\n        model = str(model)\n    else:",
        ["tests/test_character_entities.py"],
    ),
    (
        "the key the caller resolved stops reaching the builder",
        SDK,
        "                        model=key,",
        "                        # model=key,",
        ["tests/test_character_entities.py"],
    ),
    (
        "Veo's measured reference key is refused again",
        SDK,
        "            and model_key not in set(VEO_R2V_LANES.values())",
        "            and True",
        ["tests/test_character_entities.py", "tests/test_board_estimate.py"],
    ),
    (
        "the reference guard widens to a prefix, waving through unmeasured names",
        SDK,
        '            and not model_key.startswith(("abra_", "omni_flash_"))\n'
        "            and model_key not in set(VEO_R2V_LANES.values())",
        '            and not model_key.startswith(("abra_", "omni_flash_", "veo_3_1_r2v_"))',
        ["tests/test_character_entities.py"],
    ),
    (
        "the unmeasured reference key is quoted as free again",
        EST,
        "        return None, (\n"
        '            f"{_LANE_LABELS.get(lane, lane)} (Veo reference) — hậu tố "',
        "        return 0, (\n"
        '            f"{_LANE_LABELS.get(lane, lane)} (Veo reference) — hậu tố "',
        ["tests/test_board_estimate.py"],
    ),
    # ── P14b: the cleanup round ──────────────────────────────────────────
    (
        "the re-poll stops carrying the project its operations were made in",
        EXEC,
        '            "project_id": (row.params or {}).get("project_id"),',
        '            "project_id": None,',
        ["tests/test_repoll.py"],
    ),
    (
        "the poll handler stops re-seeding the project after a restart",
        PROC,
        '    sdk.remember_operations(names, params.get("project_id"))',
        "    pass",
        ["tests/test_repoll.py"],
    ),
    (
        "a 360p text-to-video is rendered at 720p again",
        SDK,
        '                model_key = omni_model_key("text", duration_s, resolution or "720p")',
        '                model_key = omni_model_key("text", duration_s)',
        ["tests/test_t2v.py"],
    ),
    (
        "the worker stops passing the resolution it was given",
        PROC,
        "        variant_count=variant_count,\n        resolution=resolution,",
        "        variant_count=variant_count,",
        ["tests/test_t2v.py"],
    ),
    (
        "the gave-up rule stops recognising a row we stopped waiting for",
        EXEC,
        '_GAVE_UP_STATUSES = ("canceled", "timeout")',
        '_GAVE_UP_STATUSES = ("canceled",)',
        ["tests/test_repoll.py"],
    ),
    (
        "the character port stops offering the Veo lane that works",
        MODELS,
        "            qualities=_reference_lane_options(),",
        '            qualities=[Option(value="omni", label=QUALITY_LABELS["omni"])],',
        ["tests/test_models_registry.py"],
    ),
    (
        "the registry hand-types its lane list again, so a new lane 500s",
        MODELS,
        "            label=QUALITY_LABELS.get(key, key),\n"
        "            note=(\n"
        '                f"Veo chưa có payload {capability} trên đường mới — "',
        "            label=QUALITY_LABELS[key],\n"
        "            note=(\n"
        '                f"Veo chưa có payload {capability} trên đường mới — "',
        ["tests/test_models_registry.py"],
    ),
    (
        "the clip-review loop becomes unreachable again",
        NODES,
        '''        "review_loop",
        "reviewLoop",''',
        '''        "_review_loop_disabled",
        "reviewLoop",''',
        ["tests/test_node_settings.py", "tests/test_board_estimate.py"],
    ),
    (
        "the canvas spelling stops reaching the reader",
        NODES,
        '        ("reviewLoop", "review_loop"),',
        '        ("reviewLoopXX", "review_loop"),',
        ["tests/test_node_settings.py"],
    ),
]


def run(tests: list[str]) -> bool:
    """True when the test selection passes."""
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
        source = file.read_text(encoding="utf-8")
        if source.count(old) != 1:
            print(f"{index:2d}. ANCHOR MISSED ({source.count(old)}x): {label}")
            survived.append(f"{index}. {label} (anchor)")
            continue
        file.write_text(source.replace(old, new), encoding="utf-8")
        try:
            passed = run(tests)
        finally:
            file.write_text(source, encoding="utf-8")
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
