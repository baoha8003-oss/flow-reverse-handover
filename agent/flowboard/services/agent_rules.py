"""Whether a proposed graph would actually run, checked before it is applied.

The canvas agent writes nodes and wires. Everything that can go wrong with
those is already checked somewhere — `canvas_catalog` knows which sockets
connect, `prompt_checks` knows which tags resolve, `character_ports` knows how
many reference slots a lane serves. This module asks those three about a
proposal; it does not own a rule of its own.

That matters more here than elsewhere. A second copy of the character ceiling
or the tag rule would drift, and the drift shows up as the agent proposing a
board that the run then refuses — after the user has approved it, which is the
one moment they had to say no.

**Errors block, warnings inform.** Two of the checks are errors by the
arbitrator's decision: a character wire past the lane's ceiling, and a tag that
names nobody on the board. Both produce a dispatch that either fails or, worse,
succeeds having quietly dropped the reference the user was paying for.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from flowboard.services import (
    canvas_catalog,
    character_ports,
    character_store,
    prompt_checks,
)
from flowboard.services.prompt_checks import Finding


@dataclass(frozen=True)
class ProposedNode:
    """A node as the agent describes it. `ref` is the agent's own handle."""

    ref: str
    type: str
    title: str = ""
    prompt: str = ""


@dataclass(frozen=True)
class ProposedEdge:
    source: str
    target: str
    source_port: Optional[str] = None
    target_port: Optional[str] = None


def _cast(nodes: Iterable[ProposedNode]) -> list[str]:
    """The names a tag may resolve to, spelled the way a tag is spelled.

    Titles are what a person types on a node; tags cannot contain spaces, so
    `character_store.normalize_name` is what turns "Mai Anh" into the `MaiAnh`
    that `@@MaiAnh` actually names. Handing raw titles to `prompt_checks`
    instead makes every multi-word title fail to match its own tag — and
    `unknown_tag` is an error, so the agent would block the most ordinary board
    there is. Asked of `character_store` because that is the module whose
    convention the tag follows.

    A node with no title contributes nothing rather than an empty string, which
    would make a bare `@` resolve to everything.
    """
    out: list[str] = []
    for node in nodes:
        token = character_store.normalize_name(node.title or "")
        if token:
            out.append(token)
    return out


def check_plan(
    nodes: Iterable[ProposedNode],
    edges: Iterable[ProposedEdge],
    *,
    lane: Optional[str] = None,
) -> list[Finding]:
    """Findings for a whole proposal, errors first in the caller's report.

    `lane` is the video lane the board will run on, because both the character
    ceiling and the duration table depend on it. Unknown lane means the
    conservative default (3 slots), which is what `character_ports` already
    does — asking rather than assuming keeps one answer in one place.
    """
    nodes = list(nodes)
    edges = list(edges)
    by_ref = {n.ref: n for n in nodes}
    findings: list[Finding] = []

    # 1. Sockets. A wire onto a port the target does not read is a wire that
    #    looks connected in the canvas and feeds nothing at run time.
    character_wires: dict[str, list[str]] = {}
    for edge in edges:
        target = by_ref.get(edge.target)
        source = by_ref.get(edge.source)
        if target is None or source is None:
            findings.append(Finding(
                rule="unknown_node",
                severity="error",
                message=(
                    f"Dây nối tới node không có trong kế hoạch "
                    f"({edge.source} → {edge.target})."
                ),
                span=f"{edge.source}→{edge.target}",
            ))
            continue
        incoming = canvas_catalog.accepts(target.type, edge.target_port, lane=lane)
        if not incoming.ok:
            findings.append(Finding(
                rule="bad_target_port", severity="error",
                message=incoming.reason,
                span=f"{target.title or target.ref}.{edge.target_port}",
            ))
        outgoing = canvas_catalog.emits(source.type, edge.source_port)
        if not outgoing.ok:
            findings.append(Finding(
                rule="bad_source_port", severity="error",
                message=outgoing.reason,
                span=f"{source.title or source.ref}.{edge.source_port}",
            ))
        if character_ports.is_character_port(edge.target_port):
            character_wires.setdefault(edge.target, []).append(edge.target_port or "")

    # 2. The lane's reference ceiling, counted per consuming node. Asked of
    #    `character_ports` so the number cannot disagree with the dispatch.
    limit = character_ports.limit_for(lane)
    for ref, ports in character_wires.items():
        distinct = sorted(set(ports))
        if len(distinct) > limit:
            node = by_ref.get(ref)
            findings.append(Finding(
                rule="too_many_characters", severity="error",
                message=(
                    f"{len(distinct)} cổng nhân vật trên “{(node.title if node else ref)}” "
                    f"— làn “{lane or 'mặc định'}” chỉ nhận {limit}."
                ),
                span=", ".join(distinct),
            ))

    # 3. Tags, against the titles actually on the board. `prompt_checks` owns
    #    the tag grammar and the dialogue split; it is handed the cast rather
    #    than re-deriving either here.
    cast = _cast(nodes)
    for node in nodes:
        if not node.prompt.strip():
            continue
        # `cast or None` is the executor's own idiom at its pre-dispatch gate:
        # an empty cast means "nobody is named on this board", and
        # `prompt_checks` then declines to call any tag unknown rather than
        # calling them all unknown. Conservative in the direction that cannot
        # block a board — matched here so the agent's answer and the run's
        # answer are the same answer.
        findings.extend(
            prompt_checks.check(node.prompt, cast=cast or None, lane=lane)
        )

    return findings


def blocking(findings: Iterable[Finding]) -> list[Finding]:
    """The findings that must stop an apply. Delegates the severity rule."""
    return prompt_checks.errors(list(findings))
