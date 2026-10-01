"""Counting what a board will spend, before it spends it.

An imported workflow is a dozen nodes that become dozens of billable calls.
The count has to be right in the direction that matters: over-reporting cost
makes someone pause, under-reporting makes them pay.
"""
from __future__ import annotations

import pytest
from sqlmodel import select

from flowboard.db import get_session
from flowboard.db.models import Board, Node
from flowboard.routes.templates import TEMPLATE_DIR
from flowboard.services import template_import
from flowboard.short_id import generate_unique_short_id


def _board_with(
    nodes: list[tuple[str, dict]],
    edges: "list[tuple[int, int, str]] | None" = None,
) -> int:
    """Build a board from node tuples, optionally wired.

    ``edges`` is ``(from_index, to_index, target_port)`` against the node list.
    It exists because a video node's servable lanes depend on what feeds it: a
    bare one is text-to-video, which only OMNI can do on this transport, while
    one with a start frame is image-to-video, where the Veo lanes live. A test
    about pricing needs to say which it means.
    """
    created: list[int] = []
    with get_session() as s:
        board = Board(name="estimate test")
        s.add(board)
        s.commit()
        s.refresh(board)
        for node_type, data in nodes:
            # A generation node with no prompt is skipped by the run, so the
            # estimate reports it as not-ready rather than billable. Tests
            # about pricing need a runnable node, so give them one.
            if node_type in ("image", "video") and "prompt" not in data:
                data = {**data, "prompt": "một cảnh"}
            node = Node(
                board_id=board.id,
                short_id=generate_unique_short_id(s, board.id),
                type=node_type,
                data=data,
            )
            s.add(node)
            s.commit()
            s.refresh(node)
            created.append(node.id)
        for src, dst, port in edges or []:
            from flowboard.db.models import Edge

            s.add(Edge(
                board_id=board.id,
                source_id=created[src],
                target_id=created[dst],
                kind="media",
                target_port=port,
            ))
        s.commit()
        return board.id


#: An image feeding a video's start frame: the shape every packaged workflow
#: uses, and the one where the Veo lanes are servable.
_I2V_WIRE = [(0, 1, "start_frame")]


def _estimate(client, board_id: int) -> dict:
    resp = client.get(f"/api/boards/{board_id}/estimate")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_prompts_and_notes_cost_nothing_and_are_not_listed(client):
    """They produce no dispatch. Listing them as free line items would bury
    the ones that do cost money."""
    board = _board_with([("prompt", {}), ("note", {}), ("visual_asset", {})])
    body = _estimate(client, board)
    assert body["billableJobs"] == 0
    assert body["localJobs"] == 0
    assert body["items"] == []


def test_variants_multiply_the_job_count(client):
    """One image node asking for four variants is four calls, not one. This
    is the whole reason a graph is more expensive than it looks."""
    board = _board_with([("image", {"variantCount": 4})])
    body = _estimate(client, board)
    assert body["billableJobs"] == 4
    assert body["items"][0]["jobs"] == 4


def test_a_missing_variant_count_is_one_not_zero(client):
    board = _board_with([("image", {})])
    assert _estimate(client, board)["billableJobs"] == 1


def test_an_absurd_variant_count_is_clamped_to_what_will_actually_run(client):
    """9999 used to be quoted as one job — and `gen_image` had no ceiling at
    all, so it drew nine thousand images and charged for them. The estimate is
    only worth reading if it says what the dispatch will do, so both now clamp
    through the same helper and both answer 16."""
    from flowboard.worker.processor import MAX_VARIANTS

    board = _board_with([("image", {"variantCount": 9999})])
    assert _estimate(client, board)["billableJobs"] == MAX_VARIANTS


def test_a_broken_variant_count_falls_back_to_one(client):
    """Data rot must not produce a scary number that makes the warning
    useless."""
    for rot in (True, "four", -3, 0, None):
        board = _board_with([("image", {"variantCount": rot})])
        assert _estimate(client, board)["billableJobs"] == 1, rot


