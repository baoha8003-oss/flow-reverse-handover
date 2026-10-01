"""Pipeline executor — materialize a chat-proposed plan and run it.

A ``Plan.spec`` looks roughly like::

    {
      "nodes": [
        {"tmp_id": "a", "type": "character", "params": {"prompt": "..."}},
        {"tmp_id": "b", "type": "image",     "params": {"prompt": "..."}},
        {"tmp_id": "c", "type": "video",     "params": {"prompt": "..."}}
      ],
      "edges": [
        {"from": "a", "to": "b", "kind": "ref"},
        {"from": "b", "to": "c", "kind": "ref"}
      ],
      "layout_hint": "left_to_right"
    }

``materialize_plan`` writes the spec to the DB (Node + Edge rows). Endpoints
in edges may reference a ``tmp_id`` from this plan or a ``#shortId`` of an
existing node on the same board — the executor resolves both.

``run_pipeline`` walks the resulting DAG topologically. For ``image``/``video``
nodes that have a prompt, it enqueues a Request through the existing worker
(``flowboard.worker.processor``) and waits for the row to settle, threading
upstream media ids forward (character refs for image, start media id for
video). One node's failure does not abort the whole run — independent branches
keep going; downstream-of-failure nodes are short-circuited to ``error`` with
a synthetic ``upstream_failed`` reason.

This module is imported by ``routes/plans.py``; the actual ``asyncio.create_task``
spawning happens there so we stay testable here.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import Edge, Node, PipelineRun, Plan, Request
from flowboard.short_id import generate_unique_short_id
from flowboard.services import node_settings, review_loop
from flowboard.worker.processor import get_worker

logger = logging.getLogger(__name__)


# ── Layout ────────────────────────────────────────────────────────────────


COL_WIDTH = 280
ROW_HEIGHT = 200
ORIGIN_X = 200.0
ORIGIN_Y = 200.0


def auto_layout(
    spec_nodes: list[dict],
    spec_edges: list[dict],
    *,
    existing_short_ids: set[str] | None = None,
) -> dict[str, tuple[float, float]]:
    """Assign (x, y) to each ``tmp_id`` based on topological depth.

    Edges that reference unknown endpoints (typo, or ``#shortId`` of a
    pre-existing node) are ignored for depth purposes — they don't push
    anything in the new plan deeper.
    """
    tmp_ids = [n.get("tmp_id") for n in spec_nodes if isinstance(n.get("tmp_id"), str)]
    tmp_set = set(tmp_ids)
    existing = existing_short_ids or set()

    incoming: dict[str, set[str]] = defaultdict(set)
    outgoing: dict[str, set[str]] = defaultdict(set)
    for e in spec_edges:
        src = _normalise_endpoint(e.get("from"))
        dst = _normalise_endpoint(e.get("to"))
        if src is None or dst is None:
            continue
        # Only consider edges whose target is one of the new plan nodes for
        # depth calculation. Edges to/from existing #shortId nodes don't shift
        # new layout.
        if dst in tmp_set:
            if src in tmp_set:
                incoming[dst].add(src)
                outgoing[src].add(dst)
            elif src in existing:
                # Treat as a virtual root: increment depth by 1.
                incoming[dst].add(f"__existing__:{src}")

    # Compute depth via BFS from roots.
    depth: dict[str, int] = {}
    roots = [t for t in tmp_ids if not incoming.get(t)]
    queue: list[tuple[str, int]] = [(r, 0) for r in roots]
    while queue:
        node, d = queue.pop(0)
        prev = depth.get(node)
        if prev is not None and prev >= d:
            continue
        depth[node] = d
        for child in outgoing.get(node, ()):
            queue.append((child, d + 1))

    # Any node not reached (cycle) gets depth 0.
    for t in tmp_ids:
        depth.setdefault(t, 0)

    # Group by depth and assign row index.
    by_depth: dict[int, list[str]] = defaultdict(list)
    for t in tmp_ids:  # preserve declaration order within a depth
        by_depth[depth[t]].append(t)

    layout: dict[str, tuple[float, float]] = {}
    for d, ids in by_depth.items():
        for row, t in enumerate(ids):
            layout[t] = (ORIGIN_X + d * COL_WIDTH, ORIGIN_Y + row * ROW_HEIGHT)
    return layout


def _normalise_endpoint(raw: Any) -> Optional[str]:
    """Edges may use bare ``tmp_id`` or ``#shortId``. Strip the leading ``#``."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    return raw[1:] if raw.startswith("#") else raw


# ── Materialisation ────────────────────────────────────────────────────────


# Kept in step with `routes.nodes.NodeType`. The post-production types run
# locally through the `postprod` request type instead of a Flow dispatch.
_POSTPROD_NODE_TYPES = {
    "analyze_video",
    "merge_video",
    "edit_video",
    "extract_last_frame",
    "add_bgm",
    "create_voice",
    "align_video_voice",
    "sync_image_voice",
    "remove_watermark",
    "review_video",
}
#: Generation nodes — they reach Flow and cost money.
#:
#: `motion_control` is deliberately NOT here. Its dispatch resolved a
#: reference-image request, while the packaged tool's Motion Control drives
#: Flow's Edit Video endpoint with an uploaded clip — a different generation at
#: a different price. Running it spent money on something other than what the
#: node's name promises, so the node is off the canvas until that endpoint is
#: built. `services/motion_control.py` keeps the mined port order and refusals
#: for when it is.
_GENERATION_NODE_TYPES = {"image", "video"}

_VALID_NODE_TYPES = {
    "character",
    "prompt",
    "note",
    "Storyboard",
    # A media holder — an upload, a link list, a saved reference. The canvas
    # and `routes/nodes` have accepted it since P6; this allowlist did not, and
    # a type missing here is a node `materialize_plan` SKIPS. A board built
    # from an archetype came out without the socket the user drops the source
    # video into, and nothing said so.
    "visual_asset",
    *_GENERATION_NODE_TYPES,
    *_POSTPROD_NODE_TYPES,
}


