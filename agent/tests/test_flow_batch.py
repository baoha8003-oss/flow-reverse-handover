"""The batchexecute codec, builders and readers.

These lock down the slots that cost hours to find — see `docs/flow-capture.md`
and the comments in `flow_batch.py`. A payload Flow accepts and then ignores
looks exactly like a payload that worked, so the assertions here are about
position, not shape.

**Ported from flowkit** (MIT) v1.2.0 `tests/unit/test_flow_batch.py`, adapted
where this build deviates: no image-upscale RPC, no model-folding resolver, and
`flow_sdk` owns the image nickname resolver.
"""
import json

import pytest

from flowboard.services import flow_batch as fb


def envelope(rpcid: str, payload) -> str:
    """A response body as batchexecute serves it: sentinel, then chunks."""
    chunk = json.dumps([["wrb.fr", rpcid, json.dumps(payload)]])
    return f")]}}'\n{len(chunk)}\n{chunk}"


def inner(freq: str):
    """The inner payload back out of an f.req envelope."""
    return json.loads(json.loads(freq)[0][0][1])


class TestEnvelopeCodec:
    def test_build_wraps_inner_as_a_json_string(self):
        freq = fb.build_envelope("rpc1", [1, "two"])
        assert json.loads(freq) == [[["rpc1", '[1,"two"]', None, "generic"]]]

    def test_parse_reads_a_payload_back(self):
        results = fb.parse_envelope(envelope("rpc1", {"a": 1}))
        assert [(r.rpcid, r.data) for r in results] == [("rpc1", {"a": 1})]

    def test_parse_survives_a_truncated_tail(self):
        """A response cut mid-chunk must not cost us the envelopes before it."""
        body = envelope("rpc1", {"a": 1}) + '\n50\n[["wrb.fr","rpc2","[1,2'
        results = fb.parse_envelope(body)
        assert [r.rpcid for r in results] == ["rpc1"]

    def test_parse_tolerates_a_missing_sentinel(self):
        chunk = json.dumps([["wrb.fr", "rpc1", '{"a":1}']])
        assert fb.parse_envelope(chunk)[0].data == {"a": 1}

    def test_empty_body_is_no_results_not_a_crash(self):
        assert fb.parse_envelope("") == []

    def test_error_slot_becomes_an_error_result(self):
        chunk = json.dumps([["wrb.fr", "rpc1", None, None, None, [8]]])
        result = fb.parse_envelope(f")]}}'\n{len(chunk)}\n{chunk}")[0]
        assert not result.ok and result.error == [8]

    def test_first_payload_raises_on_the_error_slot(self):
        chunk = json.dumps([["wrb.fr", "rpc1", None, None, None, [8]]])
        with pytest.raises(fb.RpcError):
            fb.first_payload(f")]}}'\n{len(chunk)}\n{chunk}", "rpc1")

    def test_first_payload_raises_when_the_rpc_is_absent(self):
        with pytest.raises(fb.FlowBatchError):
            fb.first_payload(envelope("other", [1]), "rpc1")


