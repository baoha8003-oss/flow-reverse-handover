"""The worker must process more than one request at a time (the whole point
of the P2 change) while spacing dispatches apart. These construct a
WorkerController with explicit overrides because conftest pins the global
config to serial/no-cooldown for the other tests' determinism.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from flowboard.worker.processor import WorkerController


def _enqueue_row(client, marker: str) -> int:
    return client.post(
        "/api/requests",
        json={"type": "proxy", "params": {"marker": marker}},
    ).json()["id"]


@pytest.mark.asyncio
async def test_two_requests_run_at_once_with_max_concurrent_2(client):
    """With max_concurrent=2, two slow handlers overlap in time. A serial
    worker would run them back-to-back (~2×) instead."""
    peak = 0
    inflight = 0
    lock = asyncio.Lock()

    async def slow(_params):
        nonlocal peak, inflight
        async with lock:
            inflight += 1
            peak = max(peak, inflight)
        await asyncio.sleep(0.3)
        async with lock:
            inflight -= 1
        return ({"ok": True}, None)

    ids = [_enqueue_row(client, f"m{i}") for i in range(2)]
    w = WorkerController(handlers={"proxy": slow}, max_concurrent=2, cooldown_s=0)
    task = asyncio.create_task(w.start())
    try:
        for rid in ids:
            w.enqueue(rid)
        for _ in range(60):
            await asyncio.sleep(0.05)
            done = [
                client.get(f"/api/requests/{rid}").json()["status"] == "done"
                for rid in ids
            ]
            if all(done):
                break
        assert all(done)
        assert peak == 2, f"expected 2 in flight at once, saw {peak}"
    finally:
        w.request_shutdown()
        await asyncio.wait_for(task, timeout=3.0)


@pytest.mark.asyncio
async def test_max_concurrent_1_never_overlaps(client):
    """The pinned-serial config must genuinely serialize."""
    peak = 0
    inflight = 0
    lock = asyncio.Lock()

    async def slow(_params):
        nonlocal peak, inflight
        async with lock:
            inflight += 1
            peak = max(peak, inflight)
        await asyncio.sleep(0.2)
        async with lock:
            inflight -= 1
        return ({"ok": True}, None)

    ids = [_enqueue_row(client, f"s{i}") for i in range(3)]
    w = WorkerController(handlers={"proxy": slow}, max_concurrent=1, cooldown_s=0)
    task = asyncio.create_task(w.start())
    try:
        for rid in ids:
            w.enqueue(rid)
        for _ in range(80):
            await asyncio.sleep(0.05)
            if all(
                client.get(f"/api/requests/{rid}").json()["status"] == "done"
                for rid in ids
            ):
                break
        assert peak == 1, f"serial worker overlapped: peak={peak}"
    finally:
        w.request_shutdown()
        await asyncio.wait_for(task, timeout=3.0)


@pytest.mark.asyncio
async def test_cooldown_spaces_dispatches(client):
    """cooldown_s must delay each successive dispatch, even at concurrency 2."""
    starts: list[float] = []

    async def stamp(_params):
        starts.append(time.monotonic())
        return ({"ok": True}, None)

    ids = [_enqueue_row(client, f"c{i}") for i in range(3)]
    w = WorkerController(handlers={"proxy": stamp}, max_concurrent=2, cooldown_s=0.2)
    task = asyncio.create_task(w.start())
    try:
        for rid in ids:
            w.enqueue(rid)
        for _ in range(120):
            await asyncio.sleep(0.05)
            if len(starts) >= 3:
                break
        assert len(starts) == 3
        gaps = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)]
        # Each dispatch waited ~0.2s after the previous (allow slack).
        assert all(g >= 0.15 for g in gaps), f"dispatches not spaced: {gaps}"
    finally:
        w.request_shutdown()
        await asyncio.wait_for(task, timeout=3.0)