def materialize_plan(session, plan_id: int) -> dict:
    """Create Node + Edge rows for ``plan.spec``. Returns a summary dict.

    Idempotent at the row level — won't re-create rows on re-run. Looks for an
    existing PipelineRun-tagged set via ``Plan.spec[\"_materialized_node_ids\"]``;
    if present, returns those rows untouched.
    """
    plan = session.get(Plan, plan_id)
    if plan is None:
        raise ValueError(f"plan {plan_id} not found")

    spec = plan.spec or {}
    spec_nodes = spec.get("nodes") or []
    spec_edges = spec.get("edges") or []
    if not isinstance(spec_nodes, list):
        spec_nodes = []
    if not isinstance(spec_edges, list):
        spec_edges = []

    # Idempotent re-run: if we've already materialised, return what we have.
    cached_ids = spec.get("_materialized_node_ids")
    if isinstance(cached_ids, list) and cached_ids:
        nodes = list(
            session.exec(select(Node).where(Node.id.in_(cached_ids))).all()  # type: ignore[attr-defined]
        )
        if nodes:
            return {
                "plan_id": plan_id,
                "node_ids": [n.id for n in nodes],
                "tmp_to_node_id": spec.get("_tmp_to_node_id") or {},
                "created": False,
            }

    board_id = plan.board_id
    # Existing #shortId index for edge endpoint resolution.
    existing_nodes = list(
        session.exec(select(Node).where(Node.board_id == board_id)).all()
    )
    short_id_to_node: dict[str, Node] = {n.short_id: n for n in existing_nodes if n.short_id}

    layout = auto_layout(
        spec_nodes, spec_edges, existing_short_ids=set(short_id_to_node)
    )

    # Build initial data + create Node rows.
    tmp_to_node_id: dict[str, int] = {}
    created_node_ids: list[int] = []
    skipped: list[str] = []

    for spec_node in spec_nodes:
        if not isinstance(spec_node, dict):
            continue
        tmp_id = spec_node.get("tmp_id")
        node_type = spec_node.get("type")
        if not isinstance(tmp_id, str) or not tmp_id:
            skipped.append("(missing tmp_id)")
            continue
        if node_type not in _VALID_NODE_TYPES:
            skipped.append(tmp_id)
            logger.warning("plan %s: skipping node %s with bad type %r", plan_id, tmp_id, node_type)
            continue
        params = spec_node.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        title = (
            params.get("title")
            if isinstance(params.get("title"), str)
            else node_type.title()
        )
        prompt = params.get("prompt") if isinstance(params.get("prompt"), str) else None

        x, y = layout.get(tmp_id, (ORIGIN_X, ORIGIN_Y))
        short_id = generate_unique_short_id(session, board_id)
        data: dict[str, Any] = {"title": title}
        if prompt:
            data["prompt"] = prompt
        # Generation settings ride under `sourceSettings` because that is the
        # single place `node_settings.merged_settings` looks for them. This
        # function used to drop them, and the failure was invisible in the
        # worst way: the board LOOKED configured on the canvas while every
        # dispatch went out at Flow's defaults — landscape, default duration,
        # default lane — and the lane default is the one that costs credits.
        source_settings = params.get("sourceSettings")
        if isinstance(source_settings, dict) and source_settings:
            data["sourceSettings"] = dict(source_settings)
        node = Node(
            board_id=board_id,
            short_id=short_id,
            type=node_type,
            x=x,
            y=y,
            data=data,
            status="idle",
        )
        session.add(node)
        session.flush()  # need node.id for tmp_to_node_id
        assert node.id is not None
        tmp_to_node_id[tmp_id] = node.id
        created_node_ids.append(node.id)

    # Edges. Endpoints resolve via tmp_to_node_id first, then existing short_ids.
    created_edge_ids: list[int] = []
    for spec_edge in spec_edges:
        if not isinstance(spec_edge, dict):
            continue
        src_raw = _normalise_endpoint(spec_edge.get("from"))
        dst_raw = _normalise_endpoint(spec_edge.get("to"))
        if src_raw is None or dst_raw is None:
            continue
        # `kind` names the edge's ROLE and only ever means "ref" or "hint".
        # A spec that says `kind: "start_frame"` or `kind: "media"` is naming
        # the destination PORT instead — which is exactly what
        # `storyboard.build_spec` writes. The old code coerced any
        # unrecognised value to "ref" and discarded it, so a materialised
        # board could not tell a start-frame wire from a plain reference and
        # the executor had no way to know which upstream fed which socket.
        raw_kind = spec_edge.get("kind") if isinstance(spec_edge.get("kind"), str) else "ref"
        kind = raw_kind if raw_kind in ("ref", "hint") else "ref"

        target_port = spec_edge.get("target_port")
        if not isinstance(target_port, str) or not target_port.strip():
            # Fall back to the port hiding in `kind`, if that is what it was.
            target_port = None if raw_kind in ("ref", "hint") else raw_kind.strip()
        else:
            target_port = target_port.strip()
        source_port = spec_edge.get("source_port")
        source_port = source_port.strip() if isinstance(source_port, str) and source_port.strip() else None

        src_id = _resolve_endpoint(src_raw, tmp_to_node_id, short_id_to_node)
        dst_id = _resolve_endpoint(dst_raw, tmp_to_node_id, short_id_to_node)
        if src_id is None or dst_id is None:
            logger.warning(
                "plan %s: skipping edge %s→%s (unresolved endpoint)",
                plan_id, src_raw, dst_raw,
            )
            continue
        if src_id == dst_id:
            continue

        edge = Edge(
            board_id=board_id, source_id=src_id, target_id=dst_id, kind=kind,
            source_port=source_port, target_port=target_port,
        )
        session.add(edge)
        session.flush()
        assert edge.id is not None
        created_edge_ids.append(edge.id)

    # Persist materialisation hint on the plan so re-runs are idempotent.
    new_spec = dict(spec)
    new_spec["_materialized_node_ids"] = created_node_ids
    new_spec["_tmp_to_node_id"] = tmp_to_node_id
    plan.spec = new_spec
    session.add(plan)
    # Caller commits.

    return {
        "plan_id": plan_id,
        "node_ids": created_node_ids,
        "edge_ids": created_edge_ids,
        "tmp_to_node_id": tmp_to_node_id,
        "skipped": skipped,
        "created": True,
    }


def _resolve_endpoint(
    raw: str,
    tmp_to_node_id: dict[str, int],
    short_id_to_node: dict[str, Node],
) -> Optional[int]:
    if raw in tmp_to_node_id:
        return tmp_to_node_id[raw]
    node = short_id_to_node.get(raw)
    if node is not None and node.id is not None:
        return node.id
    return None


# ── Execution ─────────────────────────────────────────────────────────────


#: How long a node waits for its request row to settle.
#:
#: Derived from the worker's own ceiling rather than picked: the worker polls
#: Flow for VIDEO_POLL_INTERVAL_S * VIDEO_POLL_MAX_CYCLES (7s x 86, ~10
#: minutes, matching the packaged tool), plus a margin for the dispatch round
#: trip and the queue wait before polling even starts.
#:
#: It was 180s, and the first real pipeline run found what that costs. An 8s
#: clip was still generating at three minutes; the executor stamped the node
#: `timeout` and marked the run failed, while the request went on running and
#: Flow went on charging for it. A wait shorter than the operation it waits
#: for does not save anything — it just throws away the result the user paid
#: for and reports a failure that did not happen.
#:
#: A longer wait only narrows that window, it does not close it: a queue
#: paused for a quarter of an hour, a tripped 403 breaker or a closed
#: extension bridge still outlasts any timeout. What closes it is
#: `_cancel_if_queued`, which takes the row off the queue when the executor
#: stops waiting, so nothing can dispatch it afterwards.
def _default_request_timeout_s() -> float:
    from flowboard.worker import processor

    return processor.VIDEO_POLL_INTERVAL_S * processor.VIDEO_POLL_MAX_CYCLES + 120.0


_REQUEST_POLL_INTERVAL_S = 0.5