class TestImageRequest:
    PID = "11111111-2222-3333-4444-555555555555"

    def test_aspect_lands_in_slot_4_not_a_variant_count(self):
        """Slot 4 is the aspect ratio. `count=1` only looked right because
        1 means square."""
        item = inner(fb.image_request("a cat", self.PID,
                                      aspect="IMAGE_ASPECT_RATIO_LANDSCAPE"))[1][0]
        assert item[4] == fb.ASPECT_LANDSCAPE

    def test_count_repeats_the_item_under_fresh_seeds(self):
        items = inner(fb.image_request("a cat", self.PID, count=3, seed=100))[1]
        assert len(items) == 3
        assert [i[3] for i in items] == [100, 100 + 9973, 100 + 2 * 9973]

    def test_prompts_give_each_variant_its_own_text(self):
        items = inner(fb.image_request("fallback", self.PID, count=3,
                                       prompts=["one", "two"]))[1]
        assert [i[8][0][0][0] for i in items] == ["one", "two", "fallback"]

    def test_reference_puts_the_media_id_first_and_the_type_flag_fourth(self):
        """The arrangement probing never found: wrong ones are accepted and
        then quietly ignored."""
        item = inner(fb.image_request("a cat", self.PID, ref_media_ids=["mid-1"]))[1][0]
        assert item[2] == [["mid-1", None, None, None, fb.REF_TYPE_IMAGE]]

    def test_base_image_and_references_use_distinct_wire_types(self):
        item = inner(fb.image_request(
            "make the boat blue", self.PID,
            base_media_id="base-1", ref_media_ids=["ref-1", "base-1"],
        ))[1][0]
        assert item[2] == [
            ["base-1", None, None, None, fb.BASE_TYPE_IMAGE],
            ["ref-1", None, None, None, fb.REF_TYPE_IMAGE],
        ]

    @pytest.mark.parametrize("bad_count", [0, 5, -1, True])
    def test_count_outside_flow_ui_range_is_rejected(self, bad_count):
        with pytest.raises(ValueError):
            fb.image_request("a cat", self.PID, count=bad_count)

    def test_no_references_leaves_the_slot_null_rather_than_empty(self):
        assert inner(fb.image_request("a cat", self.PID))[1][0][2] is None

    def test_the_captcha_placeholder_is_present_for_the_extension_to_replace(self):
        assert fb.CAPTCHA_SLOT in fb.image_request("a cat", self.PID)

    def test_the_project_id_rides_in_the_context(self):
        assert inner(fb.image_request("a cat", self.PID))[1][0][7][5] == self.PID

    def test_the_model_is_named_in_slot_5(self):
        item = inner(fb.image_request("a cat", self.PID, model="NARWHAL"))[1][0]
        assert item[5] == "NARWHAL"

    def test_a_nickname_never_reaches_the_wire(self):
        """Upstream resolves nicknames here AND lets unknown ids through, so a
        newly exposed model works before its next release. This build keeps one
        resolver (`flow_sdk.resolve_image_model`) and a closed set on the wire:
        an unknown key is never assumed to be anything."""
        with pytest.raises(ValueError, match="not a Flow wire id"):
            fb.image_request("a cat", self.PID, model="NANO_BANANA_PRO")
        with pytest.raises(ValueError, match="not a Flow wire id"):
            fb.image_request("a cat", self.PID, model="FUTURE_BANANA_3")