def test_local_steps_are_counted_separately_and_priced_at_zero(client):
    """ffmpeg runs here. A board that is mostly post-production must not read
    as expensive."""
    board = _board_with([("merge_video", {}), ("add_bgm", {}), ("edit_video", {})])
    body = _estimate(client, board)
    assert body["billableJobs"] == 0
    assert body["localJobs"] == 3
    assert all(item["credits"] == 0 for item in body["items"])


def test_omni_is_priced_from_the_sdk_table(client):
    from flowboard.services import flow_sdk

    board = _board_with(
        [("video", {"videoQuality": "omni", "durationSeconds": 10})]
    )
    body = _estimate(client, board)
    assert body["knownCredits"] == flow_sdk.OMNI_FLASH_CREDIT_COST[10]
    assert body["unpricedJobs"] == 0


def test_a_variant_count_multiplies_a_text_to_video_quote_because_it_multiplies_the_run(
    client,
):
    """This test used to assert the opposite, on a premise that was false.

    It said "no video handler reads `variant_count`" — but
    `_handle_gen_video_text` does read it, and `gen_video_text` loops one submit
    per variant. So this exact board, unwired and therefore text-to-video, was
    quoted 1 job / 30 credits and dispatched 4 / 120. The fixture was the failure
    scenario and the assertion certified it.

    A wired clip is image-to-video, which fans out over `start_media_ids`
    instead — and a board run resolves exactly one — so only the text-to-video
    family multiplies. The sibling test below pins that half.
    """
    from flowboard.services import flow_sdk

    board = _board_with(
        [("video", {"videoQuality": "omni", "durationSeconds": 10, "variantCount": 4})]
    )
    body = _estimate(client, board)
    assert body["billableJobs"] == 4
    assert body["knownCredits"] == 4 * flow_sdk.OMNI_FLASH_CREDIT_COST[10]


def test_a_variant_count_does_not_multiply_a_wired_clip(client):
    """Image-to-video fans out over source images, not over `variantCount`, and
    a board run resolves exactly one start frame. Multiplying here would quote
    several times the real cost — the direction that makes someone refuse a run
    they could afford."""
    from flowboard.services import flow_sdk

    board = _board_with(
        [("image", {"prompt": "một khung"}),
         ("video", {"videoQuality": "omni", "durationSeconds": 10, "variantCount": 4})],
        [(0, 1, "start_frame")],
    )
    body = _estimate(client, board)
    clip = next(i for i in body["items"] if i["type"] == "video")
    assert clip["jobs"] == 1
    assert clip["credits"] == flow_sdk.OMNI_FLASH_CREDIT_COST[10]


def test_a_veo_lane_is_unpriced_not_free(client):
    """There is no published price table for the Veo lanes — not in this
    build and not in the packaged tool, which only ever reads the balance.
    Reporting 0 credits would be a lie in the expensive direction."""
    # Wired from an image, because a bare video node is text-to-video and this
    # transport has no Veo text-to-video at all -- it would be quoted as
    # not-ready rather than unpriced, which is a different fact.
    board = _board_with(
        [("image", {}), ("video", {"videoQuality": "fast"})], _I2V_WIRE
    )
    body = _estimate(client, board)
    assert body["billableJobs"] == 2  # the image, and the clip
    assert body["knownCredits"] == 0
    # Asserted on the clip's own line rather than the board total, because the
    # image feeding it has no published price either and would hide the point.
    clip = next(i for i in body["items"] if i["type"] == "video")
    assert clip["credits"] is None
    assert "Fast" in clip["note"]


def test_omni_without_a_duration_is_unpriced_rather_than_guessed(client):
    board = _board_with([("video", {"videoQuality": "omni"})])
    body = _estimate(client, board)
    assert body["unpricedJobs"] == 1
    assert body["items"][0]["credits"] is None