async def run_pipeline(
    run_id: int,
    *,
    request_timeout_s: Optional[float] = None,
    poll_interval_s: float = _REQUEST_POLL_INTERVAL_S,
) -> None:
    """Execute the plan attached to PipelineRun[run_id]. Long-running."""
    # Resolved here, not as a default argument, so a test that turns the
    # worker's poll knobs down to milliseconds gets a short wait too — a
    # default evaluated at import time would keep the full ten minutes and
    # make the suite sleep for real.
    if request_timeout_s is None:
        request_timeout_s = _default_request_timeout_s()
    logger.info(
        "pipeline run %s: starting (request timeout %.0fs)", run_id, request_timeout_s
    )

    # Load run + plan + materialised nodes/edges.
    with get_session() as s:
        run = s.get(PipelineRun, run_id)
        if run is None:
            logger.warning("pipeline run %s not found at start", run_id)
            return
        plan = s.get(Plan, run.plan_id)
        if plan is None:
            run.status = "failed"
            run.error = "plan_missing"
            run.finished_at = datetime.now(timezone.utc)
            s.add(run)
            s.commit()
            return

        # Mark started.
        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        plan.status = "running"
        s.add(run)
        s.add(plan)
        s.commit()
        s.refresh(run)
        s.refresh(plan)

        node_ids: list[int] = list(plan.spec.get("_materialized_node_ids") or [])
        # A run may be scoped to a subset — "run only the nodes that failed",
        # or a single branch. Nodes OUTSIDE the subset are still loaded below,
        # because a wire into the subset has to resolve to the upstream's
        # mediaId; they are simply never dispatched. Dropping them from the
        # snapshot instead would make every scoped run look like a board of
        # orphans with nothing wired to them.
        run_only: set[int] = {
            i for i in (plan.spec.get("_run_only_node_ids") or []) if isinstance(i, int)
        }
        if not node_ids:
            run.status = "failed"
            run.error = "no_materialized_nodes"
            run.finished_at = datetime.now(timezone.utc)
            plan.status = "failed"
            s.add(run)
            s.add(plan)
            s.commit()
            return

        # Pull the snapshot we need before releasing the session.
        nodes = list(
            s.exec(select(Node).where(Node.id.in_(node_ids))).all()  # type: ignore[attr-defined]
        )
        node_by_id = {n.id: n for n in nodes}
        board_id = plan.board_id
        edges = list(
            s.exec(
                select(Edge).where(
                    Edge.board_id == board_id,
                    Edge.source_id.in_(node_ids) | Edge.target_id.in_(node_ids),  # type: ignore[attr-defined]
                )
            ).all()
        )

    # Build adjacency limited to nodes within this plan.
    incoming: dict[int, list[int]] = defaultdict(list)
    outgoing: dict[int, list[int]] = defaultdict(list)
    for e in edges:
        if e.source_id in node_by_id and e.target_id in node_by_id:
            incoming[e.target_id].append(e.source_id)
            outgoing[e.source_id].append(e.target_id)

    order = _topo_sort(node_ids, incoming)
    failed_nodes: set[int] = set()

    for nid in order:
        node = node_by_id.get(nid)
        if node is None:
            continue

        # Outside the requested subset: skipped whole, before the
        # upstream-failure check. Stamping it would be worse than useless —
        # these are the nodes that already SUCCEEDED, and marking one
        # `upstream_failed` because a re-run neighbour failed again would
        # throw away a good result the user is not re-running.
        if run_only and nid not in run_only:
            continue

        # Short-circuit on upstream failure.
        upstream_failed = any(s in failed_nodes for s in incoming.get(nid, ()))
        if upstream_failed:
            failed_nodes.add(nid)
            _stamp_node_status(nid, "error", error="upstream_failed")
            continue

        if node.type == "analyze_video":
            # Not a postprod op: it reads the clip with the pinned vision
            # provider and writes text. `ops_for` returns nothing for it on
            # purpose, so before this branch existed the node reported success
            # having done nothing — and the three branch sockets downstream
            # (`image_prompts` / `video_prompts` / `voice_prompts`) had no
            # source at all, which is what made them unusable.
            ok = await _run_analysis_node(
                node, _upstream_with_ports(nid, edges, node_by_id), node_by_id
            )
            if not ok:
                failed_nodes.add(nid)
            continue

        if node.type == "prompt":
            # A `gemini_prompt` node imported from the packaged tool carries an
            # INSTRUCTION, not a prompt ("Bạn là chuyên gia tạo kịch bản… Người
            # dùng sẽ…"). Passed downstream verbatim it told the generator to
            # be an expert rather than describing a picture. Running it turns
            # the instruction into the three texts its sockets promise.
            await _run_prompt_node(
                node, _upstream_with_ports(nid, edges, node_by_id), node_by_id
            )
            continue

        if node.type in _POSTPROD_NODE_TYPES:
            # Ports, not just node ids: a narration wire and a music wire
            # both carry audio, and only the port name tells them apart.
            ok = await _run_postprod_node(
                node,
                _upstream_with_ports(nid, edges, node_by_id),
                node_by_id=node_by_id,
                request_timeout_s=request_timeout_s,
                poll_interval_s=poll_interval_s,
            )
            if not ok:
                failed_nodes.add(nid)
            continue

        if node.type not in _GENERATION_NODE_TYPES:
            # character/prompt/note nodes have no generation step.
            continue

        wires = _upstream_with_ports(nid, edges, node_by_id)
        prompt = _prompt_for(node, wires)
        if not prompt:
            # No prompt → leave node idle. Not an error.
            continue

        # The free check, where its own docstring says it belongs: before the
        # dispatch. Both blocking rules are paid for otherwise — a tag inside
        # spoken text is READ ALOUD in the delivered clip, and a real person's
        # name in a visual prompt is refused by Google's filter after the
        # request is sent. Reaching either costs a generation to discover.
        rule_error = _prompt_rule_error(node, wires, prompt)
        if rule_error:
            failed_nodes.add(nid)
            _stamp_node_status(nid, "error", error=rule_error)
            continue

        # An upload socket the user left empty is an error, not something to
        # dispatch around. `video` already refused below; `image` used to drop
        # the reference list and generate anyway, which spends credits on a
        # prompt that names photos it was never given.
        empty_ports = missing_upload_ports(node, wires)
        if empty_ports:
            failed_nodes.add(nid)
            _stamp_node_status(
                nid,
                "error",
                error=f"missing_upload:{','.join(sorted(empty_ports))}",
            )
            continue

        # Resolve project_id from board → BoardFlowProject.
        project_id = _project_id_for_board(board_id)
        if project_id is None:
            failed_nodes.add(nid)
            _stamp_node_status(nid, "error", error="no_project")
            continue

        # Image: collect upstream character mediaIds.
        # Video: pick the first upstream image's mediaId as start.
        upstream_node_ids = incoming.get(nid, ())
        upstream_nodes = [node_by_id[u] for u in upstream_node_ids if u in node_by_id]

        if node.type == "image":
            ref_media_ids = [
                (u.data or {}).get("mediaId")
                for u in upstream_nodes
                if u.type in ("character", "image", "visual_asset")
                and isinstance((u.data or {}).get("mediaId"), str)
            ]
            ref_media_ids = [m for m in ref_media_ids if m]
            params = {
                "prompt": prompt,
                "project_id": project_id,
            }
            if ref_media_ids:
                params["ref_media_ids"] = ref_media_ids
            req_type = "gen_image"

            engine = node_settings.image_engine(node_settings.merged_settings(node))
            if engine == "openai":
                from flowboard.services import openai_images

                # Refusing beats falling back to Flow. A silent fallback
                # would spend Flow credits on a node whose owner explicitly
                # asked for a different engine — the same class of quiet
                # substitution that made imported boards run at Flow's
                # defaults, except this one costs money per node.
                if not openai_images.available():
                    failed_nodes.add(nid)
                    _stamp_node_status(nid, "error", error="openai_image_disabled")
                    continue
                # OpenAI's generations endpoint draws from text alone. The
                # board says "use these photos"; honouring the engine while
                # dropping the references would produce a confident image of
                # the wrong person and charge for it.
                if ref_media_ids:
                    failed_nodes.add(nid)
                    _stamp_node_status(
                        nid, "error", error="openai_image_refs_unsupported"
                    )
                    continue
                params["node_id"] = nid
                req_type = "gen_image_openai"
        else:  # video
            # Before spending anything: did a previous attempt on this node
            # already start renders that never finished? RUN Lỗi and a re-run
            # both arrive here, and a fresh dispatch would bill a second set of
            # clips while the first set sits finished in the project. Re-poll
            # instead. Only unresolved operations qualify — see
            # `_unresolved_video_operations`.
            # What would be dispatched now, so the re-poll can tell "finish the
            # render I asked for" from "I changed my mind". Both reads are pure —
            # `_start_frame_media_id` is a lookup and `for_dispatch` derives from
            # node data — so building this here costs nothing and commits to
            # nothing.
            from flowboard.services import node_settings as _ns

            _settings = _ns.for_dispatch(node)
            repoll_shape = {
                "prompt": prompt,
                "aspect_ratio": (node.data or {}).get("aspectRatio"),
                "start_media_id": _start_frame_media_id(wires, upstream_nodes),
                **{
                    k: _settings.get(k)
                    for k in ("video_quality", "duration_s", "resolution")
                },
            }
            repoll = _unresolved_video_operations(nid, current=repoll_shape)
            if repoll is not None and repoll.get("adopt_media_ids"):
                # A previous attempt finished after the executor stopped waiting.
                # The clip exists and is paid for; claim it rather than dispatch
                # a second one.
                adopted = repoll["adopt_media_ids"]
                first = next((m for m in adopted if isinstance(m, str) and m), None)
                logger.info(
                    "node %s: adopting %d media id(s) a finished request left "
                    "behind instead of dispatching again",
                    nid, sum(1 for m in adopted if m),
                )
                patch: dict[str, Any] = {"mediaIds": adopted}
                if first:
                    patch["mediaId"] = first
                _stamp_node_status(nid, "done", data_patch=patch)
                with get_session() as s:
                    fresh = s.get(Node, nid)
                    if fresh is not None:
                        node_by_id[nid] = fresh
                continue
            if repoll is not None:
                logger.info(
                    "node %s: re-polling %d operation(s) from a previous attempt "
                    "instead of dispatching again",
                    nid, len(repoll["operation_names"]),
                )
                _stamp_node_status(nid, "running")
                request_row_id = _create_request_row(nid, "poll_video", repoll)
                get_worker().enqueue(request_row_id)
                try:
                    settled = await _await_request(
                        request_row_id,
                        timeout_s=request_timeout_s,
                        poll_s=poll_interval_s,
                    )
                except asyncio.TimeoutError:
                    _cancel_if_queued(request_row_id)
                    settled = None
                _settle_generation_node(nid, settled, failed_nodes, node_by_id)
                continue

            start_media_id = _start_frame_media_id(wires, upstream_nodes)

            # Component mode: named characters instead of a start frame.
            # Checked BEFORE dispatch because the one combination Google
            # refuses — an image plus entities — comes back as a bare
            # INVALID_ARGUMENT 13 after the round-trip, naming neither wire.
            from flowboard.services import character_ports

            lane = node_settings.video_quality(node_settings.merged_settings(node))
            character_problem = character_ports.validate(
                wires, lane=lane, has_start_frame=bool(start_media_id)
            )
            if character_problem:
                failed_nodes.add(nid)
                _stamp_node_status(nid, "error", error=character_problem)
                continue
            characters = character_ports.collect(wires)
            # Registered characters travel as entity ids. Flow makes those in
            # its own UI — there is no create call — so what happens here is a
            # lookup, a project check and the packaged tool's own tag rule.
            from flowboard.services import character_store

            entity_plan = character_store.plan_entities(
                character_ports.collect_links(wires),
                project_id=project_id,
                prompt=prompt,
            )
            if entity_plan.refusal:
                failed_nodes.add(nid)
                _stamp_node_status(nid, "error", error=entity_plan.refusal)
                continue
            if entity_plan.unknown:
                failed_nodes.add(nid)
                _stamp_node_status(
                    nid,
                    "error",
                    error="character_unknown:" + ",".join(entity_plan.unknown),
                )
                continue
            for name in entity_plan.skipped_untagged:
                logger.info(
                    "node %s: entity %s not tagged in the prompt, left out", nid, name
                )
            if not characters and not entity_plan.entity_ids and not start_media_id:
                unnamed = [
                    w for w in wires
                    if w.port is None and getattr(w.node, "type", None) == "character"
                ]
                if unnamed:
                    # Drawn by hand before the canvas sent port names. It is
                    # not a start frame (a reference sheet makes a wrong clip)
                    # and not a character socket (no port to read), so the
                    # honest move is to ask rather than bill for a clip with
                    # none of the characters in it.
                    failed_nodes.add(nid)
                    _stamp_node_status(
                        nid, "error", error="character_wire_without_port"
                    )
                    continue
            if (
                not characters
                and not entity_plan.entity_ids
                and character_ports.wired_ports(wires)
            ):
                # Sockets are wired and every one of them is empty. Upstream
                # has already run — topological order guarantees it — so this
                # will not fill in later. Falling through would dispatch a
                # paid text-to-video with none of the characters the board is
                # about, which is the expensive way to find out.
                failed_nodes.add(nid)
                _stamp_node_status(nid, "error", error="character_upstream_empty")
                continue
            if characters or entity_plan.entity_ids:
                params = {
                    "prompt": prompt,
                    "project_id": project_id,
                    "ref_media_ids": [media for _, media in characters],
                }
                if entity_plan.entity_ids:
                    params["ref_entity_ids"] = entity_plan.entity_ids
                req_type = "gen_video_omni"
                params.update(node_settings.for_dispatch(node))
                _stamp_node_status(nid, "running")
                request_row_id = _create_request_row(nid, req_type, params)
                get_worker().enqueue(request_row_id)
                try:
                    settled = await _await_request(
                        request_row_id,
                        timeout_s=request_timeout_s,
                        poll_s=poll_interval_s,
                    )
                except asyncio.TimeoutError:
                    _cancel_if_queued(request_row_id)
                    settled = None
                _settle_generation_node(nid, settled, failed_nodes, node_by_id)
                continue

            if start_media_id:
                params = {
                    "prompt": prompt,
                    "project_id": project_id,
                    "start_media_id": start_media_id,
                }
                req_type = "gen_video"
            elif start_frame_unavailable(node, wires, runtime=True):
                # A start-frame socket IS wired, and nothing will ever fill
                # it. Falling through to text-to-video here would ignore
                # what the user asked for and charge them for the wrong
                # clip — the board says "start from this photo".
                failed_nodes.add(nid)
                _stamp_node_status(nid, "error", error="missing_upstream_image")
                continue
            else:
                # Nothing is wired to a start frame, so text-to-video is
                # what this node means. The worker has always supported it
                # (`gen_video_text`); the canvas simply never asked, so the
                # most basic thing you can build here — a prompt and a video
                # node — failed with `missing_upstream_image`.
                params = {"prompt": prompt, "project_id": project_id}
                req_type = "gen_video_text"

        # The node's own aspect / duration / quality / model. Applied last so
        # a resolved setting wins, and merged rather than assigned so an
        # unresolved one leaves the worker's default alone.
        #
        # Without this the dispatch carried prompt, project and start frame
        # and nothing else, so every imported workflow ran at Flow's
        # defaults — landscape, default duration, default model — however
        # emphatically the template asked for 9:16.
        params.update(node_settings.for_dispatch(node))

        # Look at the still before spending a video credit on it. A clip
        # built from the wrong first frame is wrong too, and the clip is the
        # expensive half.
        if req_type == "gen_video" and _frame_gate_wanted(node):
            blocked = await _frame_gate_block(params, prompt)
            if blocked:
                failed_nodes.add(nid)
                _stamp_node_status(nid, "error", error=blocked)
                continue

        # Stamp running, dispatch. Repeated only when the review loop asks
        # for another round AND the lane costs nothing — see `_review_round`.
        loop = review_loop.settings_from(node_settings.merged_settings(node))
        rounds: list[dict] = []
        settled = None
        for attempt in range(1, loop.max_rounds + 1 if loop.enabled else 2):
            _stamp_node_status(nid, "running")
            request_row_id = _create_request_row(nid, req_type, params)
            get_worker().enqueue(request_row_id)

            try:
                settled = await _await_request(
                    request_row_id,
                    timeout_s=request_timeout_s,
                    poll_s=poll_interval_s,
                )
            except asyncio.TimeoutError:
                _cancel_if_queued(request_row_id)
                settled = None
                break

            if not loop.enabled or node.type != "video" or settled.status != "done":
                break

            revised, entry = await _review_round(
                settled, params, loop, attempt, node
            )
            if entry is not None:
                rounds.append(entry)
            if revised is None:
                break
            params = {**params, "prompt": revised}

        if settled is None:
            failed_nodes.add(nid)
            _stamp_node_status(nid, "error", error="timeout")
            continue

        if settled.status == "done":
            result = settled.result or {}
            media_ids = result.get("media_ids") if isinstance(result.get("media_ids"), list) else []
            media_id = media_ids[0] if media_ids else None
            patch: dict[str, Any] = {"mediaIds": media_ids}
            if media_id:
                patch["mediaId"] = media_id
            if rounds:
                # The whole history, not only the last round: "5.1 then 7.8
                # after rewording" is the useful thing to read, and the
                # revised prompt is what the user will want to keep.
                patch["reviewRounds"] = rounds
                patch["prompt"] = params.get("prompt", prompt)
            _stamp_node_status(nid, "done", data_patch=patch)
            # Refresh in-memory snapshot so downstream nodes see the new mediaId.
            with get_session() as s:
                fresh = s.get(Node, nid)
                if fresh is not None:
                    node_by_id[nid] = fresh
        else:
            failed_nodes.add(nid)
            _stamp_node_status(nid, "error", error=settled.error or "request_failed")

    # Finalise pipeline run + plan.
    final_status = "failed" if failed_nodes else "done"
    with get_session() as s:
        run = s.get(PipelineRun, run_id)
        if run is not None:
            run.status = final_status
            run.finished_at = datetime.now(timezone.utc)
            if failed_nodes:
                run.error = f"failed_nodes:{sorted(failed_nodes)}"
            s.add(run)
        plan2 = s.get(Plan, run.plan_id) if run else None
        if plan2 is not None:
            plan2.status = final_status
            s.add(plan2)
        s.commit()
    logger.info(
        "pipeline run %s: finished status=%s failed=%d",
        run_id, final_status, len(failed_nodes),
    )