class TestVideoRequest:
    PID = "11111111-2222-3333-4444-555555555555"

    def test_video_aspect_does_not_share_the_image_encoding(self):
        """1 is portrait here; for an image 1 is square."""
        payload = inner(fb.video_request("go", self.PID, "mid",
                                         aspect="VIDEO_ASPECT_RATIO_PORTRAIT"))
        assert payload[0][0][2] == fb.VIDEO_ASPECT_PORTRAIT

    def test_an_image_aspect_is_refused_rather_than_rendered_wrong(self):
        with pytest.raises(ValueError):
            fb.video_request("go", self.PID, "mid", aspect=fb.ASPECT_LANDSCAPE)

    def test_the_source_media_id_and_a_full_frame_crop_travel_together(self):
        block = inner(fb.video_request("go", self.PID, "mid-9"))[0][0][4]
        assert block[1] == "mid-9"
        assert block[5] == fb.FULL_FRAME_CROP

    def test_a_hand_reframed_crop_overrides_the_default(self):
        crop = [None, 0.1, 1, 0.9]
        assert inner(fb.video_request("go", self.PID, "mid", crop=crop))[0][0][4][5] == crop

    def test_omni_first_frame_360p_matches_live_eb1hjf_shape(self):
        payload = inner(fb.omni_first_frame_request(
            "move", self.PID, "start-mid", duration_s=4, resolution="360p",
            aspect="VIDEO_ASPECT_RATIO_LANDSCAPE",
        ))
        request = payload[0][0]
        assert request[1] == "abra_i2v_4s_360p"
        assert request[2] == fb.VIDEO_ASPECT_LANDSCAPE
        assert request[4][1] == "start-mid"
        assert request[-1] == [4]
        assert json.loads(fb.omni_first_frame_request("x", self.PID, "m"))[0][0][0] == fb.RPC_GEN_VIDEO

    def test_omni_first_last_matches_live_nprqif_shape(self):
        freq = fb.omni_first_last_request(
            "morph", self.PID, "start", "end", duration_s=6, resolution="720p",
            aspect="VIDEO_ASPECT_RATIO_PORTRAIT",
        )
        assert json.loads(freq)[0][0][0] == fb.RPC_GEN_VIDEO_FIRST_LAST
        request = inner(freq)[0][0]
        assert request[1] == "omni_flash_i2v_6s_first_last"
        assert request[2] == fb.VIDEO_ASPECT_PORTRAIT
        assert request[4][1] == "start"
        assert request[5][1] == "end"

    def test_omni_reference_matches_live_mzza6b_shape(self):
        freq = fb.omni_reference_video_request(
            "keep refs", self.PID, ["a", "b"], duration_s=4, resolution="360p",
            aspect="VIDEO_ASPECT_RATIO_LANDSCAPE",
        )
        assert json.loads(freq)[0][0][0] == fb.RPC_GEN_VIDEO_REFERENCES
        request = inner(freq)[0][0]
        assert request[1] == [[None, "a"], [None, "b"]]
        assert request[2] == "abra_r2v_4s_360p"
        assert request[3] == fb.VIDEO_ASPECT_LANDSCAPE
        assert request[-1] == [4]

    def test_text_video_matches_the_captured_yhhmef_shape(self):
        payload = inner(fb.text_video_request(
            "a boat", self.PID,
            aspect="VIDEO_ASPECT_RATIO_LANDSCAPE",
            model="abra_t2v_4s",
        ))
        request = payload[0][0]
        assert request[0] == [None, None, [[["a boat"]]]]
        assert request[1] == "abra_t2v_4s"
        assert request[2] == fb.VIDEO_ASPECT_LANDSCAPE
        assert request[3] is None
        assert len(request[4]) == 6
        assert payload[1][5] == self.PID
        assert payload[2][1] == 1
        assert fb.CAPTCHA_SLOT in json.dumps(payload)


class TestCreateProject:
    """From flowkit commit 2eb04f9 — reverted there with an unrelated feature."""

    def test_the_title_rides_in_the_captured_slot(self):
        payload = inner(fb.create_project_request("My Film"))
        assert payload == ["projects/*", [None, ["My Film"]], [None, fb.SURFACE_ID]]

    def test_the_created_id_is_read_from_slot_0_only(self):
        pid = "11111111-2222-3333-4444-555555555555"
        assert fb.read_created_project_id([pid, ["My Film"]]) == pid

    @pytest.mark.parametrize("payload", [
        ["not-a-uuid"],
        [None, ["11111111-2222-3333-4444-555555555555"]],
        [],
        {},
    ])
    def test_anything_but_a_uuid_in_slot_0_is_a_failure(self, payload):
        """A 200 with the id somewhere unexpected is accepted and then silently
        useless — so this refuses rather than scanning for a uuid-ish string."""
        with pytest.raises(fb.FlowBatchError):
            fb.read_created_project_id(payload)


