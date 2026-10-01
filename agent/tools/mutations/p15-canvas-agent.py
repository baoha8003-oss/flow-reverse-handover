"""Mutation run for the canvas-agent build and the review-loop fix.

Each entry breaks exactly one decision and names the tests that must go red.
Three groups, and the first two are the ones worth re-running before any commit:

* **The money rule.** No action the agent can take may create a `Request` row,
  and a scoped run must not price or dispatch the whole board. Both are checked
  by breaking the gate and watching a specific test fail.
* **The review loop's CRITICAL override.** A named CRITICAL fault outranks the
  score. The `character_consistency` cap alone tops out at 8.25 — above the 7.0
  default — so without this a beautiful clip with one face change is accepted.
* **The derived catalogue.** Every port table is read from the consumer that
  honours it. Breaking a derivation has to fail the check that reads the eleven
  packaged workflows, or the catalogue can drift back into being typed.

Run from `agent/`: .venv/Scripts/python.exe tools/mutations/p15-canvas-agent.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Labels quote Vietnamese error copy, and a Windows console defaults to cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LOOP = "flowboard/services/review_loop.py"
REVIEW = "flowboard/services/video_review.py"
CATALOG = "flowboard/services/canvas_catalog.py"
RULES = "flowboard/services/agent_rules.py"
AGENT = "flowboard/services/canvas_agent.py"
ROUTE = "flowboard/routes/agent.py"
PLANS = "flowboard/routes/plans.py"
EST = "flowboard/routes/estimate.py"
OPS = "flowboard/services/node_ops.py"
PLAN_LAYER = "flowboard/services/postprod_plan.py"

T_LOOP = ["tests/test_review_loop.py"]
T_CATALOG = ["tests/test_canvas_catalog.py"]
T_RULES = ["tests/test_agent_rules.py"]
T_AGENT = ["tests/test_canvas_agent.py"]
T_PLAN = ["tests/test_board_plan.py"]
T_EST = ["tests/test_board_estimate.py"]
T_NODES = ["tests/test_nodes.py"]
T_BRANCH = ["tests/test_llm_nodes_run.py"]

# (label, file, old, new, tests)
MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    # ── the review loop's CRITICAL override ────────────────────────────
    (
        "a clip carrying a named CRITICAL fault is accepted on its score",
        LOOP,
        "if over_threshold and not has_critical:",
        "if over_threshold:",
        T_LOOP,
    ),
    (
        "the reason reports a bare score, sending someone to hunt a scoring bug",
        LOOP,
        'f"Đạt ngưỡng {settings.threshold:g} nhưng reviewer nêu lỗi CRITICAL"',
        'f"Điểm {score:g} dưới ngưỡng {settings.threshold:g}"',
        T_LOOP,
    ),
    (
        "an unknown severity is normalised in silence",
        REVIEW,
        "            logger.warning(\n"
        '                "review: unknown severity %r normalised to MINOR (%s)",',
        "            _ = (\n"
        '                "review: unknown severity %r normalised to MINOR (%s)",',
        ["tests/test_video_review.py"],
    ),
    # ── the money rule: a scoped run stays scoped ──────────────────────
    (
        "the plan drops the scope it was handed, so Run runs everything",
        PLANS,
        '            "_run_only_node_ids": scope,',
        '            "_run_only_node_ids": [],',
        T_PLAN,
    ),
    (
        "a node from another board is waved into the scope",
        PLANS,
        "        if stranger:",
        "        if False:",
        T_PLAN,
    ),
    (
        "the quote stops scoping, so a three-node run is priced as twelve",
        EST,
        "        if scope and node.id not in scope:\n            continue\n",
        "        if False:\n            continue\n",
        T_EST,
    ),
    (
        "the graph is narrowed with the scope, so a scoped clip loses its wire",
        EST,
        "    upstream_of = {\n"
        "        n.id: _upstream_with_ports(n.id, edges, by_id) for n in nodes if n.id\n"
        "    }",
        "    upstream_of = {\n"
        "        n.id: _upstream_with_ports(n.id, [], by_id) for n in nodes if n.id\n"
        "    }",
        T_EST,
    ),
    # ── the money rule: the agent cannot dispatch ──────────────────────
    (
        "a run action becomes a real write path instead of an intent",
        AGENT,
        "            if kind in INTENT_ONLY:",
        "            if False:",
        T_AGENT,
    ),
    (
        "editing under a live run is allowed, so an approved quote is rewired",
        AGENT,
        '        if _run_in_flight(s, board_id):\n            raise AgentError(\n                "run_in_flight",',
        '        if False:\n            raise AgentError(\n                "run_in_flight",',
        T_AGENT,
    ),
    (
        "a busy board stops answering 409, so the panel cannot disable Apply",
        ROUTE,
        '    "run_in_flight": 409,',
        '    "run_in_flight_x": 409,',
        T_AGENT,
    ),
    (
        "blocking findings stop stopping the apply",
        AGENT,
        "        if agent_rules.blocking(findings):",
        "        if False:",
        T_AGENT,
    ),
    # ── undo is structural only ────────────────────────────────────────
    (
        "undo throws away media that has been paid for",
        AGENT,
        '    if data.get("mediaId") or data.get("mediaIds"):',
        "    if False:",
        T_AGENT,
    ),
    (
        "undo touches a node that is mid-flight",
        AGENT,
        '    if node.status != "idle":',
        "    if False:",
        T_AGENT,
    ),
    (
        "undo walks the oldest change, reinstating what was deleted later",
        AGENT,
        "            .order_by(CanvasAction.id.desc())  # type: ignore[attr-defined]\n"
        "        ).first()\n"
        "        if row is None:\n"
        '            raise AgentError("nothing_to_undo"',
        "            .order_by(CanvasAction.id.asc())  # type: ignore[attr-defined]\n"
        "        ).first()\n"
        "        if row is None:\n"
        '            raise AgentError("nothing_to_undo"',
        T_AGENT,
    ),
    (
        "an undone row is not marked, so it undoes forever",
        AGENT,
        "        row.undone_at = datetime.now(timezone.utc)",
        "        pass",
        T_AGENT,
    ),
    (
        "the batch stops resolving its own handles, so a fresh wire dangles",
        AGENT,
        '        if body.get("ref"):\n            refs[str(body["ref"])] = node.id',
        '        if False:\n            refs[str(body["ref"])] = node.id',
        T_AGENT,
    ),
    (
        "the provider that answered is dropped",
        AGENT,
        "            board_id=board_id, kind=kind, provider=provider,\n"
        '            summary=f"Thêm node {node_type}"',
        "            board_id=board_id, kind=kind, provider=None,\n"
        '            summary=f"Thêm node {node_type}"',
        T_AGENT,
    ),
    (
        "configure stops snapshotting the previous value, so undo restores nothing",
        AGENT,
        "        before = {k: (node.data or {}).get(k) for k in patch}",
        "        before = {}",
        T_AGENT,
    ),
    (
        "a node from another board can be configured",
        AGENT,
        "    if node is None or node.board_id != board_id:",
        "    if node is None:",
        T_AGENT,
    ),
    (
        "the node type stops being checked against the catalogue",
        AGENT,
        "        if node_type not in canvas_catalog.NODE_TYPES:",
        "        if False:",
        T_AGENT,
    ),
    # ── the catalogue stays derived ────────────────────────────────────
    (
        "the catalogue loses the cover socket a packaged workflow wires",
        CATALOG,
        '        *_named(postprod_plan.THUMBNAIL_PORTS),\n        "title",',
        '        "title",',
        T_CATALOG,
    ),
    (
        "the catalogue loses the image node's reference slots",
        CATALOG,
        '    "image": ("prompt", "image_1", "image_2", "image_3"),',
        '    "image": ("prompt",),',
        T_CATALOG,
    ),
    (
        "the catalogue stops advertising the branch outputs",
        CATALOG,
        '    "analyze_video": tuple(sorted(set(postprod_plan.BRANCH_FIELDS))),',
        '    "analyze_video": (),',
        T_CATALOG,
    ),
    (
        "the node type list drifts from the route that accepts them",
        CATALOG,
        '    "sync_image_voice", "remove_watermark", "review_video",',
        '    "sync_image_voice", "remove_watermark",',
        T_CATALOG,
    ),
    (
        "the character ceiling is hard-coded instead of asked",
        CATALOG,
        "    return character_ports.limit_for(lane)",
        "    return 99",
        T_CATALOG,
    ),
    (
        "a type with no inputs accepts wires that feed nothing",
        CATALOG,
        "    if not PORTS_IN[node_type] and not character_ports.is_character_port(port):",
        "    if False:",
        T_CATALOG,
    ),
    # ── the cover socket has a field behind it ─────────────────────────
    (
        "the cover socket loses its field, so it delivers the scene description",
        PLAN_LAYER,
        '    "thumbnail_prompt": "thumbnailPrompt",',
        '    "_thumbnail_off": "thumbnailPrompt",',
        T_BRANCH,
    ),
    (
        "branch_text stops preferring the branch field over the plain prompt",
        PLAN_LAYER,
        '    for key in ((field,) if field else ()) + ("composedPrompt", "prompt"):',
        '    for key in ("composedPrompt", "prompt"):',
        T_BRANCH,
    ),
    # ── the rules are asked, not restated ─────────────────────────────
    (
        "the cast stops being tokenised, so every real name blocks the board",
        RULES,
        '        token = character_store.normalize_name(node.title or "")',
        '        token = (node.title or "").strip()',
        T_RULES,
    ),
    # NOT LISTED, and the reason is worth keeping: dropping the `if token:`
    # guard in `agent_rules._cast` is an EQUIVALENT mutation. `prompt_checks`
    # builds its own cast with `if c and c.strip()`, so an empty string passed
    # in is filtered there and nothing observable changes. The guard stays
    # because it documents the intent and survives a change on that side, but
    # a test for it would be a test of `prompt_checks`, not of this module.
    (
        "target sockets stop being checked",
        RULES,
        "        if not incoming.ok:",
        "        if False:",
        T_RULES,
    ),
    (
        "source sockets stop being checked",
        RULES,
        "        if not outgoing.ok:",
        "        if False:",
        T_RULES,
    ),
    (
        "the reference ceiling stops being enforced on a proposal",
        RULES,
        "        if len(distinct) > limit:",
        "        if False:",
        T_RULES,
    ),
    (
        "the ceiling counts wires instead of distinct sockets",
        RULES,
        "        distinct = sorted(set(ports))",
        "        distinct = sorted(ports)",
        T_RULES,
    ),
    (
        "a wire to a node outside the proposal is accepted",
        RULES,
        "        if target is None or source is None:",
        "        if False:",
        T_RULES,
    ),
    (
        "the prompt rules stop running over a proposal",
        RULES,
        "        findings.extend(\n"
        "            prompt_checks.check(node.prompt, cast=cast or None, lane=lane)\n"
        "        )",
        "        pass",
        T_RULES,
    ),
    # ── the shared node mutations ─────────────────────────────────────
    (
        "the merge sentinel stops deleting a key, so a field cannot be cleared",
        OPS,
        "        if value is None:\n            merged.pop(key, None)",
        "        if False:\n            merged.pop(key, None)",
        T_NODES + ["tests/test_node_settings.py"],
    ),
    (
        "the cascade stops detaching history, so the delete aborts on the FK",
        OPS,
        "    for r in requests:\n        r.node_id = None\n        s.add(r)",
        "    for r in requests:\n        pass",
        T_NODES,
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
        raw = file.read_bytes()
        # The repo carries both line endings. Rewriting a whole file's endings
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