def test_an_unclassified_type_counts_as_billable(client):
    """Being wrong here should cost a needless confirmation, never a
    surprise charge."""
    board = _board_with([("some_future_type", {})])
    body = _estimate(client, board)
    assert body["billableJobs"] == 1
    assert body["unpricedJobs"] == 1


def test_unknown_board_is_a_404(client):
    assert client.get("/api/boards/999999/estimate").status_code == 404


@pytest.mark.skipif(
    not list(TEMPLATE_DIR.glob("*.json")), reason="packaged templates not installed"
)
def test_a_real_imported_workflow_accounts_for_every_generator(client):
    """The case this endpoint exists for: a template lands on the canvas and
    the user needs the number before pressing Run.

    This used to assert `billableJobs > 0`, and that assumption was wrong.
    Every packaged workflow starts from photos the user has to supply, and
    the alphabetically-first one — `gioi_thieu_do_noi_that` — has BOTH its
    generators wired to empty uploads. The honest quote for it is zero
    billable and two not-ready; demanding a non-zero number was demanding
    that the estimate promise work the board cannot do.

    The real invariant is that the dialog explains the whole board: every
    generation node counted exactly once, billable or not-ready, with the
    line items adding up to the headline.
    """
    name = sorted(p.name for p in TEMPLATE_DIR.glob("*.json"))[0]
    board_id = client.post(f"/api/templates/{name}/import").json()["boardId"]
    body = _estimate(client, board_id)
    assert body["billableJobs"] + body["notReadyJobs"] > 0, (
        "an imported workflow with no generation work at all"
    )
    assert body["localJobs"] > 0, "no post-production step survived the import"
    # Every listed item names its node, so the dialog can point at the graph.
    assert all(item["shortId"] for item in body["items"])
    # The line items must add up to the headline numbers, or the dialog shows
    # a total the list below it does not explain.
    assert sum(i["jobs"] for i in body["items"] if i["credits"] != 0) == (
        body["billableJobs"]
    )


@pytest.mark.skipif(
    not (TEMPLATE_DIR / "gioi_thieu_do_noi_that.json").is_file(),
    reason="packaged templates not installed",
)
def test_the_furniture_template_reports_its_empty_uploads(client):
    """The board that exposed the defect, pinned by name.

    Its two generators are fed by `Upload Media` nodes that ship empty, and
    its image prompt names the photos it expects. Before this the estimate
    said `notReadyJobs: 0` — "ready" — and a run would have spent credits
    generating from a prompt referencing pictures it was never given.
    """
    board_id = client.post(
        "/api/templates/gioi_thieu_do_noi_that.json/import"
    ).json()["boardId"]
    body = _estimate(client, board_id)

    assert body["notReadyJobs"] > 0, "empty uploads quoted as ready to run"
    assert body["billableJobs"] == 0, "quoted a charge for a board that cannot run"
    blocked = [i for i in body["items"] if "Thiếu ảnh đầu vào" in (i["note"] or "")]
    assert blocked, "nothing told the user which socket is empty"
    # The note names the port, or "add an image" is unactionable on a board
    # with several upload sockets.
    assert any("image_" in (i["note"] or "") for i in blocked)


# ── the headline number must be the one the run produces ────────────────

def test_only_the_types_the_executor_dispatches_are_counted(client):
    """`run_pipeline` skips character and Storyboard nodes. Counting them
    quoted calls the run would never make — the number people are asked to
    approve has to be the number that happens."""
    board = _board_with([("character", {}), ("Storyboard", {}), ("image", {})])
    assert _estimate(client, board)["billableJobs"] == 1


