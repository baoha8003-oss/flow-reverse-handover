"""Mutation run for P13.6 / P13.8.

Each entry breaks exactly one decision made in this phase and names the tests
that must go red. A surviving mutation is a test with no teeth, not a mutation
worth keeping: either the assertion is missing or the decision is not actually
load-bearing, and both are worth knowing before a live run spends credits.

Run from `agent/`: .venv/Scripts/python.exe tools/mutations/p13-batch-migration.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SDK = "flowboard/services/flow_sdk.py"
BATCH = "flowboard/services/flow_batch.py"
PROC = "flowboard/worker/processor.py"
EXEC = "flowboard/services/pipeline_executor.py"
EST = "flowboard/routes/estimate.py"
MODELS = "flowboard/routes/models.py"
NODES = "flowboard/services/node_settings.py"
SETTINGS = "flowboard/services/settings_store.py"
AUTH = "flowboard/routes/auth.py"

# (label, file, old, new, tests)
MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    (
        "a video key outside the accepted set is passed through",
        BATCH,
        '    if isinstance(key, str) and key in VIDEO_MODELS:\n        return key',
        '    if isinstance(key, str):\n        return key',
        ["tests/test_flow_batch.py", "tests/test_flow_batch_golden.py",
         "tests/test_money_safety.py"],
    ),
    (
        "an unknown lane folds onto the default instead of refusing",
        SDK,
        '''    key = BATCH_VIDEO_LANES.get(requested)
    if key is None:''',
        '''    key = BATCH_VIDEO_LANES.get(requested, BATCH_VIDEO_LANES[DEFAULT_VIDEO_QUALITY])
    if key is None:''',
        ["tests/test_empty_quality_falls_back_expensively.py"],
    ),
    (
        "a Veo start-end dispatch quietly becomes a plain first-frame clip",
        SDK,
        '''    if start_end:
        plan["error"] = (''',
        '''    if False:
        plan["error"] = (''',
        ["tests/test_start_end_fl.py"],
    ),
    # The blanket "Veo text-to-video is refused" guard used to be mutated here.
    # The live measurement removed the code: `YhhmEf` parses Veo keys, so the
    # lanes dispatch. The property that replaced it — never silently sending
    # Omni for a lane the board did not choose — is the "goes back to sending
    # Omni whatever the lane asked for" entry below.
    (
        "reference entities are dropped instead of refused",
        SDK,
        "        if entities:\n            return {\n                \"raw\": None,",
        "        if False:\n            return {\n                \"raw\": None,",
        ["tests/test_character_entities.py"],
    ),
    (
        "a text-to-video lane with no key folds onto the default",
        SDK,
        "    keys = VEO_T2V_LANES.get(lane)\n    if keys is None:",
        "    keys = VEO_T2V_LANES.get(lane, VEO_T2V_LANES[DEFAULT_VIDEO_QUALITY])\n"
        "    if keys is None:",
        ["tests/test_t2v.py"],
    ),
    (
        "a text-to-video length Veo lacks is rounded to the nearest key",
        SDK,
        "    key = keys.get(duration_s)\n    if key is None:",
        "    key = keys.get(duration_s, keys[8])\n    if key is None:",
        ["tests/test_t2v.py", "tests/test_board_estimate.py"],
    ),
    (
        "text-to-video goes back to sending Omni whatever the lane asked for",
        SDK,
        "            plan = resolve_t2v_plan(lane or DEFAULT_VIDEO_QUALITY, duration_s)",
        '            plan = {"model_key": omni_model_key("text", duration_s), "error": None}',
        ["tests/test_t2v.py"],
    ),
    (
        "a mid-batch failure drops the operations already created",
        SDK,
        '    return {"raw": None, "error": err, "operation_names": list(operations)}',
        '    return {"raw": None, "error": err}',
        ["tests/test_flow_sdk.py"],
    ),
    (
        "the 17 MB project listing is fetched without a match window",
        SDK,
        "            match=operation_id,\n            timeout=120,",
        "            timeout=120,",
        ["tests/test_flow_sdk.py"],
    ),
    (
        "a poster-only media record counts as a finished clip",
        SDK,
        "        if not urls.video:",
        "        if False:",
        ["tests/test_flow_sdk.py"],
    ),
    (
        "the listing is never consulted on a quiet round",
        SDK,
        "        worth_looking = rounds % LISTING_EVERY_NTH_ROUND == 0",
        "        worth_looking = False",
        ["tests/test_flow_sdk.py"],
    ),
    (
        "Flow's transient [8] rejection is not marked as retryable",
        SDK,
        "        if exc.detail == [fb.RPC_RESOURCE_EXHAUSTED]:",
        "        if False:",
        ["tests/test_t2v.py"],
    ),
    (
        "Flow's own error code is buried again instead of leading the string",
        SDK,
        "        reason = fb.read_rpc_reason(exc.detail)",
        "        reason = None",
        ["tests/test_flow_sdk.py"],
    ),
    (
        "an Omni duration that does not exist is rounded instead of refused",
        SDK,
        "    if duration_s not in OMNI_VALID_DURATIONS:\n        raise ValueError(",
        "    if False:\n        raise ValueError(",
        ["tests/test_requests.py", "tests/test_money_safety.py"],
    ),
    (
        "a page that cannot sign is treated as a terminal failure",
        PROC,
        "        any(sub in el for sub in _PAGE_UNSIGNED_SUBSTRINGS)\n        or ",
        "        ",
        ["tests/test_flow_sdk.py", "tests/test_worker_retry.py"],
    ),
    (
        "the captcha codes lose to the terminal-prefix rule again",
        PROC,
        "    if any(code in el for code in _CAPTCHA_CODES):",
        "    if False:",
        ["tests/test_worker_retry.py"],
    ),
    (
        "the poll result no longer carries the key that dispatched",
        PROC,
        '    if dispatch.get("model_key"):\n        out["model_key"] = dispatch["model_key"]',
        '    if False:\n        out["model_key"] = dispatch["model_key"]',
        ["tests/test_t2v.py"],
    ),
    (
        "the reference handler serves Omni on a Veo lane",
        SDK,
        # Moved twice. First when Veo's own r2v key turned out to work, then into
        # `resolve_character_port_plan` when the quote and the run were made to
        # share one resolver — they had disagreed about the FAMILY, quoting a
        # character board at 0 credits that dispatched at 75. Breaking the
        # condition still produces the original bug: every lane silently becomes
        # Omni, and Omni bills.
        '''    if lane and lane != "omni":
        plan = resolve_r2v_plan(lane)''',
        '''    if False:
        plan = resolve_r2v_plan(lane)''',
        ["tests/test_money_safety.py", "tests/test_motion_control.py"],
    ),
    (
        "the estimate stops asking whether a lane can be dispatched",
        EST,
        # The comment line disambiguates: `_price_of` opens with the same two
        # statements since both were made to ask the family first.
        '''    lane = _lane_of(node)
    if has_characters:
        # Asked first''',
        '''    return None
    lane = _lane_of(node)
    if has_characters:
        # Asked first''',
        ["tests/test_batch_workflow_sheet.py", "tests/test_board_estimate.py"],
    ),
    (
        "the free lane is quoted as unpriced rather than zero",
        EST,
        "    if flow_sdk.is_zero_credit_model(model_key):\n        return 0,",
        "    if False:\n        return 0,",
        ["tests/test_node_settings.py"],
    ),
    (
        "the estimate asks whether a start frame has RESOLVED, not whether it is wired",
        EST,
        "                        has_start_frame=has_start_frame_wire(wires),",
        "                        has_start_frame=bool(_start_frame_present(node, wires)),",
        ["tests/test_storyboard.py", "tests/test_hand_drawn_wires.py",
         "tests/test_board_estimate.py"],
    ),
    (
        "a timed-out render is re-dispatched instead of re-polled",
        EXEC,
        "            repoll = _unresolved_video_operations(nid, current=repoll_shape)",
        "            repoll = None",
        ["tests/test_repoll.py"],
    ),
    (
        "operations Google terminally refused are re-polled forever",
        EXEC,
        '            if isinstance(reason, str) and reason and reason != "timeout_waiting_video":',
        "            if False:",
        ["tests/test_repoll.py"],
    ),
    (
        "a lane note promises the paid substitution again",
        MODELS,
        # Two call sites write that note now, image-to-video and
        # text-to-video, so it is anchored on the loop the image-to-video one
        # runs — emptying it drops the warning from every refused lane.
        "    for key, why in flow_sdk.REFUSED_VIDEO_LANES.items():",
        "    for key, why in []:",
        ["tests/test_models_registry.py"],
    ),
    (
        "a Veo lane with no reference key folds onto Omni and bills",
        SDK,
        '''    key = VEO_R2V_LANES.get(lane)
    if key:''',
        '''    key = VEO_R2V_LANES.get(lane, "veo_3_1_r2v_lite_low_priority")
    if key:''',
        ["tests/test_money_safety.py", "tests/test_motion_control.py"],
    ),
    (
        "the character port stops serving Veo's free reference key",
        SDK,
        "        plan = resolve_r2v_plan(lane)",
        '        plan = {"model_key": None, "error": "nope"}',
        ["tests/test_money_safety.py", "tests/test_board_estimate.py"],
    ),
    (
        "a text-to-video lane measured unknown is offered as if it worked",
        SDK,
        '''REFUSED_T2V_LANES: dict[str, str] = {
    "fast_relaxed": (''',
        '''REFUSED_T2V_LANES: dict[str, str] = {
    "unused_lane_zz": (''',
        ["tests/test_models_registry.py"],
    ),
    (
        "a node's resolution never reaches the dispatch",
        NODES,
        # The tuple gained the clip-review keys, so the closing `):` no longer
        # follows these two. Anchored on the pair alone, which is what the
        # mutation is about.
        '''        "resolution",
        "video_resolution",''',
        '''        "resolution_disabled",
        "video_resolution_disabled",''',
        ["tests/test_node_settings.py"],
    ),
    (
        "a resolution Flow rejects is coerced to the pricier one",
        NODES,
        '''    logger.info("node_settings: unmapped resolution %r", raw)
    return None''',
        '''    logger.info("node_settings: unmapped resolution %r", raw)
    return "720p"''',
        ["tests/test_node_settings.py"],
    ),
    (
        "config.json values bypass the closed-set check",
        SETTINGS,
        "        if key in _ENUM_VALUES and validate_value(key, value) is not None:",
        "        if False:",
        ["tests/test_flow_account_settings.py"],
    ),
    (
        "the free probe will send any rpcid it is handed",
        AUTH,
        "    if body.rpcid not in _PROBE_RPCS:",
        "    if False:",
        ["tests/test_auth.py"],
    ),
    (
        "the free probe echoes the listing window it read",
        AUTH,
        '        "response_chars": len(raw) if isinstance(raw, str) else None,',
        '        "response_chars": raw,',
        ["tests/test_auth.py"],
    ),
    (
        "the free probe hands back the signed media urls",
        AUTH,
        '                out["has_video_url"] = urls.video is not None',
        '                out["has_video_url"] = urls.video',
        ["tests/test_auth.py"],
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