def _frame_gate_wanted(node) -> bool:
    """Whether this node asked for its first frame to be checked.

    Off unless asked for, like the review loop: on by default it would spend
    a vision call before every i2v dispatch on every board.
    """
    raw = node_settings.merged_settings(node).get("frame_gate")
    return bool(raw) and str(raw).strip().lower() not in ("false", "0", "no")


async def _frame_gate_block(params: dict, prompt: str) -> Optional[str]:
    """An error string when the start frame should stop the dispatch.

    None means go ahead — including when the check itself could not run. A
    checker that is down must not block generation; the gate exists to save
    a credit, not to hold the pipeline hostage to a third service.

    It does NOT regenerate the image. Images are billable too
    (`estimate.BILLABLE_TYPES`), so an automatic retry here would spend the
    user's credits on something they did not individually approve — the
    same rule the review loop follows. Stopping saves the video credit,
    which is the expensive one, and hands the decision back.
    """
    from flowboard.services import frame_gate
    from flowboard.services import media as media_service

    media_id = str(params.get("start_media_id") or "").strip()
    if not media_id:
        return None
    path = media_service.cached_path(media_id)
    if path is None:
        logger.info("frame gate: %s is not in the cache, skipping the check", media_id)
        return None

    try:
        verdict = await frame_gate.check_frame(path, brief=prompt)
    except frame_gate.GateError as exc:
        logger.info("frame gate: could not check the frame (%s)", exc)
        return None

    if verdict.ok:
        return None
    reason = "; ".join(verdict.reasons[:2]) or verdict.fix or "không đạt"
    return f"frame_gate_rejected: {reason}"[:300]