def test_a_multi_pass_edit_counts_every_pass(client):
    """A fully-configured edit_video is several ffmpeg runs, not one."""
    board = _board_with(
        [
            (
                "edit_video",
                {
                    "sourceSettings": {
                        "enable_remove_veo_logo": True,
                        "veo_logo_x_pct": 70,
                        "veo_logo_y_pct": 85,
                        "veo_logo_w_pct": 25,
                        "veo_logo_h_pct": 10,
                        "enable_bgm": True,
                        "bgm_sample_file": "calm.mp3",
                        "upscale_2k_4k": True,
                    }
                },
            )
        ]
    )
    assert _estimate(client, board)["localJobs"] == 3


def test_an_unconfigured_local_node_still_counts_as_one_step(client):
    """Nothing is wired in yet, so the planner answers "no ops". Reporting 0
    would make the node look absent from the run rather than not-ready."""
    board = _board_with([("merge_video", {})])
    assert _estimate(client, board)["localJobs"] == 1


# ── a node the run will skip must not be quoted as a cost ───────────────

def test_a_generation_node_with_no_prompt_is_not_counted_as_billable(client):
    """Several shipped templates ship with the prompt boxes blank on purpose.
    The executor skips those nodes, so quoting them made the dialog promise
    calls the run would never make."""
    board = _board_with(
        [("image", {"prompt": ""}), ("video", {"prompt": "chạy", "videoQuality": "omni",
                                              "sourceSettings": {"duration": 8}})],
        _I2V_WIRE,
    )
    body = _estimate(client, board)
    assert body["billableJobs"] == 1
    assert body["notReadyJobs"] == 1


def test_a_not_ready_node_is_still_listed_so_the_user_knows_why(client):
    """Dropping it silently would make a board look emptier than it is."""
    board = _board_with([("image", {"prompt": ""})])
    body = _estimate(client, board)
    assert len(body["items"]) == 1
    assert body["items"][0]["jobs"] == 0
    assert "prompt" in body["items"][0]["note"].lower()


def test_a_prompt_arriving_over_a_wire_counts_as_ready(client):
    """The prompt usually lives in its own node and travels along an edge —
    that is how every packaged workflow is built."""
    from flowboard.db.models import Edge

    with get_session() as s:
        board = Board(name="wired prompt")
        s.add(board)
        s.commit()
        s.refresh(board)
        prompt_node = Node(
            board_id=board.id,
            short_id=generate_unique_short_id(s, board.id),
            type="prompt",
            data={"prompt": "một con mèo"},
        )
        image_node = Node(
            board_id=board.id,
            short_id=generate_unique_short_id(s, board.id),
            type="image",
            data={},
        )
        s.add(prompt_node)
        s.add(image_node)
        s.commit()
        s.refresh(prompt_node)
        s.refresh(image_node)
        s.add(
            Edge(
                board_id=board.id,
                source_id=prompt_node.id,
                target_id=image_node.id,
                target_port="prompt",
            )
        )
        s.commit()
        board_id = board.id

    body = _estimate(client, board_id)
    assert body["billableJobs"] == 1, "a wired prompt was not seen"
    assert body["notReadyJobs"] == 0


# ── transcription is neither free nor a Flow credit ─────────────────────

def test_a_subtitled_edit_reports_its_gemini_calls_separately(client, monkeypatch):
    """Six of the nine shipped workflows enable subtitles, so importing one
    can commit the user to several Gemini calls they never asked for. The
    dialog has to show that before the run, not after."""
    monkeypatch.setattr("flowboard.services.transcribe.available", lambda: True)
    board = _board_with(
        [("edit_video", {"sourceSettings": {"enable_sub": True, "sub_size": 40}})]
    )
    body = _estimate(client, board)
    assert body["transcribeJobs"] == 1
    # Not a Flow credit, and not counted as free ffmpeg work either.
    assert body["billableJobs"] == 0
    assert body["knownCredits"] == 0
    assert body["items"][0]["credits"] is None
    assert "gemini" in body["items"][0]["note"].lower()


