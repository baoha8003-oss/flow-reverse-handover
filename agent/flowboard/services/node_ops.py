"""The node mutations that more than one caller needs.

Both of these lived inline in `routes.nodes` and both are needed by a second
caller now — the canvas agent's apply and undo. Two copies of a merge rule is
two rules: the sentinel that deletes a key, or the detach-before-delete order
that keeps the activity feed alive, would drift in one copy and the symptom
would be a node that reappears after reload.

None of these commit. That is deliberate — apply writes several nodes in
one transaction, and a service that commits per node would leave a half-built
board behind on the third failure. It is also a known footgun in this
codebase: `materialize_plan` says "caller commits" and a route once forgot,
answering "8 nodes" for an empty board. So the routes that call these have a
test that the rows actually survive a fresh session.
"""
from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session, select

from flowboard.db.models import Asset, Edge, Node, Request


def merge_node_data(existing: Optional[dict], patch: dict) -> dict:
    """Shallow-merge a `data` patch, with `None` meaning "delete this key".

    Shallow on purpose: every field of the node's data is a scalar or a list,
    so one level is the whole schema. Replacing the column wholesale — the
    original behaviour — dropped any sibling the caller did not resend, which
    silently erased `aspectRatio`, `imageEngine` and friends on every partial
    update from the canvas.

    The `None` sentinel is what lets a caller clear a field: `{"aiBrief": None}`
    removes it, while an absent key is left alone. Without the distinction
    "stop showing this" and "do not touch this" are the same request.
    """
    merged = dict(existing or {})
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


def delete_node_cascade(s: Session, node: Node) -> dict[str, Any]:
    """Remove a node and everything the graph owns, detaching what it does not.

    Edges belong to the graph, so they go. `Request` and `Asset` rows are
    history — the activity feed and the media cache — and their `node_id` is
    nullable precisely so they can outlive the node. Detaching rather than
    deleting keeps two things working: the spend record for a generation the
    user paid for, and any saved reference still pointing at that media.

    The detach has to happen FIRST. Skipping it raised a FOREIGN KEY failure
    that aborted the whole transaction, so the canvas showed the node gone
    (optimistic update) while the backend had kept it — the node came back on
    reload with no error anywhere.

    Does not commit. See the module docstring.
    """
    node_id = node.id
    requests = list(s.exec(select(Request).where(Request.node_id == node_id)).all())
    for r in requests:
        r.node_id = None
        s.add(r)
    assets = list(s.exec(select(Asset).where(Asset.node_id == node_id)).all())
    for a in assets:
        a.node_id = None
        s.add(a)
    edges = list(
        s.exec(
            select(Edge).where(
                (Edge.source_id == node_id) | (Edge.target_id == node_id)
            )
        ).all()
    )
    edge_ids = [e.id for e in edges]
    for e in edges:
        s.delete(e)
    s.delete(node)
    return {
        "ok": True,
        "deleted_edges": edge_ids,
        "detached_requests": len(requests),
        "detached_assets": len(assets),
    }


def backfill_operation_ids(s: Session) -> dict[str, int]:
    """Copy `operation_names` / `model_key` from finished renders onto their nodes.

    `_settle_generation_node` only started recording these two at P16, so every
    clip rendered before then has a node that knows what it produced and not what
    produced it. Both facts were kept all along — on the `Request` row, in
    `result` — they were simply never carried across.

    This is not tidying. Two capabilities read them:

    * an upscale addresses the OPERATION and the MEDIA in different payload
      slots, and the capture warns that swapping them is accepted and then fails
      NOT_FOUND. Without the operation id the button is correctly disabled, so
      every clip predating P16 is un-upscalable — and the user already paid for
      those clips;
    * an extension refuses an Omni source, because Flow only extends Veo. It
      decides from the model key. With the key ABSENT the refusal cannot fire:
      an Omni clip reads as "not Omni" and the button offers an extension Flow
      will not serve — a round trip at best, a billed failure at worst. So this
      backfill closes a money hole rather than opening a feature.

    Idempotent, and cheap: a node that already carries `operationNames` is
    skipped, so it costs one query per start-up and nothing after the first run.
    Newest finished request per node wins — a node re-run twice should describe
    its LATEST clip, not whichever row happened to come back first.

    Does not commit; the caller does.
    """
    counts = {"scanned": 0, "patched": 0}
    #: Every request type that ends in a video the two capabilities act on.
    #: `poll_video` included on purpose: a clip recovered by re-polling is as
    #: real as one from the first dispatch, and its row carries the same ids.
    kinds = ("gen_video", "gen_video_text", "gen_video_omni", "poll_video")
    rows = s.exec(
        select(Request)
        .where(Request.status == "done")
        .where(Request.type.in_(kinds))  # type: ignore[attr-defined]
        .order_by(Request.id)
    ).all()

    # Later rows overwrite earlier ones, so the newest finished render wins.
    best: dict[int, Request] = {}
    for row in rows:
        if isinstance(row.node_id, int):
            best[row.node_id] = row

    for node_id, row in best.items():
        node = s.get(Node, node_id)
        if node is None:
            continue
        data = dict(node.data or {})
        if data.get("operationNames") and data.get("sourceModelKey"):
            continue
        counts["scanned"] += 1
        result = row.result if isinstance(row.result, dict) else {}
        patch: dict[str, Any] = {}
        ops = result.get("operation_names")
        if (
            not data.get("operationNames")
            and isinstance(ops, list)
            and any(isinstance(o, str) and o for o in ops)
        ):
            patch["operationNames"] = ops
        key = result.get("model_key")
        if not data.get("sourceModelKey") and isinstance(key, str) and key:
            patch["sourceModelKey"] = key
        if not patch:
            continue
        node.data = merge_node_data(node.data, patch)
        s.add(node)
        counts["patched"] += 1
    return counts