async def _review_round(settled, params: dict, loop, attempt: int, node):
    """Score the clip that just landed, and decide whether to try again.

    Returns ``(revised prompt or None, the round's log entry or None)``.
    None for the prompt means STOP — the clip passed, the rounds ran out,
    the lane costs credits, or nothing could be reviewed or reworded. In
    every one of those cases the clip that exists is what the node keeps.

    A failure to review is never a failure of the node. The clip is real and
    the user asked for it; losing it because a vision provider was down
    would be the worse outcome by a distance.
    """
    from flowboard.config import STORAGE_DIR
    from flowboard.services import media as media_service
    from flowboard.services import video_review

    result = settled.result or {}
    ids = result.get("media_ids") if isinstance(result.get("media_ids"), list) else []
    if not ids:
        return None, None
    path = media_service.cached_path(str(ids[0]))
    if path is None:
        logger.info("review loop: clip %s is not in the cache, skipping", ids[0])
        return None, None

    try:
        review = await video_review.review_clip(
            path, prompt=str(params.get("prompt") or ""), storage_dir=STORAGE_DIR
        )
    except video_review.ReviewError as exc:
        logger.info("review loop: could not review node %s (%s)", node.id, exc)
        return None, None

    quality = params.get("quality") or node_settings.video_quality(
        node_settings.merged_settings(node)
    )
    # What actually dispatched, not what was asked for. A board set to a
    # 0-credit lane on a tier that has none is served a paid key, and judging
    # the label here is how this loop spent three paid clips per node while
    # believing every round was free.
    dispatched_key = result.get("effective_model_key") or result.get("model_key")
    decision = review_loop.decide(
        round_index=attempt,
        score=review.score,
        verdict=review.verdict,
        settings=loop,
        quality=quality,
        model_key=dispatched_key if isinstance(dispatched_key, str) else None,
        fix_hint=review.fix_hint,
        has_critical=review.has_critical,
    )
    entry = {
        "round": decision.index,
        "score": decision.score,
        "verdict": decision.verdict,
        "accepted": decision.accepted,
        "reason": decision.reason,
        "fixHint": decision.fix_hint,
        "issues": [
            {"severity": i.severity, "timeRange": i.time_range,
             "description": i.description}
            for i in review.issues
        ],
    }
    if not decision.retried:
        return None, entry

    revised = await review_loop.revise_prompt(str(params.get("prompt") or ""), review)
    if revised is None:
        # Retrying with the identical prompt would produce the identical
        # clip and burn the round for nothing.
        entry["reason"] = (
            f"{decision.reason} Nhưng không sửa được prompt (AI không trả lời) "
            "— giữ clip hiện có."
        )
        return None, entry
    entry["revisedPrompt"] = revised
    return revised, entry


#: Upstream types that can supply a still to a start-frame socket.
#:
#: `START_FRAME_PORTS` accepts a nameless wire so hand-drawn boards work, and
#: a nameless wire is all the canvas can draw. That blanket acceptance is what
#: made a `prompt` look like an empty start frame (the simplest board anyone
#: can draw then failed with `missing_upstream_image`) and a `character` look
#: like a filled one (a paid i2v clip generated from a reference sheet). So a
#: nameless wire is read by what is on the other end of it.
_START_FRAME_SOURCE_TYPES: frozenset[str] = frozenset(
    {"image", "visual_asset", "video", *_POSTPROD_NODE_TYPES}
)


def _is_start_frame_wire(wire) -> bool:
    """Whether this wire occupies the start-frame socket."""
    from flowboard.services.postprod_plan import START_FRAME_PORTS

    if wire.port not in START_FRAME_PORTS:
        return False
    if wire.port is None:
        return getattr(wire.node, "type", None) in _START_FRAME_SOURCE_TYPES
    return True


def has_start_frame_wire(upstream) -> bool:
    """Whether anything at all is wired to this node's start-frame socket.

    This is the image-to-video / text-to-video question, and it is NOT the same
    as "is a start frame resolvable yet": a producer upstream that has not run
    is still an image-to-video. The estimate needs the first question to know
    which lanes can serve the node, and asking the second one there made every
    storyboard clip read as text-to-video before its image had been drawn.
    """
    return any(_is_start_frame_wire(w) for w in upstream)


def _start_frame_media_id(wires, upstream_nodes) -> Optional[str]:
    """The still a video generation should start from.

    Whatever media landed on the start-frame socket — not "the first
    upstream node that happens to be of type `image`", which is what this
    used to be and which quietly broke the pattern every packaged workflow
    is built on.

    Those workflows chain scenes as `video → extract_last_frame → video`,
    taking the last frame of one clip as the first frame of the next. An
    `extract_last_frame` node writes a real `mediaId` when it finishes, but
    its type is not `image`, so the next video never saw it and failed with
    `missing_upstream_image`. The same blindness hit a `visual_asset` wired
    straight into `start_frame` — a user's own photo as the opening frame.
    Counted on the imported boards: six video nodes that could never find a
    start frame, four of them the scene-chaining case.

    Falls back to the old type-based scan so a board drawn by hand, whose
    wires carry no port names, keeps working.
    """
    for wire in wires:
        if not _is_start_frame_wire(wire):
            continue
        mid = (wire.node.data or {}).get("mediaId")
        if isinstance(mid, str) and mid.strip():
            return mid.strip()
    for u in upstream_nodes:
        if u.type == "image":
            mid = (u.data or {}).get("mediaId")
            if isinstance(mid, str) and mid.strip():
                return mid.strip()
    return None


#: Node types whose media the USER supplies up front. Wired into a generator
#: with no `mediaId`, nothing in the run will ever fill them — unlike an
#: `image`/`video` upstream, which produces its media while the run proceeds.
UPLOAD_TYPES = ("visual_asset", "character")

#: Node types that produce media during the run. An empty one of these is not
#: missing input — it simply has not run yet.
PRODUCER_TYPES = ("image", "video", *_POSTPROD_NODE_TYPES)


def start_frame_unavailable(node, upstream, *, runtime: bool = False) -> bool:
    """True when a video can never obtain a start frame in this run.

    The estimate's half of `_start_frame_media_id`. The executor refuses
    such a node for free, so this costs nothing either way — but quoting it
    as billable told the user a board would spend credits it cannot spend,
    and the confirmation dialog is worth nothing if its number is fiction.

    A producer upstream counts as satisfied even with no `mediaId` yet:
    that is the whole point of a chain. ``runtime=True`` withdraws that
    credit, because by then the producer has had its turn: the executor
    walks the board in topological order, so a wired producer still holding
    no media has already finished and produced none. Extending it trust it
    has spent means falling through to text-to-video — buying a clip that
    ignores the photo the board was built around, and charging for it.

    False when NOTHING is wired to a start frame — that node is a
    text-to-video, not a broken image-to-video, and the run dispatches it
    as one. The distinction matters: a socket wired to an empty upload says
    "start from this photo" and must not be quietly downgraded, while no
    socket at all says "make a video from this prompt".
    """
    if node.type != "video":
        return False
    wired = [w for w in upstream if _is_start_frame_wire(w)]
    if not wired:
        return False
    for wire in wired:
        if wire.node.type in PRODUCER_TYPES and not runtime:
            return False
        media = (wire.node.data or {}).get("mediaId")
        if isinstance(media, str) and media.strip():
            return False
    return True


def missing_upload_ports(node, upstream) -> list[str]:
    """Upload sockets this node needs and the user never filled in.

    Exists because of a real, costed asymmetry found on 2026-09-02 while
    preparing the first pipeline run. A `video` node with no start frame
    refuses to dispatch and spends nothing. An `image` node in the same
    position simply dropped `ref_media_ids` from its params and dispatched
    anyway.

    On the packaged "Thời Trang Nữ" workflow that meant a virtual try-on —
    whose prompt reads "use image **nguoi_mau** as the base and **san_pham**
    as the garment reference" — would have run with neither photo, spent
    credits, and returned an image unrelated to the request. The cost
    preview called that board `notReadyJobs: 0`, because readiness was
    decided by prompt presence alone.

    Scoped to `image` because that is the node type that consumes uploads as
    references. The first version of this reported EVERY empty upload wired
    into any generator, and the first real board it met proved that too
    broad: a `video` fed by a filled `visual_asset` AND an empty `character`
    placeholder was declared not-ready, when a video never reads character
    refs at all — only its start frame, which was present. A video's start
    frame has its own check, `_start_frame_media_id`, which is both more
    precise and already free of charge.

    Reports only when NOT ONE upload resolved. With some references present
    the generation is a judgement call about how many the prompt needs, and
    the graph cannot answer that; with none present it is unambiguous.

    Generator upstreams are never reported: an `image` feeding a `video`'s
    `start_frame` has no `mediaId` before the run and a real one after it,
    so treating that as missing would refuse to run the very chains these
    workflows are built from.
    """
    if node.type != "image":
        return []
    wired = [w for w in upstream if w.node.type in UPLOAD_TYPES]
    if not wired:
        return []
    missing: list[str] = []
    for wire in wired:
        media = (wire.node.data or {}).get("mediaId")
        if isinstance(media, str) and media.strip():
            return []  # at least one reference resolved
        missing.append(wire.port or "?")
    return missing


#: Rules that stop a dispatch. The rest are logged.
#:
#: These two are the ones the generator or the filter charges for: a tag in
#: spoken text is read aloud in audio the user has paid for, and a banned name
#: in a visual prompt comes back INVALID_ARGUMENT after the request is sent.
#: The others (`unknown_tag`, `too_many_characters`, `bad_duration`) depend on
#: a cast list and a lane this function has to infer, and a wrong inference
#: blocking a board someone can actually run is worse than the round trip.
_BLOCKING_RULES: frozenset[str] = frozenset({"tag_in_dialogue", "banned_name"})