def test_no_transcription_is_counted_without_a_key(client, monkeypatch):
    """The planner does not schedule a pass that would certainly fail, so
    the estimate must not quote one either."""
    monkeypatch.setattr("flowboard.services.transcribe.available", lambda: False)
    board = _board_with(
        [("edit_video", {"sourceSettings": {"enable_sub": True}})]
    )
    assert _estimate(client, board)["transcribeJobs"] == 0


def test_an_edit_without_subtitles_reports_no_transcription(client):
    board = _board_with(
        [("edit_video", {"sourceSettings": {"upscale_2k_4k": True}})]
    )
    body = _estimate(client, board)
    assert body["transcribeJobs"] == 0
    assert body["localJobs"] == 1


# ── the review loop's own spend ────────────────────────────────────────


def test_the_review_loop_is_counted_before_the_run(client):
    """`review_loop`'s docstring says the estimate shows its vision calls
    before the run. It did not — this module never consulted it — so up to four
    vision calls a node were invisible until the bill arrived."""
    board = _board_with([
        ("image", {}),
        ("video", {"videoQuality": "lite_relaxed",
                   "sourceSettings": {"review_loop": True,
                                      "review_max_rounds": 3}}),
    ], _I2V_WIRE)
    body = _estimate(client, board)
    # One review always happens when the loop is on; three is the ceiling.
    assert body["reviewJobs"] == 1
    assert body["reviewJobsMax"] == 3


def test_a_board_with_the_loop_off_counts_none(client):
    """Off unless asked for: a loop that counted by default would scare every
    board that never opted in."""
    board = _board_with([("video", {"videoQuality": "lite_relaxed"})])
    body = _estimate(client, board)
    assert body["reviewJobs"] == 0
    assert body["reviewJobsMax"] == 0


def test_the_loop_on_an_image_node_counts_nothing(client):
    """The loop reviews clips. An image node carrying the flag is not a video
    review waiting to happen."""
    board = _board_with([("image", {"sourceSettings": {"review_loop": True}})])
    assert _estimate(client, board)["reviewJobs"] == 0


def test_the_ceiling_never_drops_below_the_floor(client):
    """A stray `review_max_rounds: 1` must not make the range read backwards."""
    board = _board_with([
        ("image", {}),
        ("video", {"videoQuality": "lite_relaxed",
                   "sourceSettings": {"review_loop": True,
                                      "review_max_rounds": 1}}),
    ], _I2V_WIRE)
    body = _estimate(client, board)
    assert body["reviewJobs"] == 1
    assert body["reviewJobsMax"] == 1


def test_post_production_reviews_still_count_alongside(client):
    """Two different things called "review": a post-production `review` op and
    the loop. Both are the user's AI quota, so both belong in the same total."""
    board = _board_with([
        ("image", {}),
        ("video", {"videoQuality": "lite_relaxed",
                   "sourceSettings": {"review_loop": True,
                                      "review_max_rounds": 2}}),
        ("review_video", {}),
    ], _I2V_WIRE)
    body = _estimate(client, board)
    assert body["reviewJobs"] >= 2
    assert body["reviewJobsMax"] >= body["reviewJobs"]


# ── lanes this transport cannot dispatch ──────────────────────────────


def test_a_refused_lane_is_not_ready_rather_than_billable(client):
    """A lane with no key on the batch path errors for free, so quoting credits
    for it would have someone cancel a run that was never going to charge them.

    The note has to name the lane: "not ready" on a board whose wiring is
    perfect reads as a bug until it says which dropdown to change.
    """
    board = _board_with(
        [("image", {}), ("video", {"videoQuality": "fast_relaxed"})], _I2V_WIRE
    )
    body = _estimate(client, board)
    clip = next(i for i in body["items"] if i["type"] == "video")
    assert clip["jobs"] == 0
    assert body["notReadyJobs"] >= 1
    assert "TỪ CHỐI" in clip["note"]
    assert "Lower Priority" in clip["note"]


