"""A FlowClient whose transport replays canned batchexecute responses.

Every test that used to fake `api_request` fakes `batch_rpc` instead. The shape
is deliberately the same as the real thing — a `)]}'` sentinel, a length prefix,
`["wrb.fr", rpcid, "<payload as a JSON string>"]` — so a test exercises the real
parser rather than a convenient stand-in for it. `calls` records what each RPC
was asked, which is what lets a test assert on the ENVELOPE as well as on what
came back: for a payload Flow accepts and then quietly ignores, the envelope is
the only place the mistake is visible.

Modelled on flowkit's `tests/unit/test_flow_client_batch.py` fixture (MIT).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional, Union

from flowboard.services import flow_batch as fb

#: A canned answer: either a dict, or a callable taking the `match` argument so
#: a test can vary the reply per lookup (the project listing does that).
Canned = Union[dict, Callable[[Optional[str]], dict]]


def envelope(rpcid: str, payload: Any) -> str:
    """A response body as batchexecute serves it: sentinel, then a chunk."""
    chunk = json.dumps([["wrb.fr", rpcid, json.dumps(payload)]])
    return f")]}}'\n{len(chunk)}\n{chunk}"


def rpc_error(rpcid: str, detail: Any = None) -> str:
    """An envelope with the error slot filled.

    `[8]` is the transient generation rejection Flow returns under image load;
    anything else is treated as terminal. The detail rides in slot 5.
    """
    chunk = json.dumps([["wrb.fr", rpcid, None, None, None, detail or [8]]])
    return f")]}}'\n{len(chunk)}\n{chunk}"


def inner(freq: str) -> Any:
    """The inner payload back out of an `f.req` envelope."""
    return json.loads(json.loads(freq)[0][0][1])


class BatchFakeClient:
    """Stands in for `flow_client` in `FlowSDK(client=…)`.

    Only `batch_rpc` is implemented, because after the migration that is the
    only method the SDK uses. A test that reaches for anything else is asking
    for a transport this build no longer has, and the AttributeError says so.
    """

    def __init__(self, responses: Optional[dict[str, Canned]] = None) -> None:
        #: rpcid → canned answer. Missing means "an empty body", which is what
        #: an unparseable reply looks like — not a crash.
        self.responses: dict[str, Canned] = dict(responses or {})
        self.calls: list[dict[str, Any]] = []

    async def batch_rpc(
        self,
        rpcid: str,
        freq: str,
        captcha_action: Optional[str] = None,
        match: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> dict:
        self.calls.append({
            "rpcid": rpcid,
            "freq": freq,
            "captcha": captcha_action,
            "match": match,
            "timeout": timeout,
        })
        canned = self.responses.get(rpcid, {"data": ""})
        return canned(match) if callable(canned) else canned

    # ── conveniences for assertions ───────────────────────────────────

    def calls_for(self, rpcid: str) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["rpcid"] == rpcid]

    def payload_for(self, rpcid: str, index: int = 0) -> Any:
        """The inner payload of the n-th call to ``rpcid``."""
        return inner(self.calls_for(rpcid)[index]["freq"])

    def reply(self, rpcid: str, payload: Any) -> None:
        self.responses[rpcid] = {"data": envelope(rpcid, payload)}

    def fail(self, rpcid: str, detail: Any = None) -> None:
        self.responses[rpcid] = {"data": rpc_error(rpcid, detail)}

    def transport_error(self, rpcid: str, error: str) -> None:
        """What the extension answers when it never got to send the RPC."""
        self.responses[rpcid] = {"error": error}


def image_reply(media_id: str) -> Any:
    """An `ogiZ0b` payload carrying one signed url, as Flow returns it."""
    return [[f"https://{fb.MEDIA_HOST}/image/{media_id}?sig=x"]]


def operation_reply(
    operation_id: str,
    project_id: str,
    status: Optional[str] = None,
    complaint: Optional[str] = None,
) -> Any:
    """A `jwpduf` poll payload. `status="CAE"` is finished."""
    detail = None
    if complaint:
        detail = [None] * 8 + [[fb.OUTCOME_COMPLAINT, [None, complaint]]]
    return [None, 50, [[operation_id, project_id, "scene", status, None, detail]]]


def listing_window(operation_id: str, media_id: Optional[str]) -> Callable[[Optional[str]], dict]:
    """A `Zzl0ze` answer as the extension hands it back: the 800-byte window.

    `media_id=None` is "this operation is not in the listing yet", which is the
    ordinary state for a job that is still rendering.
    """
    text = (
        f'["{operation_id}",null,null,["t",1,2,null,null,"{media_id}"],"p'
        if media_id
        else ""
    )
    return lambda _match: {"data": text}


def media_reply(media_id: str, *, video: bool = True, image: bool = True) -> Any:
    """An `as29s` payload. The poster arrives before the clip, so a record with
    `video=False` is the "not fetchable yet" state rather than a failure."""
    urls = []
    if image:
        urls.append(f"https://{fb.MEDIA_HOST}/image/{media_id}?s=1")
    if video:
        urls.append(f"https://{fb.MEDIA_HOST}/video/{media_id}?s=1")
    return urls
