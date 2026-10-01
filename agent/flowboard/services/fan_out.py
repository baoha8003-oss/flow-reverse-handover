"""One prompt node with many lines, turned into many generation nodes.

The packaged tool has a `prompt_list` node: you paste twenty lines and it
produces twenty videos. Here the same thing was possible only by hand —
duplicating the image node twenty times and pasting a line into each.

**This is an explicit action, not something the run does.** That is the whole
design decision. Fanning out during execution would be less clicking, and it
would make the cost dialog wrong: the user approves "3 billable calls",
the executor discovers seventeen more lines, and the bill is what it is. So
the nodes exist on the canvas *before* anything is estimated or dispatched,
and what the dialog quotes is what happens.

Idempotent by construction. Each clone remembers which prompt node it came
from and which line it holds, so running the action again after editing the
list updates, adds and removes rather than producing a second set beside the
first. A canvas that doubles every time you press the button is not a canvas
anyone keeps using.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlmodel import select

from flowboard.db.models import Asset, Edge, Node, Request
from flowboard.short_id import generate_unique_short_id

logger = logging.getLogger(__name__)

#: Guard against a paste of a whole script becoming three hundred billable
#: nodes. The packaged tool's own batches sit well under this.
MAX_FANOUT = 50

#: Horizontal gap between the template node and its first clone, and the
#: vertical gap between clones. Matches the canvas's own default node size.
_COL_GAP = 320.0
_ROW_GAP = 220.0

#: Node data that belongs to ONE generation, never to a copy of the recipe.
_RUN_ONLY_KEYS = frozenset({
    "mediaId", "mediaIds", "slotErrors", "status", "error", "renderedAt",
    "thumbnailUrl", "reviewRounds", "aiBrief", "aiBriefStatus",
    "autoPromptStatus",
})


class FanOutError(RuntimeError):
    def __init__(self, message: str, *, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def split_lines(text: Any) -> list[str]:
    """The prompt list, one entry per line.

    Blank lines are separators, not entries — a trailing newline is how every
    text box ends and it must not become an empty prompt that dispatches and
    is refused as `missing_prompt`.
    """
    if not isinstance(text, str):
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _clean(data: dict) -> dict:
    """The recipe without the results — for SEEDING a new clone only.

    Never for a node that already exists. Applied to the template or to a
    clone that has already run, this deletes the mediaId of an image the user
    paid for, and no button in the product puts it back.
    """
    return {k: v for k, v in (data or {}).items() if k not in _RUN_ONLY_KEYS}


def _reprompt(data: dict, line: str, **extra) -> dict:
    """Change what an existing node will generate, keeping what it generated.

    The prompt is the only thing fan-out is entitled to rewrite here. A clone
    whose line is unchanged should not even notice the second press; one whose
    line changed keeps its old result until it is re-run, which is what lets
    the cost dialog say honestly that only N nodes need running.
    """
    out = dict(data or {})
    out["prompt"] = line
    out.update(extra)
    return out


def plan_fan_out(session, board_id: int, prompt_node_id: int) -> dict:
    """What `fan_out` would do, without doing it. Used by the confirm step."""
    prompt_node, template, lines, existing = _resolve(session, board_id, prompt_node_id)
    return {
        "lines": len(lines),
        "templateShortId": template.short_id,
        "willCreate": max(0, len(lines) - 1 - len(existing)),
        "willUpdate": min(len(existing), max(0, len(lines) - 1)),
        "willDelete": max(0, len(existing) - max(0, len(lines) - 1)),
    }


def _resolve(session, board_id: int, prompt_node_id: int):
    """(prompt node, template generation node, lines, existing clones)."""
    prompt_node = session.get(Node, prompt_node_id)
    if prompt_node is None or prompt_node.board_id != board_id:
        raise FanOutError("không có node prompt đó trên board này", status=404)
    if prompt_node.type != "prompt":
        raise FanOutError("chỉ node prompt mới tách được thành nhiều dòng")

    lines = split_lines((prompt_node.data or {}).get("prompt"))
    if not lines:
        raise FanOutError("node prompt đang trống")
    if len(lines) > MAX_FANOUT:
        raise FanOutError(
            f"{len(lines)} dòng vượt trần {MAX_FANOUT} — mỗi dòng là một lần "
            "gọi có tính tiền, nên số này cố ý bị chặn"
        )

    edges = list(session.exec(select(Edge).where(Edge.board_id == board_id)).all())
    nodes = {n.id: n for n in session.exec(select(Node).where(Node.board_id == board_id)).all()}

    # The template is the generation node this prompt feeds. Exactly one:
    # with two, "which one gets the lines" has no answer the graph can give,
    # and guessing would silently fan out the wrong branch.
    targets = [
        nodes[e.target_id] for e in edges
        if e.source_id == prompt_node_id and e.target_id in nodes
        and nodes[e.target_id].type in ("image", "video")
    ]
    # A clone is not a candidate template — it is the previous result of this
    # very action.
    targets = [t for t in targets if not _is_clone_of(t, prompt_node)]
    if not targets:
        raise FanOutError(
            "node prompt này chưa nối tới node ảnh/video nào — nối vào một "
            "node mẫu trước, rồi tách"
        )
    if len(targets) > 1:
        raise FanOutError(
            f"node prompt nối tới {len(targets)} node sinh — tách chỉ chạy khi "
            "có đúng một node mẫu"
        )

    existing = sorted(
        (n for n in nodes.values() if _is_clone_of(n, prompt_node)),
        key=_clone_index,
    )
    return prompt_node, targets[0], lines, existing


def _is_clone_of(node: Node, prompt_node: Node) -> bool:
    return (node.data or {}).get("fanOutFrom") == prompt_node.short_id


def _carry_edges(session, board_id: int, carried, clone, known_edges) -> None:
    """Give a clone every non-prompt wire the template has, once each."""
    have = {
        (e.source_id, e.target_port)
        for e in known_edges
        if e.target_id == clone.id
    }
    for source in carried:
        if (source.source_id, source.target_port) in have:
            continue
        session.add(
            Edge(
                board_id=board_id,
                source_id=source.source_id,
                target_id=clone.id,
                kind=source.kind,
                source_port=source.source_port,
                target_port=source.target_port,
                source_variant_idx=source.source_variant_idx,
            )
        )


def _detach_children(session, node_id: Optional[int]) -> None:
    """Release the rows that point at a node about to be deleted.

    Requests are kept and unlinked rather than deleted: they are the record of
    what was spent, and the activity log is the only place a user can see that
    a clip was paid for. Assets go, because an asset with no node is
    unreachable.
    """
    if node_id is None:
        return
    for request in session.exec(select(Request).where(Request.node_id == node_id)).all():
        request.node_id = None
        session.add(request)
    for asset in session.exec(select(Asset).where(Asset.node_id == node_id)).all():
        session.delete(asset)


def _clone_index(node: Node) -> int:
    value = (node.data or {}).get("fanOutIndex")
    return value if isinstance(value, int) else 0


def fan_out(session, board_id: int, prompt_node_id: int) -> dict:
    """Turn each line into its own generation node. Creates rows only.

    The first line stays on the template node, so a board that already ran
    once keeps its first result instead of being rebuilt from scratch around
    it.
    """
    prompt_node, template, lines, existing = _resolve(session, board_id, prompt_node_id)
    edges = list(session.exec(select(Edge).where(Edge.board_id == board_id)).all())

    # Line 1 belongs to the template itself — but only its prompt changes.
    # Cleaning it here erased the first result on every second press, which is
    # the opposite of what this function's own docstring promises.
    template.data = _reprompt(template.data or {}, lines[0])
    session.add(template)

    # Everything wired INTO the template that is not this prompt gets wired
    # into every clone too: a reference photo or a character feeds all of
    # them, and a clone without it would generate something else entirely.
    carried = [
        e for e in edges
        if e.target_id == template.id and e.source_id != prompt_node_id
    ]

    # Only the columns that are NEW need room. Asking for room every time
    # would shove the board right on every press — press it twice with the
    # same list and everything downstream has moved twice for no reason.
    _make_runway(
        session, board_id, template, prompt_node,
        columns=max(0, (len(lines) - 1) - len(existing)),
    )

    wanted = lines[1:]
    created = updated = deleted = 0

    for i, line in enumerate(wanted):
        if i < len(existing):
            clone = existing[i]
            clone.data = _reprompt(
                clone.data or {},
                line,
                fanOutFrom=prompt_node.short_id,
                fanOutIndex=i,
            )
            clone.x = template.x + _COL_GAP * (i + 1)
            clone.y = template.y
            session.add(clone)
            # A reference wired into the template AFTER the first press has to
            # reach the clones that already exist too, or half the board
            # generates against a photo the other half has never seen.
            _carry_edges(session, board_id, carried, clone, edges)
            updated += 1
            continue

        clone = Node(
            board_id=board_id,
            short_id=generate_unique_short_id(session, board_id),
            type=template.type,
            x=template.x + _COL_GAP * (i + 1),
            y=template.y,
            w=template.w,
            h=template.h,
            data={
                **_clean(template.data or {}),
                "prompt": line,
                "title": f"{(template.data or {}).get('title') or template.type} {i + 2}",
                "fanOutFrom": prompt_node.short_id,
                "fanOutIndex": i,
            },
        )
        session.add(clone)
        session.commit()
        session.refresh(clone)
        _carry_edges(session, board_id, carried, clone, edges)
        created += 1

    # Lines removed from the list: the clones that held them go too, with
    # their wires. Leaving them would keep dispatching a prompt the user
    # deleted — and paying for it.
    for stale in existing[len(wanted):]:
        for e in edges:
            if e.source_id == stale.id or e.target_id == stale.id:
                session.delete(e)
        # A clone that has run owns Request and Asset rows pointing at it.
        # Deleting the node without releasing them is a FOREIGN KEY error,
        # which reaches the user as a bare 500 the moment they shorten a list
        # on a board that has already been run once.
        _detach_children(session, stale.id)
        session.delete(stale)
        deleted += 1

    session.commit()
    logger.info(
        "board %s: fan-out from %s → %d line(s) (+%d, ~%d, -%d)",
        board_id, prompt_node.short_id, len(lines), created, updated, deleted,
    )
    return {
        "lines": len(lines),
        "created": created,
        "updated": updated,
        "deleted": deleted,
        "templateShortId": template.short_id,
    }


def _make_runway(
    session, board_id: int, template: Node, prompt_node: Node, *, columns: int
) -> None:
    """Push whatever sits to the right of the template out of the way.

    Without this the clones land on top of existing nodes, which on a board
    built left-to-right is most of them. The packaged tool leaves the same
    gap, and for the same reason: a fan-out you then have to untangle by hand
    is not a shortcut.

    Only nodes strictly to the right move, and only by the width the clones
    need — dragging the whole board around would lose the arrangement someone
    made on purpose.
    """
    if columns <= 0:
        return
    shift = _COL_GAP * columns
    edge_x = template.x + _COL_GAP * 0.5
    moved = 0
    for node in session.exec(select(Node).where(Node.board_id == board_id)).all():
        if node.id == template.id or node.x <= edge_x:
            continue
        if _is_clone_of(node, prompt_node):
            # A clone of THIS fan-out is repositioned below, not shoved.
            # Clones of a different prompt node are ordinary neighbours and
            # do move — they are as much in the way as anything else.
            continue
        node.x = node.x + shift
        session.add(node)
        moved += 1
    if moved:
        logger.info("fan-out: moved %d node(s) right by %.0f", moved, shift)