def _prompt_rule_error(node, wires, prompt: str) -> Optional[str]:
    """``"prompt_rule:<rule>"`` when this prompt must not be dispatched.

    Costs nothing: no model call, and the rule file is read once per process.
    """
    from flowboard.services import prompt_checks

    cast = [
        title
        for wire in wires
        if getattr(wire.node, "type", None) == "character"
        for title in [(wire.node.data or {}).get("title")]
        if isinstance(title, str) and title.strip()
    ]
    try:
        findings = prompt_checks.check(prompt, cast=cast or None)
    except Exception:  # pragma: no cover — a check must never fail a run
        logger.warning("prompt_checks raised; dispatching anyway", exc_info=True)
        return None

    blocking = [
        f for f in findings
        if f.severity == "error" and f.rule in _BLOCKING_RULES
    ]
    for finding in findings:
        if finding not in blocking:
            logger.info(
                "node %s prompt note [%s] %s", node.id, finding.rule, finding.message
            )
    if not blocking:
        return None
    logger.warning(
        "node %s refused before dispatch: %s",
        node.id, "; ".join(f.message for f in blocking),
    )
    return "prompt_rule:" + ",".join(sorted({f.rule for f in blocking}))


def _prompt_for(node, upstream) -> Optional[str]:
    """The text this generation node should run with.

    Its own `data.prompt` first — that is what a node typed into the canvas
    carries. Failing that, an upstream `prompt` node's text: in every one of
    the packaged workflows the prompt arrives over a WIRE, and reading only
    the node's own field meant an imported board dispatched nothing at all
    while reporting success.

    Read by PORT, the same way `postprod_plan` reads its inputs, and using
    that module's own port list so there is one definition rather than two.
    Two upstream resolvers with two different conventions is precisely how
    the planner and the handler ended up speaking different languages.

    Only `prompt` nodes count. A character or image upstream is a reference,
    not a script — and a prompt wired to some other socket (an `edit_video`
    title, say) is not this node's prompt either.
    """
    from flowboard.services.postprod_plan import TEXT_PORTS, branch_text

    own = (node.data or {}).get("prompt")
    if isinstance(own, str) and own.strip():
        return own.strip()
    for wire in upstream:
        if wire.port not in TEXT_PORTS or wire.node.type != "prompt":
            continue
        # `branch_text` owns the order: branch field, then what the node's own
        # model wrote, then the plain text. Written out a second time here, the
        # two copies disagreed about the middle one.
        text = branch_text(wire.node, wire.port)
        if text:
            return text
    return None


def _upstream_with_ports(node_id: int, edges, node_by_id: dict) -> list:
    """Every wire into this node, paired with the socket it landed on.

    Edges drawn by hand on the canvas carry no port; they arrive as None and
    the planner treats them as the node's default input.
    """
    from flowboard.services.postprod_plan import Upstream

    out = []
    for e in edges:
        if e.target_id != node_id:
            continue
        source = node_by_id.get(e.source_id)
        if source is not None:
            out.append(Upstream(source, e.target_port))
    return out


#: `sourceType` values that mean "this prompt node asks a model at run time".
#: An imported `gemini_prompt` node is the only one today; `promptMode` on the
#: node is the canvas's own way of saying the same thing.
_LLM_PROMPT_SOURCE_TYPES: frozenset[str] = frozenset({"gemini_prompt"})


def _is_llm_prompt_node(node) -> bool:
    data = node.data or {}
    if data.get("promptMode") == "gemini":
        return True
    return str(data.get("sourceType") or "") in _LLM_PROMPT_SOURCE_TYPES


async def _run_prompt_node(node, upstream, node_by_id: dict) -> bool:
    """Compose this prompt node's texts with a model. True unless it failed.

    Only for nodes that ASK for it — a hand-typed prompt node is a text the
    user wrote and running a model over it would rewrite their words.

    Never fails the board: a prompt node that could not reach a provider
    leaves its instruction in place, which is exactly the behaviour every run
    had before this existed. Downstream nodes then refuse for their own
    reasons, and those reasons are the honest ones.
    """
    if not _is_llm_prompt_node(node):
        return True

    from flowboard.services import postprod_plan
    from flowboard.services.llm import run_llm_chain
    from flowboard.services.llm.base import LLMError

    nid = node.id
    data = node.data or {}
    instruction = str(data.get("prompt") or "").strip()
    brief = postprod_plan._text_on(upstream, postprod_plan.TEXT_PORTS) or ""
    if not instruction and not brief:
        return True

    _stamp_node_status(nid, "running")
    ask = brief or "Không có mô tả nào từ node phía trước — làm theo hướng dẫn trên."
    try:
        answer, provider = await run_llm_chain(
            "script_writer", ask, system_prompt=instruction or None, timeout=180.0
        )
    except LLMError as exc:
        logger.warning("node %s could not compose: %s", nid, exc)
        _stamp_node_status(nid, "error", error=f"prompt_compose_failed:{exc}"[:200])
        return False

    from flowboard.services import video_analysis

    parsed = video_analysis.parse(answer)
    patch: dict = {"composedPrompt": (parsed.description or answer).strip()}
    patch.update(video_analysis.branch_texts(parsed))
    patch["composedBy"] = provider
    _stamp_node_status(nid, "done", data_patch=patch)
    with get_session() as s:
        fresh = s.get(Node, nid)
        if fresh is not None:
            node_by_id[nid] = fresh
    return True


async def _run_analysis_node(node, upstream, node_by_id: dict) -> bool:
    """Read the upstream clip with the pinned vision model, write the texts."""
    from flowboard.services import media as media_service
    from flowboard.services import postprod_plan, video_analysis

    nid = node.id
    media_id = postprod_plan._video_in(upstream)
    if not media_id or media_id == postprod_plan.PENDING:
        logger.info("pipeline: node %s (analyze_video) has no clip yet", nid)
        return True

    source = media_service.cached_path(media_id)
    if source is None:
        _stamp_node_status(nid, "error", error="analyze_video_media_missing")
        return False

    settings = postprod_plan._settings_of(node)
    _stamp_node_status(nid, "running")
    try:
        result = await video_analysis.analyze(
            source,
            mode=str(settings.get("analysis_mode") or ""),
            style=str(settings.get("style") or ""),
            voice=str(settings.get("voice") or ""),
            custom=str(settings.get("custom_voice_prompt") or ""),
            scene_count=_locked_scene_count(settings),
        )
    except video_analysis.AnalysisError as exc:
        _stamp_node_status(nid, "error", error=f"analyze_video_failed:{exc}"[:200])
        return False

    # The node's own text is the description; the three branch fields are what
    # the sockets carry. Written together, because a consumer reading one of
    # them while another is stale would pair scene 3's dialogue with scene 1's
    # picture.
    patch: dict = {
        "prompt": result.description or result.prompt,
        "analysisMode": result.mode,
        "script": result.script,
    }
    if result.thumbnail_prompt:
        patch["thumbnailPrompt"] = result.thumbnail_prompt
    patch.update(video_analysis.branch_texts(result))
    _stamp_node_status(nid, "done", data_patch=patch)
    with get_session() as s:
        fresh = s.get(Node, nid)
        if fresh is not None:
            node_by_id[nid] = fresh
    return True


def _locked_scene_count(settings: dict) -> Optional[int]:
    """`lock_scene_count` pins the breakdown to a number of scenes.

    The packaged tool writes it as the STRING "False" about as often as a
    boolean, and a scene count of zero is not a count — both read as "let the
    model choose".
    """
    from flowboard.services.postprod_plan import _number, _truthy

    if not _truthy(settings.get("lock_scene_count")):
        return None
    count = _number(settings.get("scene_count"))
    if count is None or count < 1:
        return None
    return int(min(40, count))


async def _run_postprod_node(
    node,
    upstream_nodes,
    *,
    node_by_id: dict,
    request_timeout_s: float,
    poll_interval_s: float,
) -> bool:
    """Run one post-production node's ops in order. True unless it failed.

    A node can be several passes over the same clip (``edit_video`` is), so
    the ops run as a chain with each pass consuming the previous one's
    output. Only the last output lands on the node: the intermediates are
    working files, and showing them on the card would make a four-pass edit
    look like four results.

    A node with nothing to do returns True and stays idle — same as a
    generation node with no prompt. That lets a half-wired board run the
    parts that are ready instead of failing whole.
    """
    from flowboard.services import postprod_plan

    nid = node.id
    ops = postprod_plan.ops_for(node, upstream_nodes)
    if not ops:
        logger.info("pipeline: node %s (%s) has nothing to run", nid, node.type)
        return True

    _stamp_node_status(nid, "running")
    # Every step's output, not just the last: a pass can need two earlier
    # results. The subtitle pair is the case — `subtitles` takes the SRT
    # `transcribe` produced AND the clip that went into it, and "previous"
    # can only name one of them.
    outputs: list[str] = []

    for step, op in enumerate(ops, start=1):
        params, err = _resolve_step_refs(op, outputs)
        if err is not None:
            _stamp_node_status(nid, "error", error=f"{err}_step_{step}")
            return False
        request_row_id = _create_request_row(nid, "postprod", params)
        get_worker().enqueue(request_row_id)
        try:
            settled = await _await_request(
                request_row_id,
                timeout_s=request_timeout_s,
                poll_s=poll_interval_s,
            )
        except asyncio.TimeoutError:
            _cancel_if_queued(request_row_id)
            _stamp_node_status(nid, "error", error=f"timeout_step_{step}")
            return False

        if settled.status != "done":
            _stamp_node_status(
                nid, "error", error=settled.error or f"postprod_failed_step_{step}"
            )
            return False

        result = settled.result or {}
        media_ids = result.get("media_ids")
        if not isinstance(media_ids, list) or not media_ids:
            _stamp_node_status(nid, "error", error=f"no_output_step_{step}")
            return False
        outputs.append(media_ids[0])

    # Only the LAST output lands on the node. The intermediates — a
    # de-logo'd copy, an SRT — are working files; showing them would make a
    # four-pass edit look like four results.
    final = outputs[-1]
    _stamp_node_status(
        nid, "done", data_patch={"mediaId": final, "mediaIds": [final]}
    )
    with get_session() as s:
        fresh = s.get(Node, nid)
        if fresh is not None:
            node_by_id[nid] = fresh
    return True