def test_a_veo_lane_on_text_to_video_is_billable_now(client):
    """A bare video node is text-to-video, and Veo serves that again.

    This test asserted the opposite this morning: that a Veo lane here was
    refused, because nobody had captured a Veo text-to-video payload. Measured
    live the same day — `YhhmEf` parses Veo keys and objects only to the plan —
    so the commonest board shape there is went from "always refused" to
    "dispatches the lane it asked for".
    """
    board = _board_with([("video", {"prompt": "một cảnh"})])
    body = _estimate(client, board)
    clip = body["items"][0]
    assert clip["jobs"] == 1
    assert body["notReadyJobs"] == 0


def test_a_text_to_video_length_veo_does_not_have_is_not_ready(client):
    """Omni has 10s and Veo does not. The duration is part of the key, so the
    nearest one would bill a different clip than the board asked for."""
    board = _board_with([
        ("video", {"prompt": "một cảnh", "videoQuality": "lite",
                   "sourceSettings": {"duration": 10}}),
    ])
    body = _estimate(client, board)
    clip = body["items"][0]
    assert clip["jobs"] == 0
    assert "10s" in clip["note"]


def test_a_lane_with_no_text_to_video_key_is_still_not_ready(client):
    """`quality` has no `veo_3_1_t2v_*` name in the exe at all."""
    board = _board_with([("video", {"prompt": "một cảnh", "videoQuality": "quality"})])
    body = _estimate(client, board)
    assert body["items"][0]["jobs"] == 0


def test_the_veo_reference_lane_is_ready_but_unpriced(client):
    """Ready, yes. Free — nobody measured that, and this test used to assert it.

    Veo's reference family has exactly one key on this transport and the board is
    dispatchable on it (20/09: `MZZa6b` reads the name from `request[2]`, answering
    `[7]` MODEL_ACCESS_DENIED rather than `[5]`). What the measurement could NOT
    establish is the price: `[7]` means this plan does not hold the key, so this
    account cannot run it to find out.

    "0 credit" was inferred from the `_low_priority` suffix. That is the same class
    of guess the registry refuses to make for 360p — and it is the dangerous
    direction, because a free label is what lets the review loop re-run a clip
    without asking. Unpriced says the true thing and `unpricedJobs` carries the
    warning.
    """
    board = _board_with(
        [("character", {"mediaId": "m-face"}),
         ("video", {"videoQuality": "lite_relaxed"})],
        [(0, 1, "character_1")],
    )
    body = _estimate(client, board)
    clip = next(i for i in body["items"] if i["type"] == "video")
    assert clip["jobs"] == 1, "the lane dispatches — it must not read as refused"
    assert clip["credits"] is None
    assert "CHƯA ĐO" in (clip["note"] or "")
    assert body["unpricedJobs"] >= 1


def test_a_character_port_lane_with_no_veo_key_is_not_ready(client):
    """The other three r2v names carry `portrait` and come back `[5]`."""
    board = _board_with(
        [("character", {"mediaId": "m-face"}),
         ("video", {"videoQuality": "fast"})],
        [(0, 1, "character_1")],
    )
    body = _estimate(client, board)
    clip = next(i for i in body["items"] if i["type"] == "video")
    assert clip["jobs"] == 0
    assert "OMNI" in clip["note"]


def test_the_omni_lane_on_the_same_wiring_is_priced_and_billable(client):
    """The other half: the refusal must be about the lane, not about the shape
    of the board. Omni serves all three families and costs credits."""
    board = _board_with(
        [("character", {"mediaId": "m-face"}),
         ("video", {"videoQuality": "omni", "sourceSettings": {"duration": "8s"}})],
        [(0, 1, "character_1")],
    )
    body = _estimate(client, board)
    clip = next(i for i in body["items"] if i["type"] == "video")
    assert clip["jobs"] == 1
    assert clip["credits"] == 25