class TestVideoModelGuard:
    """Refuse, never fold — the money rule that differs from upstream."""

    def test_an_accepted_key_passes_through(self):
        for key in fb.VIDEO_MODELS:
            assert fb.check_video_model(key) == key

    @pytest.mark.parametrize("rejected", [
        # The one that matters: upstream's resolver matches the substring
        # "ultra" and folds this 0-credit key onto the PAID fast_ultra one.
        "veo_3_1_i2v_s_fast_ultra_relaxed",
        "veo_3_1_i2v_lite_low_priority_relaxed",
        "veo_3_1_i2v_s_fast_portrait",
        "veo_3_1_i2v_s_fast_fl",
        "veo_3_1_r2v_fast_portrait",
        "veo_3_1_t2v_lite",
        "",
        None,
    ])
    def test_a_key_flow_does_not_accept_is_refused_not_folded(self, rejected):
        with pytest.raises(ValueError, match="not one Flow accepts"):
            fb.check_video_model(rejected)

    def test_the_only_free_key_is_the_low_priority_one(self):
        """Pinned here because every "may I spend this automatically?" decision
        downstream reads it."""
        assert "veo_3_1_i2v_lite_low_priority" in fb.VIDEO_MODELS
        assert fb.VIDEO_MODEL == "veo_3_1_i2v_lite_low_priority"


class TestReaders:
    OP = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    MID = "12345678-1234-1234-1234-1234567890ab"

    def test_images_are_read_out_of_the_url_path(self):
        url = f"https://{fb.MEDIA_HOST}/image/{self.MID}?sig=x"
        images = fb.read_images(["noise", [url, "more"]])
        assert images == [fb.GeneratedImage(media_id=self.MID, url=url)]

    def test_a_repeated_url_is_not_a_second_variant(self):
        url = f"https://{fb.MEDIA_HOST}/image/{self.MID}?sig=x"
        assert len(fb.read_images([url, url])) == 1

    def test_text_video_submit_reads_media_and_workflow_ids(self):
        payload = [None, 10, [], [[self.MID, "project-1", self.OP, "CAE"]]]
        assert fb.read_text_video_submit(payload) == {
            "media_id": self.MID,
            "project_id": "project-1",
            "workflow_id": self.OP,
            "status": "CAE",
        }

    def test_operation_reads_the_id_and_status(self):
        op = fb.read_operation([None, 50, [[self.OP, "proj", "scene", "CAE"]]])
        assert (op.operation_id, op.status, op.done) == (self.OP, "CAE", True)

    def test_the_third_uuid_is_the_scene_and_is_never_taken_as_media(self):
        """Feeding it to the media rpc answers NOT_FOUND forever."""
        record = [self.OP, "proj", "scene-uuid", "CAE"]
        op = fb.read_operation([None, 50, [record]])
        assert "scene-uuid" not in (op.operation_id, op.project_id, op.status)

    def test_a_complaint_is_carried_but_is_not_a_terminal_status(self):
        detail = [None] * 8 + [[fb.OUTCOME_COMPLAINT, [None, "Media not found."]]]
        op = fb.read_operation([None, 50, [[self.OP, "proj", "scene", None, None, detail]]])
        assert op.complained and op.error == "Media not found."
        assert not op.done

    def test_a_healthy_outcome_carries_no_complaint(self):
        detail = [None] * 8 + [[fb.OUTCOME_OK]]
        op = fb.read_operation([None, 50, [[self.OP, "p", "s", "CAE", None, detail]]])
        assert op.error is None

    def test_an_empty_operation_payload_raises(self):
        with pytest.raises(fb.FlowBatchError):
            fb.read_operation([None, 50, []])

    def test_the_media_id_is_found_in_an_unparsable_listing(self):
        """The listing outgrows any response cap; a truncated tail still holds
        the entry we came for."""
        text = f'["{self.OP}",null,null,["title",1,2,null,null,"{self.MID}"],"proj' 
        assert fb.find_media_id_in_text(text, self.OP) == self.MID

    def test_an_absent_operation_reads_as_not_there_yet(self):
        assert fb.find_media_id_in_text("nothing here", self.OP) is None

    def test_the_media_id_is_found_in_a_decoded_listing_too(self):
        payload = [[self.OP, None, None, ["t", 1, None, None, self.MID], "proj"]]
        assert fb.find_media_id(payload, self.OP) == self.MID

    def test_urls_are_split_by_kind(self):
        video = f"https://{fb.MEDIA_HOST}/video/{self.MID}?s=1"
        image = f"https://{fb.MEDIA_HOST}/image/{self.MID}?s=1"
        urls = fb.read_media_urls([image, video], self.MID)
        assert (urls.video, urls.image) == (video, image)

    def test_a_poster_only_record_has_no_video_yet(self):
        """A media id arrives before the clip is fetchable; downloading on the
        id alone saves a still picture."""
        image = f"https://{fb.MEDIA_HOST}/image/{self.MID}?s=1"
        assert fb.read_media_urls([image], self.MID).video is None

    def test_uploaded_media_id_is_the_first_slot(self):
        assert fb.read_uploaded_media_id([[self.MID, "proj", "op", "CAE"]]) == self.MID

    def test_an_upload_with_no_id_raises(self):
        with pytest.raises(fb.FlowBatchError):
            fb.read_uploaded_media_id([[]])