def _resolve_step_refs(op: dict, outputs: list[str]) -> tuple[dict, Optional[str]]:
    """Swap a planned op's placeholders for the media ids they refer to.

    Two forms, and the second exists because the first cannot express a pass
    that needs two earlier results:

    * ``PREVIOUS`` — the step immediately before.
    * ``__step_N__`` — the N-th step's output, 1-based.

    Returns ``(params, None)`` or ``(_, reason)``. A reference that cannot
    be resolved is a hard stop: dispatching with the placeholder still in
    place would hand ffmpeg a media id that does not exist, and dispatching
    with ``None`` is worse — some ops would read it as "not supplied" and
    fail with a misleading message.
    """
    from flowboard.services import postprod_plan

    params: dict = {}
    for key, value in op.items():
        if value == postprod_plan.PREVIOUS:
            if not outputs:
                return {}, "no_previous_output"
            params[key] = outputs[-1]
            continue
        if isinstance(value, str) and value.startswith(postprod_plan.STEP_PREFIX):
            try:
                index = int(value[len(postprod_plan.STEP_PREFIX):].rstrip("_"))
            except ValueError:
                return {}, "bad_step_reference"
            if index < 1 or index > len(outputs):
                return {}, "step_reference_out_of_range"
            params[key] = outputs[index - 1]
            continue
        params[key] = value
    return params, None


def _topo_sort(node_ids: Iterable[int], incoming: dict[int, list[int]]) -> list[int]:
    """Kahn's algorithm. Cycles get appended at the end (best-effort)."""
    pending = list(node_ids)
    in_count = {nid: len(incoming.get(nid, ())) for nid in pending}
    ready = [nid for nid, c in in_count.items() if c == 0]
    out: list[int] = []
    seen: set[int] = set()
    # Track forward edges via the inverse of incoming.
    forward: dict[int, list[int]] = defaultdict(list)
    for tgt, srcs in incoming.items():
        for s in srcs:
            forward[s].append(tgt)
    while ready:
        nid = ready.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        out.append(nid)
        for child in forward.get(nid, ()):
            in_count[child] -= 1
            if in_count[child] <= 0:
                ready.append(child)
    # Any leftover (cycle) gets appended.
    for nid in pending:
        if nid not in seen:
            out.append(nid)
    return out


def _project_id_for_board(board_id: int) -> Optional[str]:
    from flowboard.db.models import BoardFlowProject  # local import to avoid cycle

    with get_session() as s:
        row = s.get(BoardFlowProject, board_id)
        return row.flow_project_id if row is not None else None


#: Request kinds that start a render at Google and name operations for it.
_VIDEO_DISPATCH_TYPES = ("gen_video", "gen_video_text", "gen_video_omni", "poll_video")

#: Statuses that mean "we stopped waiting", as opposed to "Google refused it".
#: Only these make a row worth re-polling: a content-filter rejection is terminal
#: per operation, and re-polling one would replace a clear refusal with a
#: ten-minute wait for the same answer.
#:
#: Read from the STATUS column, and only from it. There used to be a parallel
#: tuple of error strings whose first entry — `"timeout"` — matched nothing,
#: because the worker writes `req.error = "timeout_waiting_video"` and puts
#: `"timeout"` in the status. Correcting that entry then showed the whole tuple
#: was redundant: every site that writes one of those errors sets the matching
#: status in the same breath (`processor.py` timeout/timeout_waiting_video,
#: `routes/plans.py` canceled/stopped_by_user, `routes/requests.py`
#: canceled/canceled). Two columns carrying one fact is how the dead entry hid
#: for so long, so this keeps the column that is always written.
_GAVE_UP_STATUSES = ("canceled", "timeout")


#: Params that decide WHAT a video dispatch renders, as opposed to where it is
#: filed. `project_id` and `paygate_tier` are deliberately absent: neither
#: changes the clip, and treating them as part of the identity would re-dispatch
#: a render the user already paid for.
_RENDER_DEFINING_PARAMS = (
    "prompt",
    "video_quality",
    "duration_s",
    "resolution",
    "aspect_ratio",
    "start_media_id",
    "end_media_id",
)


def _describes_same_render(stored: dict[str, Any], current: dict[str, Any]) -> bool:
    """Whether a finished attempt rendered what is being asked for now.

    Compared conservatively, and the asymmetry is the point: a field absent from
    the stored row is NOT treated as a change. A spurious "changed" verdict
    dispatches a second render and bills for it, which is the failure this whole
    branch exists to prevent — so only a field both sides state, and state
    differently, counts.

    Prompt is normalised because trailing whitespace is not a different clip.
    """
    for key in _RENDER_DEFINING_PARAMS:
        was, now = stored.get(key), current.get(key)
        if key == "prompt":
            was = (was or "").strip() or None
            now = (now or "").strip() or None
        if was is None or now is None:
            continue
        if was != now:
            return False
    return True


def _node_already_has(node_id: int, media_ids: list[Optional[str]]) -> bool:
    """Whether this node already carries the media a finished row holds.

    The question that separates "a paid clip was orphaned" from "the user pressed
    run again". Both look identical on the request row; only the node says which.
    """
    with get_session() as s:
        node = s.get(Node, node_id)
        data = (node.data if node is not None else None) or {}
    on_node = {
        m for m in (data.get("mediaIds") or []) if isinstance(m, str) and m
    }
    if isinstance(data.get("mediaId"), str) and data["mediaId"]:
        on_node.add(data["mediaId"])
    return all(m in on_node for m in media_ids if isinstance(m, str) and m)