def test_a_wired_clip_is_image_to_video_even_at_a_length_veo_t2v_lacks(client):
    """The i2v/t2v distinction, pinned where it still shows.

    It used to show everywhere: reading "is a start frame RESOLVED" instead of
    "is one WIRED" made every storyboard clip look like text-to-video, and
    text-to-video was refused outright, so whole boards were quoted as not-ready.
    Veo text-to-video works now, so that symptom is gone and the distinction
    could rot unnoticed.

    It still decides which rules apply. A Veo image-to-video key carries its own
    8 seconds and ignores the node's duration; Veo text-to-video has 4/6/8 and
    refuses 10. So a wired clip asking for 10s is ready, and the same clip
    misread as text-to-video is refused for a length that was never going to be
    sent.
    """
    board = _board_with(
        [("image", {}),
         ("video", {"videoQuality": "lite", "sourceSettings": {"duration": 10}})],
        _I2V_WIRE,
    )
    body = _estimate(client, board)
    clip = next(i for i in body["items"] if i["type"] == "video")
    assert clip["jobs"] == 1, clip["note"]
    assert body["notReadyJobs"] == 0


def test_a_character_board_with_no_lane_is_quoted_as_the_omni_it_will_run(client):
    """The quote and the run disagreed about which FAMILY a character node uses.

    The worker's rule is written at `processor.py` beside the lane branch: "An
    absent lane still means Omni". The estimate read the same absent lane as
    `DEFAULT_VIDEO_QUALITY` ("lite"), found no Veo reference key for it, and
    quoted "sẽ bị TỪ CHỐI, không tốn credit".

    Every character board the canvas builds lands here, because the ✨ Dựng dạng
    button sends only `scene_count` and the archetype omits `quality`. Measured
    on the shipped `character_drama`: quoted 0 credits, dispatched 3 × 25.

    So an unset lane must be priced as Omni — the same answer the dispatch gives
    — not as a refusal the run will never make.
    """
    board = _board_with(
        [("character", {"mediaId": "m-face"}),
         ("video", {"sourceSettings": {"duration": "8s"}})],
        [(0, 1, "character_1")],
    )
    body = _estimate(client, board)
    clip = next(i for i in body["items"] if i["type"] == "video")
    assert clip["jobs"] == 1, f"quoted not-ready: {clip.get('note')!r}"
    assert clip["credits"] == 25
    assert body["billableJobs"] >= 1


def test_a_veo_clip_says_its_length_control_did_nothing(client):
    """A Veo image-to-video key carries its own 8 seconds, and the duration
    control drives the OMNI lane. The value was accepted, forwarded, recorded on
    the request row, and dropped — so the dialog showed a 10s node with a price
    and said nothing about either.

    Not refused: the lane runs, and refusing a clip that works would be worse.
    The sibling text-to-video family DOES refuse an unavailable length, because
    there the duration is part of the key and picking the nearest one would bill a
    different clip than the board asked for.
    """
    board = _board_with(
        [("image", {"prompt": "một khung"}),
         ("video", {"videoQuality": "lite", "durationSeconds": 10})],
        [(0, 1, "start_frame")],
    )
    body = _estimate(client, board)
    clip = next(i for i in body["items"] if i["type"] == "video")
    assert clip["jobs"] == 1, "a wired Veo clip still runs"
    assert "8 giây" in (clip["note"] or "")


def test_a_veo_clip_at_eight_seconds_says_nothing_extra(client):
    """Otherwise the remark becomes noise on every clip and stops being read."""
    board = _board_with(
        [("image", {"prompt": "một khung"}),
         ("video", {"videoQuality": "lite", "durationSeconds": 8})],
        [(0, 1, "start_frame")],
    )
    body = _estimate(client, board)
    clip = next(i for i in body["items"] if i["type"] == "video")
    assert "8 giây" not in (clip["note"] or "")


