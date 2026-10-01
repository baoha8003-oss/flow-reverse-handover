"""Tests for the worker processor's paygate_tier resolution chain.

The handler reads tier from two sources in priority order:
  1. params["paygate_tier"] — stamped by the frontend at dispatch
  2. flow_client.paygate_tier — live if a pre-migration session resolved one,
     otherwise the value the user picked in Settings

**Missing is now an ordinary state, and dispatch proceeds.** It used to be a
hard refusal (`paygate_tier_unknown`), which was right while the REST payload
carried `userPaygateTier`: defaulting to TIER_ONE served Ultra users at the Pro
checkpoint and stamped the wrong tier into the DB, poisoning /api/auth/me.
Google's September 2026 migration removed that field from the wire, so there is
nothing left to get wrong — and the tier can no longer resolve by itself, so
refusing would mean an app that cannot generate anything until someone fills in
a dropdown. What the tests below pin instead: no tier is NOT coerced to
TIER_ONE, and an unknown tier never reaches the payload.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from flowboard.services.flow_client import flow_client
from flowboard.worker import processor as proc


@pytest.fixture(autouse=True)
def _reset_flow_client_tier():
    flow_client._paygate_tier = None
    yield
    flow_client._paygate_tier = None


@pytest.mark.asyncio
async def test_gen_image_uses_caller_stamped_tier_first():
    """When the dispatch stamps a tier into params, that wins —
    caller intent always beats the live signal."""
    flow_client._paygate_tier = "PAYGATE_TIER_TWO"

    with patch("flowboard.worker.processor.get_flow_sdk") as m:
        m.return_value.gen_image = AsyncMock(return_value={
            "media_ids": ["m"],
            "media_entries": [],
        })
        await proc._handle_gen_image({
            "prompt": "x",
            "project_id": "8b62385c-4916-4abd-b01f-b28173d8eb04",
            "paygate_tier": "PAYGATE_TIER_ONE",  # explicit caller value
        })
        kwargs = m.return_value.gen_image.call_args.kwargs
        assert kwargs["paygate_tier"] == "PAYGATE_TIER_ONE"


@pytest.mark.asyncio
async def test_gen_image_falls_back_to_live_flow_client_tier():
    """No paygate_tier in params + flow_client has one cached →
    handler must pick up the live signal instead of defaulting to
    TIER_ONE. This is the case we regressed away from before #20:
    legacy frontends that don't stamp tier still got the right tier
    once the extension sniffed it."""
    flow_client._paygate_tier = "PAYGATE_TIER_TWO"

    with patch("flowboard.worker.processor.get_flow_sdk") as m:
        m.return_value.gen_image = AsyncMock(return_value={
            "media_ids": ["m"],
            "media_entries": [],
        })
        await proc._handle_gen_image({
            "prompt": "x",
            "project_id": "8b62385c-4916-4abd-b01f-b28173d8eb04",
            # no paygate_tier — relies on the fallback chain
        })
        kwargs = m.return_value.gen_image.call_args.kwargs
        assert kwargs["paygate_tier"] == "PAYGATE_TIER_TWO"


@pytest.mark.asyncio
async def test_gen_image_dispatches_without_any_tier_signal():
    """No caller-stamped tier, nothing in Settings → the handler still
    dispatches, and passes tier=None rather than inventing PAYGATE_TIER_ONE.

    The silent-Pro-downgrade guard now lives in `_client_context`, which omits
    the field entirely (see test_flow_sdk). Refusing here would strand every
    generation behind a dropdown that nothing can fill automatically.
    """
    flow_client._paygate_tier = None

    with patch("flowboard.worker.processor.get_flow_sdk") as m:
        m.return_value.gen_image = AsyncMock(return_value={
            "media_ids": ["m"],
            "media_entries": [],
        })
        _result, err = await proc._handle_gen_image({
            "prompt": "x",
            "project_id": "8b62385c-4916-4abd-b01f-b28173d8eb04",
        })
        assert err is None
        _args, kwargs = m.return_value.gen_image.call_args
        assert kwargs["paygate_tier"] is None


@pytest.mark.asyncio
async def test_gen_video_dispatches_without_any_tier_signal():
    """Same, gen_video path."""
    flow_client._paygate_tier = None

    with patch("flowboard.worker.processor.get_flow_sdk") as m:
        m.return_value.gen_video = AsyncMock(return_value={"error": "stop here"})
        _result, err = await proc._handle_gen_video({
            "prompt": "x",
            "project_id": "8b62385c-4916-4abd-b01f-b28173d8eb04",
            "start_media_id": "src-1",
        })
        assert err == "stop here"
        _args, kwargs = m.return_value.gen_video.call_args
        assert kwargs["paygate_tier"] is None


@pytest.mark.asyncio
async def test_edit_image_dispatches_without_any_tier_signal():
    """Same, edit_image path."""
    flow_client._paygate_tier = None

    with patch("flowboard.worker.processor.get_flow_sdk") as m:
        m.return_value.edit_image = AsyncMock(return_value={
            "media_ids": ["m"],
            "media_entries": [],
        })
        _result, err = await proc._handle_edit_image({
            "prompt": "make it pop",
            "project_id": "8b62385c-4916-4abd-b01f-b28173d8eb04",
            "source_media_id": "src-1",
        })
        assert err is None
        _args, kwargs = m.return_value.edit_image.call_args
        assert kwargs["paygate_tier"] is None


@pytest.mark.asyncio
async def test_the_tier_label_comes_from_settings_when_nothing_is_live():
    """The Settings value is the only source left on the migrated transport."""
    from flowboard.services import settings_store

    flow_client._paygate_tier = None
    settings_store.set_many({"FLOW_PAYGATE_TIER": "PAYGATE_TIER_TWO"})
    assert proc._tier_label({}) == "PAYGATE_TIER_TWO"
    # A caller-stamped value still wins — that is what a re-run of an old row
    # carries, and it describes what that row actually dispatched with.
    assert proc._tier_label({"paygate_tier": "PAYGATE_TIER_ONE"}) == "PAYGATE_TIER_ONE"


@pytest.mark.asyncio
async def test_gen_video_applies_same_resolution_chain():
    """Resolution chain must be consistent across handlers — gen_video
    has its own copy of the lookup, so verify it behaves the same."""
    flow_client._paygate_tier = "PAYGATE_TIER_TWO"

    with patch("flowboard.worker.processor.get_flow_sdk") as m:
        # Stub the dispatch to return a synthesised "no operations"
        # so the handler exits before polling. We only care about the
        # tier arg passed to gen_video.
        m.return_value.gen_video = AsyncMock(return_value={
            "operation_names": [],
        })
        await proc._handle_gen_video({
            "prompt": "x",
            "project_id": "8b62385c-4916-4abd-b01f-b28173d8eb04",
            "start_media_id": "src-1",
            # no paygate_tier — fallback path
        })
        kwargs = m.return_value.gen_video.call_args.kwargs
        assert kwargs["paygate_tier"] == "PAYGATE_TIER_TWO"


@pytest.mark.asyncio
async def test_edit_image_applies_same_resolution_chain():
    """Third handler — same chain, same expectation."""
    flow_client._paygate_tier = "PAYGATE_TIER_TWO"

    with patch("flowboard.worker.processor.get_flow_sdk") as m:
        m.return_value.edit_image = AsyncMock(return_value={
            "media_ids": ["m"],
            "media_entries": [],
        })
        await proc._handle_edit_image({
            "prompt": "make it pop",
            "project_id": "8b62385c-4916-4abd-b01f-b28173d8eb04",
            "source_media_id": "src-1",
        })
        kwargs = m.return_value.edit_image.call_args.kwargs
        assert kwargs["paygate_tier"] == "PAYGATE_TIER_TWO"
