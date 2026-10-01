"""What the agent is allowed to propose.

These findings run before an apply, which is the last moment the user can say
no. So the two failure directions are not symmetric: a missed error means a
board approved and then refused by the run, while a false error means the agent
cannot describe an ordinary board at all. The tokenisation test below is the
second kind, and it was a real bug before it was a test.
"""
from __future__ import annotations

import pytest

from flowboard.services.agent_rules import (
    ProposedEdge as E,
)
from flowboard.services.agent_rules import (
    ProposedNode as N,
)
from flowboard.services.agent_rules import (
    blocking,
    check_plan,
)


def _rules(findings) -> list[str]:
    return sorted(f.rule for f in findings)


def test_an_ordinary_board_produces_nothing():
    nodes = [N("a", "image", "Mai Anh", "@@MaiAnh đứng trước cửa"),
             N("b", "video", "Cảnh 1")]
    edges = [E("a", "b", source_port="image_out_1", target_port="start_frame")]
    assert check_plan(nodes, edges) == []


def test_a_multi_word_title_matches_its_own_tag():
    """A tag carries no spaces, so the cast has to be tokenised the way
    `character_store` tokenises it. Comparing against the raw title made every
    multi-word name an `unknown_tag` — an error, so the agent would have
    blocked the most ordinary board there is."""
    nodes = [N("a", "image", "Đạo Sĩ Già", "@@DaoSiGia bước vào sân")]
    assert check_plan(nodes, []) == []


def test_a_tag_naming_nobody_is_an_error():
    nodes = [N("a", "image", "Mai Anh", "@@KhongAi đứng đó")]
    assert "unknown_tag" in _rules(check_plan(nodes, []))
    assert blocking(check_plan(nodes, []))


def test_a_tag_in_spoken_text_is_an_error():
    """It is read aloud in audio that has already been paid for."""
    nodes = [N("a", "prompt", "Mai Anh", 'Thoại: "@@MaiAnh xin chào"')]
    assert "tag_in_dialogue" in _rules(check_plan(nodes, []))


def test_a_wire_onto_a_socket_the_target_does_not_read_is_an_error():
    nodes = [N("a", "image", "Ảnh"), N("b", "video", "Clip")]
    edges = [E("a", "b", source_port="image_out_1", target_port="khong_co")]
    assert "bad_target_port" in _rules(check_plan(nodes, edges))


def test_a_wire_from_a_socket_the_source_does_not_offer_is_an_error():
    nodes = [N("a", "image", "Ảnh"), N("b", "video", "Clip")]
    edges = [E("a", "b", source_port="audio", target_port="start_frame")]
    assert "bad_source_port" in _rules(check_plan(nodes, edges))


def test_a_wire_to_a_node_outside_the_plan_is_an_error():
    """Otherwise the apply writes an edge with a dangling end."""
    nodes = [N("a", "image", "Ảnh")]
    edges = [E("a", "ghost", source_port="image_out_1", target_port="start_frame")]
    assert "unknown_node" in _rules(check_plan(nodes, edges))


def _character_plan(count: int):
    nodes = [N(f"c{i}", "character", f"NV{i}") for i in range(1, count + 1)]
    nodes.append(N("v", "video", "Clip"))
    edges = [
        E(f"c{i}", "v", source_port="media", target_port=f"character_{i}")
        for i in range(1, count + 1)
    ]
    return nodes, edges


@pytest.mark.parametrize("lane,count,blocked", [
    (None, 3, False), (None, 4, True),
    ("omni", 4, False), ("omni", 10, False), ("omni", 11, True),
])
def test_the_reference_ceiling_follows_the_lane(lane, count, blocked):
    """A wire past the ceiling dispatches without the reference the user is
    paying for — worse than failing, because it succeeds."""
    nodes, edges = _character_plan(count)
    assert bool(blocking(check_plan(nodes, edges, lane=lane))) is blocked


def test_the_ceiling_counts_distinct_sockets_not_wires():
    """Four wires into three sockets is three reference slots.

    Enough wires to pass the ceiling if they were counted individually, so this
    fails if the rule stops de-duplicating rather than passing vacuously.
    """
    nodes = [N(f"c{i}", "character", f"NV{i}") for i in range(1, 4)]
    nodes.append(N("v", "video", "Clip"))
    edges = [
        E("c1", "v", source_port="media", target_port="character_1"),
        E("c1", "v", source_port="media", target_port="character_1"),
        E("c2", "v", source_port="media", target_port="character_2"),
        E("c3", "v", source_port="media", target_port="character_3"),
    ]
    assert "too_many_characters" not in _rules(check_plan(nodes, edges))


def test_the_ceiling_catches_what_the_socket_check_cannot():
    """The two checks are not redundant, and this is the gap between them.

    `canvas_catalog.accepts` judges one socket at a time, so `character_4` on a
    three-slot lane is caught there. A REGISTERED character arrives as
    `character_<uuid>` and is valid at any index — three numbered sockets plus
    one uuid is four references that each pass individually. Only the per-node
    count sees it, and without it the dispatch drops a reference the user is
    paying for.
    """
    nodes = [N(f"c{i}", "character", f"NV{i}") for i in range(1, 4)]
    nodes += [N("cu", "character", "NV-uuid"), N("v", "video", "Clip")]
    edges = [
        E(f"c{i}", "v", source_port="media", target_port=f"character_{i}")
        for i in range(1, 4)
    ]
    edges.append(E(
        "cu", "v", source_port="media",
        target_port="character_a1b2c3d4-5678-4abc-9def-0123456789ab",
    ))
    rules = _rules(check_plan(nodes, edges))
    assert "bad_target_port" not in rules, "each socket must pass on its own"
    assert "too_many_characters" in rules


def test_a_node_with_no_prompt_is_not_checked_for_tags():
    """Most structural nodes carry no text, and running the prompt rules on an
    empty string reported nothing useful."""
    assert check_plan([N("a", "note", "Ghi chú")], []) == []


def test_an_untitled_node_does_not_make_every_tag_resolve():
    """An empty title must not enter the cast, or a bare token would match it."""
    nodes = [N("a", "image", "", "@@KhongAi ở đây"), N("b", "image", "Mai Anh")]
    assert "unknown_tag" in _rules(check_plan(nodes, []))


def test_a_board_that_names_nobody_does_not_claim_a_tag_is_unknown():
    """Deliberate, and the same answer the run gives.

    `pipeline_executor` passes `cast=cast or None` at its pre-dispatch gate, so
    with nobody named `prompt_checks` declines to call any tag unknown rather
    than calling them all unknown. It is conservative in the only direction
    that cannot block a board the user can actually run — and the agent has to
    agree with the run, or it would approve boards the run refuses and refuse
    boards the run accepts.
    """
    nodes = [N("a", "image", "", "@@KhongAi ở đây")]
    assert "unknown_tag" not in _rules(check_plan(nodes, []))