class TestResolvers:
    def test_rest_era_aspect_names_still_work(self):
        assert fb.resolve_aspect("IMAGE_ASPECT_RATIO_PORTRAIT") == fb.ASPECT_PORTRAIT

    def test_an_unknown_aspect_name_raises_rather_than_defaulting(self):
        with pytest.raises(ValueError):
            fb.resolve_aspect("IMAGE_ASPECT_RATIO_CINEMA")

    @pytest.mark.parametrize("name,expected", [
        ("1:1", fb.ASPECT_SQUARE),
        ("9:16", fb.ASPECT_PORTRAIT),
        ("16:9", fb.ASPECT_LANDSCAPE),
        ("3:4", fb.ASPECT_PORTRAIT_4_3),
        ("4:3", fb.ASPECT_LANDSCAPE_4_3),
        ("IMAGE_ASPECT_RATIO_PORTRAIT_THREE_FOUR", fb.ASPECT_PORTRAIT_4_3),
        ("IMAGE_ASPECT_RATIO_PORTRAIT_FOUR_THREE", fb.ASPECT_PORTRAIT_4_3),
    ])
    def test_all_current_image_aspect_names_and_friendly_aliases(self, name, expected):
        assert fb.resolve_aspect(name) == expected

    def test_invalid_integer_image_aspect_is_rejected(self):
        with pytest.raises(ValueError):
            fb.resolve_aspect(6)


class TestRpcReason:
    """Flow's own error name, read out of the detail envelope.

    Captured live on a Pro account 19/09/2026 by asking for the 0-credit lane.
    The shape is a gRPC status: a numeric code, then an ErrorInfo block holding
    the reason. Both halves matter — 7 and 8 are treated differently — so the
    example here is the real one rather than a simplified stand-in.
    """

    LIVE = [
        7, None,
        [["type.googleapis.com/google.rpc.ErrorInfo",
          ["PUBLIC_ERROR_MODEL_ACCESS_DENIED"]]],
    ]

    def test_it_finds_the_reason_in_the_captured_shape(self):
        assert fb.read_rpc_reason(self.LIVE) == "PUBLIC_ERROR_MODEL_ACCESS_DENIED"

    def test_a_detail_with_no_public_error_name_yields_none(self):
        # Not "unknown", not a guess: the caller falls back to the exception
        # type, which at least says which layer refused.
        assert fb.read_rpc_reason([fb.RPC_RESOURCE_EXHAUSTED]) is None
        assert fb.read_rpc_reason(None) is None
        assert fb.read_rpc_reason(["something else"]) is None

    def test_the_two_status_codes_we_act_on_are_named(self):
        """8 is worth a retry and 7 never is. Naming them stops the difference
        living as a bare integer in a comparison."""
        assert fb.RPC_RESOURCE_EXHAUSTED == 8
        assert fb.RPC_PERMISSION_DENIED == 7
