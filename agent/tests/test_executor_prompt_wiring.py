"""A generation node's prompt usually arrives over a wire, not on the node.

In the packaged workflows the text lives in its own `text_prompt` node and
travels to the image/video node along a `text → prompt` edge — 29 of the 129
wires across the nine files. The executor read only `node.data["prompt"]`, so
an imported board dispatched **nothing at all** and the run reported `done`.
The cost dialog would quote three billable calls and the run would make zero.

These tests are on the resolver rather than a whole run, so they are fast and
never dispatch anything.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from flowboard.routes.templates import TEMPLATE_DIR
from flowboard.services.pipeline_executor import _prompt_for
from flowboard.services.postprod_plan import Upstream

TEMPLATE_FILES = sorted(p.name for p in TEMPLATE_DIR.glob("*.json"))


def node(node_id, node_type, **data):
    return SimpleNamespace(id=node_id, type=node_type, data=data)


def up(node_obj, port="prompt"):
    """One wire in, with the socket it lands on. Every prompt wire into a
    generation node in the shipped workflows lands on `prompt` — all 24."""
    return Upstream(node_obj, port)


def test_a_nodes_own_prompt_is_used_when_it_has_one():
    target = node(1, "image", prompt="của chính nó")
    upstream = node(2, "prompt", prompt="của dây")
    assert _prompt_for(target, [up(upstream)]) == "của chính nó"


def test_the_prompt_is_read_from_an_upstream_prompt_node():
    """The case that made every imported workflow a no-op."""
    target = node(1, "image")
    upstream = node(2, "prompt", prompt="một con mèo")
    assert _prompt_for(target, [up(upstream)]) == "một con mèo"


def test_a_reference_upstream_is_not_read_as_a_script():
    """A character or image feeding an image node is a reference. Taking its
    title as the prompt would generate something nobody asked for."""
    target = node(1, "image")
    for wrong in ("character", "image", "visual_asset", "video"):
        upstream = node(2, wrong, prompt="đừng đọc cái này")
        assert _prompt_for(target, [up(upstream)]) is None


def test_the_first_prompt_wire_wins_and_the_rest_are_ignored():
    target = node(1, "video")
    wires = [up(node(2, "prompt", prompt="đầu")), up(node(3, "prompt", prompt="sau"))]
    assert _prompt_for(target, wires) == "đầu"


def test_an_empty_upstream_prompt_is_skipped_not_returned():
    target = node(1, "image")
    wires = [up(node(2, "prompt", prompt="   ")), up(node(3, "prompt", prompt="thật"))]
    assert _prompt_for(target, wires) == "thật"


def test_no_prompt_anywhere_is_none_not_an_empty_string():
    """The caller treats None as "leave idle". An empty string would dispatch
    and be refused by the handler as `missing_prompt`."""
    assert _prompt_for(node(1, "image"), []) is None


def test_a_hand_drawn_wire_with_no_port_still_counts():
    """Edges drawn on the canvas carry no port name; the main input is what
    dragging a prompt into a generation node means."""
    wires = [up(node(2, "prompt", prompt="vẽ tay"), None)]
    assert _prompt_for(node(1, "image"), wires) == "vẽ tay"


def test_a_prompt_wired_to_some_other_socket_is_not_this_nodes_prompt():
    """An `edit_video` title, for instance. Port-blind resolution would take
    it — the same class of mistake that mixed narration in as music."""
    wires = [up(node(2, "prompt", prompt="TIÊU ĐỀ"), "title")]
    assert _prompt_for(node(1, "video"), wires) is None


def test_whitespace_is_trimmed_from_both_sources():
    own = node(1, "image", prompt="  có khoảng trắng  ")
    assert _prompt_for(own, []) == "có khoảng trắng"
    wired = node(1, "image")
    assert _prompt_for(wired, [up(node(2, "prompt", prompt="  qua dây  "))]) == "qua dây"


@pytest.mark.skipif(not TEMPLATE_FILES, reason="packaged templates not installed")
def test_a_real_imported_workflow_now_has_prompts_to_run():
    """End to end against a shipped file: build the same node/edge shapes the
    import produces and confirm at least one generation node resolves a
    prompt. Zero here is the bug that shipped."""
    from flowboard.services import template_import

    resolved = 0
    for name in TEMPLATE_FILES:
        doc = json.loads((TEMPLATE_DIR / name).read_text(encoding="utf-8-sig"))
        by_source = {}
        for entry in doc["nodes"]:
            canvas_type = template_import.NODE_TYPE_MAP.get(entry["type"], "note")
            settings = entry.get("settings") or {}
            data = {}
            prompt = settings.get("prompt")
            if isinstance(prompt, str) and prompt.strip():
                data["prompt"] = prompt
            by_source[entry["id"]] = node(entry["id"], canvas_type, **data)

        wires_into = {}
        for wire in doc["connections"]:
            src = by_source.get(wire["srcNode"])
            if src is not None:
                wires_into.setdefault(wire["dstNode"], []).append(
                    Upstream(src, wire["dstPort"])
                )

        for nid, n in by_source.items():
            if n.type not in ("image", "video"):
                continue
            if _prompt_for(n, wires_into.get(nid, [])):
                resolved += 1

    assert resolved > 0, "no generation node in any shipped workflow has a prompt"


# ── one prompt node, three answers ────────────────────────────────────
#
# A scene written by an AI is three different texts for three different
# consumers: what the picture shows, how the camera moves, and what is said
# aloud. The packaged tool keeps them on separate sockets. Merged into one
# output they all become the same string — and the voice reads the camera
# directions aloud.


def _branching(**fields):
    return node(2, "prompt", **fields)


def test_the_socket_decides_which_of_the_three_texts_is_used():
    source = _branching(
        prompt="chung",
        imagePrompt="một ngôi chùa cổ",
        videoPrompt="máy quay lia chậm sang phải",
        voicePrompt="Ngày xửa ngày xưa…",
    )
    assert _prompt_for(node(1, "image"), [up(source, "image_prompts")]) == "một ngôi chùa cổ"
    assert _prompt_for(node(1, "video"), [up(source, "video_prompts")]) == "máy quay lia chậm sang phải"
    assert _prompt_for(node(1, "video"), [up(source, "voice_prompts")]) == "Ngày xửa ngày xưa…"


def test_a_node_with_one_text_still_answers_every_socket():
    """Most prompt nodes are typed by hand and hold a single text. Requiring
    the branch fields would break every board built before this existed."""
    plain = _branching(prompt="một con mèo")
    for port in ("image_prompts", "video_prompts", "voice_prompts", "prompt", None):
        assert _prompt_for(node(1, "image"), [up(plain, port)]) == "một con mèo"


def test_an_empty_branch_falls_back_rather_than_dispatching_nothing():
    """A blank branch field is not a decision to send nothing — it is a
    field nobody filled in. Returning None there leaves the node idle with
    no explanation."""
    partial = _branching(prompt="chung", imagePrompt="   ")
    assert _prompt_for(node(1, "image"), [up(partial, "image_prompts")]) == "chung"


def test_the_nodes_own_prompt_still_wins_over_any_branch():
    target = node(1, "image", prompt="của chính nó")
    source = _branching(prompt="chung", imagePrompt="của dây")
    assert _prompt_for(target, [up(source, "image_prompts")]) == "của chính nó"