def test_the_review_loop_now_shows_up_in_the_quote(client):
    """`reviewJobs` counted zero on every board, always.

    `_review_loop_calls` asks `review_loop.settings_from(merged_settings(node))`,
    and the three loop keys were missing from that allow-list — so the loop read as
    off no matter what a node carried, and the range the dialog renders
    ("1–4 lần chấm clip") could never appear. The counter existed, the docstring
    described it, and the number was structurally always 0.
    """
    board = _board_with(
        [("image", {"prompt": "một khung"}),
         ("video", {"videoQuality": "lite",
                    "sourceSettings": {"review_loop": True, "review_max_rounds": 4}})],
        [(0, 1, "start_frame")],
    )
    body = _estimate(client, board)
    # The floor: the first scoring pass always happens when the loop is on.
    assert body["reviewJobs"] >= 1
    # And the ceiling is above it, so the dialog can render a range.
    assert body["reviewJobsMax"] > body["reviewJobs"]


def test_a_board_that_never_asked_for_the_loop_quotes_no_review_calls(client):
    board = _board_with(
        [("image", {"prompt": "một khung"}), ("video", {"videoQuality": "lite"})],
        [(0, 1, "start_frame")],
    )
    body = _estimate(client, board)
    assert body["reviewJobs"] == 0


# ── pricing a scoped run ──────────────────────────────────────────────


def _node_ids_of(board_id: int) -> list[int]:
    with get_session() as s:
        return sorted(
            n.id for n in s.exec(select(Node).where(Node.board_id == board_id)).all()
        )


def test_no_scope_prices_the_whole_board(client):
    board = _board_with([("image", {}), ("video", {})], _I2V_WIRE)
    assert _estimate(client, board)["billableJobs"] == 2


def test_a_scope_prices_only_the_nodes_in_it(client):
    """Quoting twelve jobs for a three-job run makes someone cancel a run they
    could afford, which is the expensive mistake in this direction."""
    board = _board_with([("image", {}), ("video", {})], _I2V_WIRE)
    video = _node_ids_of(board)[1]
    resp = client.get(f"/api/boards/{board}/estimate", params={"node_ids": [video]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["billableJobs"] == 1


def test_a_scoped_clip_still_sees_the_producer_outside_the_scope(client):
    """The graph is read whole even when the pricing loop is narrowed.

    A video wired to a start frame is image-to-video; read alone it looks like
    text-to-video, which is a different key table and a different length rule.
    Scoping the clip on its own must not change what it is.
    """
    board = _board_with(
        [("image", {}), ("video", {"quality": "lite", "duration_s": 10})],
        _I2V_WIRE,
    )
    video = _node_ids_of(board)[1]
    whole = _estimate(client, board)
    scoped = client.get(
        f"/api/boards/{board}/estimate", params={"node_ids": [video]}
    ).json()
    clip_whole = [i for i in whole["items"] if i["nodeId"] == video]
    clip_scoped = [i for i in scoped["items"] if i["nodeId"] == video]
    assert clip_whole and clip_scoped
    # Same line item both ways: the price, the job count, and the reason the
    # price is unknown. Any of the three differing means the scoped read
    # decided it was a different kind of clip.
    assert clip_whole[0]["credits"] == clip_scoped[0]["credits"]
    assert clip_whole[0]["jobs"] == clip_scoped[0]["jobs"]
    assert clip_whole[0]["note"] == clip_scoped[0]["note"]
    assert whole["notReadyJobs"] >= scoped["notReadyJobs"]


def test_an_unknown_id_in_the_scope_does_not_break_the_quote(client):
    """A quote is read-only, so a stale id should not blank the dialog. The
    gate that refuses strangers is `ensure_board_plan`, before any dispatch."""
    board = _board_with([("image", {}), ("video", {})], _I2V_WIRE)
    ids = _node_ids_of(board)
    resp = client.get(
        f"/api/boards/{board}/estimate", params={"node_ids": [ids[1], 999999]}
    )
    assert resp.status_code == 200
    assert resp.json()["billableJobs"] == 1
