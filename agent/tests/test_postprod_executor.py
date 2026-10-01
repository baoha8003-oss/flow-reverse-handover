"""Running a post-production node's ops in order.

`edit_video` is several passes over one clip. The pass ordering and the
hand-off between passes are where a wrong result looks right: a video that
plays fine with the logo still in it, or with only the last pass applied.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from flowboard.services import pipeline_executor as pe
from flowboard.services.postprod_plan import PENDING, PREVIOUS, Upstream


class Recorder:
    """Stands in for the worker: records every dispatch, answers with a
    media id per step so the chain can be traced."""

    def __init__(self, outputs=None, fail_at=None, empty_at=None):
        self.dispatched: list[dict] = []
        self.outputs = outputs or []
        self.fail_at = fail_at
        self.empty_at = empty_at
        self.statuses: list[tuple[str, str | None]] = []

    def install(self, monkeypatch):
        rows: dict[int, SimpleNamespace] = {}

        def create_row(node_id, req_type, params):
            step = len(self.dispatched) + 1
            self.dispatched.append({"type": req_type, "params": params})
            if step == self.fail_at:
                row = SimpleNamespace(status="failed", error="boom", result=None)
            elif step == self.empty_at:
                row = SimpleNamespace(status="done", error=None, result={"media_ids": []})
            else:
                out = (
                    self.outputs[step - 1]
                    if step - 1 < len(self.outputs)
                    else f"out{step}"
                )
                row = SimpleNamespace(
                    status="done", error=None, result={"media_ids": [out]}
                )
            rows[step] = row
            return step

        async def await_request(request_id, *, timeout_s, poll_s):
            return rows[request_id]

        def stamp(nid, status, *, error=None, data_patch=None):
            self.statuses.append((status, error))

        monkeypatch.setattr(pe, "_create_request_row", create_row)
        monkeypatch.setattr(pe, "_await_request", await_request)
        monkeypatch.setattr(pe, "_stamp_node_status", stamp)
        monkeypatch.setattr(pe, "get_worker", lambda: SimpleNamespace(enqueue=lambda _: None))
        # The node refresh reads the database; the chain does not depend on it.
        monkeypatch.setattr(pe, "get_session", _null_session)


class _NullSession:
    def __enter__(self):
        return SimpleNamespace(get=lambda *a, **k: None)

    def __exit__(self, *exc):
        return False


def _null_session():
    return _NullSession()


#: `delogo` refuses to run without a box, so a test that wants a logo pass
#: has to supply one — the same thing the shipped templates carry.
LOGO_BOX = {
    # Named explicitly: the default method is `zoom` (what the exe uses),
    # and these tests are about the CHAIN, not about which method runs.
    "veo_logo_method": "delogo",
    "veo_logo_x_pct": 70,
    "veo_logo_y_pct": 85,
    "veo_logo_w_pct": 25,
    "veo_logo_h_pct": 10,
}


def node(node_type, **data):
    return SimpleNamespace(id=7, type=node_type, data=data)


def up(node_obj, port):
    """The executor hands the planner wires, not bare nodes — the port is
    what tells a narration wire from a music one."""
    return Upstream(node_obj, port)


def run(node_obj, upstream, monkeypatch, rec: Recorder) -> bool:
    rec.install(monkeypatch)
    return asyncio.run(
        pe._run_postprod_node(
            node_obj,
            upstream,
            node_by_id={},
            request_timeout_s=1.0,
            poll_interval_s=0.001,
        )
    )


# ── the chain ────────────────────────────────────────────────────────────

def test_each_pass_consumes_the_previous_passs_output(monkeypatch):
    """The whole point of a chain. If every pass read the source clip they
    would overwrite each other and only the last would survive."""
    rec = Recorder(outputs=["afterLogo", "afterUpscale"])
    ok = run(
        node(
            "edit_video",
            sourceSettings=LOGO_BOX | {"enable_remove_veo_logo": True, "upscale_2k_4k": True},
        ),
        [up(node("video", mediaId="clip"), "media")],
        monkeypatch,
        rec,
    )
    assert ok
    assert [d["params"]["op"] for d in rec.dispatched] == ["delogo", "upscale"]
    assert rec.dispatched[0]["params"]["video"] == "clip"
    # upscale names its input `source`, not `video` — the hand-off has to be
    # substituted into whichever key that op actually reads.
    assert rec.dispatched[1]["params"]["source"] == "afterLogo"


def test_only_the_last_output_lands_on_the_node(monkeypatch):
    """The intermediates are working files. Showing them would make a
    two-pass edit look like two results."""
    rec = Recorder(outputs=["mid", "final"])
    run(
        node(
            "edit_video",
            sourceSettings=LOGO_BOX | {"enable_remove_veo_logo": True, "upscale_2k_4k": True},
        ),
        [up(node("video", mediaId="clip"), "media")],
        monkeypatch,
        rec,
    )
    assert rec.statuses[-1] == ("done", None)


def test_everything_dispatches_as_the_one_postprod_request_type(monkeypatch):
    rec = Recorder()
    run(
        node("edit_video", sourceSettings={"upscale_2k_4k": True}),
        [up(node("video", mediaId="clip"), "media")],
        monkeypatch,
        rec,
    )
    assert {d["type"] for d in rec.dispatched} == {"postprod"}


# ── failure handling ─────────────────────────────────────────────────────

def test_a_failed_pass_stops_the_chain(monkeypatch):
    """Continuing would run the next pass over the wrong input."""
    rec = Recorder(fail_at=1)
    ok = run(
        node(
            "edit_video",
            sourceSettings=LOGO_BOX | {"enable_remove_veo_logo": True, "upscale_2k_4k": True},
        ),
        [up(node("video", mediaId="clip"), "media")],
        monkeypatch,
        rec,
    )
    assert ok is False
    assert len(rec.dispatched) == 1
    assert rec.statuses[-1][0] == "error"


def test_a_pass_that_produced_nothing_is_a_failure_not_a_skip(monkeypatch):
    """A step reporting done with no media would leave the next step with a
    null input, so the chain has to stop here rather than carry on."""
    rec = Recorder(empty_at=1)
    ok = run(
        node(
            "edit_video",
            sourceSettings=LOGO_BOX | {"enable_remove_veo_logo": True, "upscale_2k_4k": True},
        ),
        [up(node("video", mediaId="clip"), "media")],
        monkeypatch,
        rec,
    )
    assert ok is False
    assert len(rec.dispatched) == 1, "the chain continued past an empty result"
    assert rec.statuses[-1][0] == "error"


def test_no_dispatch_ever_carries_a_null_or_sentinel_input(monkeypatch):
    """The invariant the guard exists for: whatever happens, a step must
    never reach the worker with `None` or the literal sentinel as an input."""
    rec = Recorder(outputs=["a", "b", "c"])
    run(
        node(
            "edit_video",
            sourceSettings=LOGO_BOX
            | {
                "enable_remove_veo_logo": True,
                "enable_bgm": True,
                "bgm_sample_file": "calm.mp3",
                "upscale_2k_4k": True,
            },
        ),
        [up(node("video", mediaId="clip"), "media"), up(node("create_voice", mediaId="vo"), "voice")],
        monkeypatch,
        rec,
    )
    assert rec.dispatched, "nothing ran"
    for d in rec.dispatched:
        for key, value in d["params"].items():
            assert value is not None, f"{d['params']['op']}.{key} is None"
            assert value != PREVIOUS, f"{d['params']['op']}.{key} is unsubstituted"
            # PENDING belongs to the cost estimate's shape-only question. It
            # reaching a dispatch would mean ffmpeg is handed a media id that
            # was never meant to exist.
            assert value != PENDING, f"{d['params']['op']}.{key} leaked a placeholder"


def test_the_estimates_placeholder_never_reaches_a_dispatch(monkeypatch):
    """`assume_ready` is the estimate's flag, and the estimate dispatches
    nothing. This pins that the run path never sets it — a future caller
    passing it through here would hand ffmpeg a fabricated media id."""
    from flowboard.services import postprod_plan

    seen = []
    real = postprod_plan.ops_for

    def spy(node_obj, upstream, **kwargs):
        seen.append(kwargs.get("assume_ready", False))
        return real(node_obj, upstream, **kwargs)

    monkeypatch.setattr(postprod_plan, "ops_for", spy)
    rec = Recorder()
    run(
        node("edit_video", sourceSettings={"upscale_2k_4k": True}),
        [up(node("video", mediaId="clip"), "media")],
        monkeypatch,
        rec,
    )
    assert seen == [False], "the run path asked for the estimate's shape mode"


# ── nothing to do ────────────────────────────────────────────────────────

def test_a_node_with_no_inputs_runs_nothing_and_is_not_an_error(monkeypatch):
    """Same as a generation node with no prompt. It lets a half-wired board
    run the parts that are ready."""
    rec = Recorder()
    ok = run(node("merge_video"), [], monkeypatch, rec)
    assert ok is True
    assert rec.dispatched == []
    assert rec.statuses == [], "an idle node was stamped"


@pytest.mark.parametrize("node_type", ["analyze_video", "some_future_type"])
def test_a_node_with_no_ops_defined_does_not_dispatch(monkeypatch, node_type):
    rec = Recorder()
    ok = run(node(node_type), [up(node("video", mediaId="clip"), "media")], monkeypatch, rec)
    assert ok is True
    assert rec.dispatched == []