def _unresolved_video_operations(
    node_id: int, *, current: dict[str, Any]
) -> Optional[dict[str, Any]]:
    """Operations a previous attempt on this node left unfinished, or None.

    Returns dispatch params for a `poll_video` request: the operation names with
    no media and no terminal error, plus the workflow records for any of them
    that were text-to-video submits.

    This is the money rule turned around. It used to be "never re-dispatch a
    result that names operations", because each name is a render already
    charged for — but that left the clip stranded. On the batch path a finished
    media id can be found again through the project listing, so the operations
    are re-polled instead and nothing is billed twice.

    ``current`` is what would be dispatched now, and it is the half that was
    missing. Nothing compared it: this function keyed on `node_id` and read only
    `row.result`, so editing a node's prompt and pressing RUN Lỗi handed back the
    OLD render under the new text — and if that operation never finished at
    Google, the edit could never be dispatched at all, because every press
    re-polled the same dead operation for the full poll budget. There is no
    force-dispatch anywhere, so the only escape was deleting the node.

    Not covered, and deliberately so: reference images and character entities.
    Resolving those has side effects (validation, cross-project media sync) that
    belong after this branch, and a wire-only change still re-polls. Narrower
    than ignoring every parameter, and stated here rather than left to be found.
    """
    with get_session() as s:
        rows = list(
            s.exec(
                select(Request)
                .where(Request.node_id == node_id)
                .where(Request.type.in_(_VIDEO_DISPATCH_TYPES))  # type: ignore[attr-defined]
                .order_by(Request.id.desc())  # type: ignore[attr-defined]
            ).all()
        )
    for row in rows:
        result = row.result or {}
        names = [
            n for n in (result.get("operation_names") or [])
            if isinstance(n, str) and n
        ]
        if not names:
            continue
        if not _describes_same_render(row.params or {}, current):
            # The node was edited since this attempt. Its operations render the
            # old request, so re-polling them would answer a question nobody
            # asked — silently, because the card keeps the new prompt beside the
            # old clip. Fall through and dispatch what is being asked for now.
            logger.info(
                "node %s: previous attempt rendered a different request — "
                "dispatching instead of re-polling", node_id,
            )
            return None
        # Which slots are still open. `media_ids` is positional against
        # `operation_names`, so slot i tells us whether operation i landed.
        media_ids = result.get("media_ids")
        media_ids = media_ids if isinstance(media_ids, list) else []
        op_errors = result.get("op_errors")
        op_errors = op_errors if isinstance(op_errors, dict) else {}
        gave_up = row.status in _GAVE_UP_STATUSES
        open_names: list[str] = []
        open_slots: list[int] = []
        for index, name in enumerate(names):
            landed = index < len(media_ids) and isinstance(media_ids[index], str)
            if landed:
                continue
            reason = op_errors.get(name)
            if isinstance(reason, str) and reason and reason != "timeout_waiting_video":
                # Google said no. Another poll cannot change that.
                continue
            if reason == "timeout_waiting_video" or gave_up or not op_errors:
                open_names.append(name)
                open_slots.append(index)
        if not open_names:
            # Fully resolved. Before giving up on it: did this attempt finish
            # media the node never received?
            #
            # The worker writes media onto the Request row; the only code that
            # copies it onto a node is the executor, and by now it has stamped the
            # node `error` and moved on. So a clip that landed after the executor
            # stopped waiting — or after Stop, where the worker keeps polling to
            # completion while the executor is cancelled outright — sat finished
            # and paid for on a row nobody read, while this function reported
            # "nothing to resume" and authorised a fresh dispatch. That was the
            # second charge. Adopt it instead of buying it twice.
            landed = [
                m if isinstance(m, str) else None
                for m in (list(media_ids) + [None] * len(names))[:len(names)]
            ]
            # Only when the node is actually missing it. Without this check,
            # adoption would also fire on a node the user asked to regenerate:
            # the media is on both the row and the node, and handing back the old
            # clip would make a deliberate re-run impossible. Adoption repairs an
            # orphan; it does not stand in for running.
            if any(landed) and not _node_already_has(node_id, landed):
                return {
                    "adopt_media_ids": landed,
                    "model_key": result.get("model_key"),
                }
            # Nothing to adopt. Stop at the newest resolved row rather than
            # walking further back: an older attempt's operations belong to a
            # render this node has since superseded.
            return None
        workflows = [
            w for w in (result.get("workflows") or [])
            if isinstance(w, dict) and w.get("name") in set(open_names)
        ]
        if not workflows:
            raw = (result.get("raw_dispatch") or {}) if isinstance(
                result.get("raw_dispatch"), dict
            ) else {}
            workflows = [
                w for w in (raw.get("workflows") or [])
                if isinstance(w, dict) and w.get("name") in set(open_names)
            ]
        return {
            "operation_names": open_names,
            "workflows": workflows or None,
            "model_key": result.get("model_key"),
            # Where these operations sit in the ORIGINAL batch, and what already
            # landed beside them. The poll returns results positional over the
            # open subset, and the settle writes `mediaIds` wholesale — so
            # without this a 3-variant batch with slot 2 finished came back as
            # `[None, "media-B"]`: the paid clip in slot 2 dropped off the node
            # and slots no longer matched their source images.
            "open_slots": open_slots,
            "landed_media_ids": [
                m if isinstance(m, str) else None
                for m in (list(media_ids) + [None] * len(names))[:len(names)]
            ],
            # The project those operations were created in. `_op_projects` lives
            # only in memory and is filled at dispatch, so an agent restart loses
            # it — and with no project id the poll never consults the listing and
            # reports pending until the budget runs out. That made "the run died, I
            # restarted the agent, press RUN Lỗi" a ten-minute no-op on a clip
            # already paid for. Read from the ROW, not the board: Flow scopes media
            # ids to the project they were made in, and a board can be re-bound.
            "project_id": (row.params or {}).get("project_id"),
        }
    return None


def _settle_generation_node(nid, settled, failed_nodes, node_by_id) -> None:
    """Write the outcome of one dispatch onto its node.

    Shared by the ordinary Veo path and the component (OMNI) path so the two
    cannot drift: a second copy of "what done looks like" is how one of them
    ends up not refreshing the in-memory snapshot and every downstream node
    then reads a stale mediaId.
    """
    if settled is None:
        failed_nodes.add(nid)
        _stamp_node_status(nid, "error", error="timeout")
        return
    if settled.status == "canceled":
        # Not a failure to report as one: the user pressed Stop, or the
        # executor took the row off a queue it had stopped waiting for.
        failed_nodes.add(nid)
        _stamp_node_status(nid, "error", error=settled.error or "canceled")
        return
    if settled.status != "done":
        failed_nodes.add(nid)
        _stamp_node_status(nid, "error", error=settled.error or "request_failed")
        return

    result = settled.result or {}
    raw = result.get("media_ids")
    media_ids = raw if isinstance(raw, list) else []
    # `media_ids` can hold a None in slot 0 — a 3-variant batch where the first
    # clip failed and the others landed. `if media_ids:` is true for that list, so
    # this wrote `mediaId = None` while reporting the node `done`; downstream
    # readers require a `str` and failed with `missing_upstream_image` against a
    # node showing success. The Veo path beside it guards on the VALUE, and the
    # docstring above says this helper exists so the two cannot drift.
    media_id = media_ids[0] if media_ids else None
    patch: dict[str, Any] = {"mediaIds": media_ids}
    if media_id:
        patch["mediaId"] = media_id
    # Keep the operation ids and the model that produced them. Both were being
    # thrown away, and both are needed AFTERWARDS: an upscale addresses the
    # operation in slot 0 and the media in slot 4 (they are different ids, and
    # the capture warns that swapping them is accepted and then fails), and an
    # extension has to know whether the source was Omni, because Flow only
    # extends Veo clips. Paired by slot with `mediaIds`, the way the dispatch
    # returned them.
    raw_ops = result.get("operation_names")
    if isinstance(raw_ops, list) and any(isinstance(o, str) and o for o in raw_ops):
        patch["operationNames"] = raw_ops
    source_model = result.get("model_key")
    if isinstance(source_model, str) and source_model:
        patch["sourceModelKey"] = source_model
    _stamp_node_status(nid, "done", data_patch=patch)
    # Refresh the snapshot so downstream nodes see the new mediaId.
    with get_session() as s:
        fresh = s.get(Node, nid)
        if fresh is not None:
            node_by_id[nid] = fresh


def _stamp_node_status(
    node_id: int,
    status: str,
    *,
    error: Optional[str] = None,
    data_patch: Optional[dict] = None,
) -> None:
    with get_session() as s:
        n = s.get(Node, node_id)
        if n is None:
            return
        n.status = status
        merged = dict(n.data or {})
        if data_patch:
            merged.update(data_patch)
        if error:
            merged["error"] = error
        n.data = merged
        s.add(n)
        s.commit()


def _create_request_row(node_id: int, req_type: str, params: dict) -> int:
    with get_session() as s:
        req = Request(
            node_id=node_id,
            type=req_type,
            params=dict(params),
            status="queued",
        )
        s.add(req)
        s.commit()
        s.refresh(req)
        assert req.id is not None
        return req.id


#: Statuses a request never leaves. `canceled` belongs here even though the
#: executor does not cause it: the user pressing Stop, or Clear queue, flips
#: rows to `canceled`, and without this the executor sat out its whole
#: timeout waiting for a row nobody was going to run — 722 seconds per node,
#: for a board the user already stopped.
_TERMINAL_REQUEST_STATUSES = ("done", "failed", "canceled")


async def _await_request(
    request_id: int,
    *,
    timeout_s: float,
    poll_s: float,
) -> Request:
    elapsed = 0.0
    while elapsed < timeout_s:
        await asyncio.sleep(poll_s)
        elapsed += poll_s
        with get_session() as s:
            row = s.get(Request, request_id)
            if row is None:
                raise RuntimeError(f"request {request_id} disappeared")
            if row.status in _TERMINAL_REQUEST_STATUSES:
                return row
    raise asyncio.TimeoutError()


def _cancel_if_queued(request_id: int) -> bool:
    """Take a request off the queue when the executor stops waiting for it.

    The executor giving up is not the worker giving up. Without this the row
    stays `queued`, and whatever unblocked the queue later — resume, the 403
    breaker's probe, the extension bridge reconnecting — dispatches it: Flow
    charges, and the media lands on the request row with no executor left to
    copy it onto the node. The user sees a failed node, presses RUN Lỗi, and
    pays a second time for the clip they already own.

    Only `queued` rows are touched. A `running` row is a call already in
    flight with Google; cancelling that locally would not stop the charge and
    would throw away the result, which is the bargain `routes/plans.stop_board`
    already refuses to make.
    """
    with get_session() as s:
        row = s.get(Request, request_id)
        if row is None or row.status != "queued":
            return False
        # "canceled", one L — the spelling the worker re-checks before it
        # picks a row up and before its final stamp.
        row.status = "canceled"
        row.error = "executor_timeout"
        s.add(row)
        s.commit()
    logger.info("request %s canceled: executor stopped waiting", request_id)
    return True
