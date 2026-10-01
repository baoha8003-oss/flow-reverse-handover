"""What the agent knows about the Flow session.

It used to know quite a lot: the extension captured a Bearer token, resolved the
signed-in profile through Google's ``/oauth2/v2/userinfo``, and the agent asked
``/v1/credits`` for the plan and the balance. Google's September 2026 migration
removed the token, so all three are gone.

What is left is deliberately thinner, and each field says where it comes from:

* the **account tier** is a Settings value (``FLOW_PAYGATE_TIER``) — a label for
  prices and lane lists, never a dispatch gate;
* whether generation can run at all is a question only the page can answer, so
  ``/flow-probe`` asks it and reports presence, never values;
* the balance is not readable on this path at all, and ``/estimate`` says so
  rather than showing a stale number.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from flowboard.services import flow_batch as fb
from flowboard.services.flow_client import flow_client

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.get("/me")
def get_me() -> dict:
    """The account tier, and whether the browser can sign a Flow call.

    ``email`` / ``name`` / ``picture`` are always null now, and the keys survive
    only so the frontend need not branch on their absence: the identity came
    from Google's userinfo endpoint, called with a token that is no longer
    minted. ``flow_tab`` reports whether the signed-in page carries something
    that looks like an address, which is as much as this build can honestly say.

    ``paygate_tier`` is whatever the user picked in Settings — a label (which
    lanes to offer, which price to quote), never a gate on dispatch. Defaulting
    it was the old footgun: the worker stamped `PAYGATE_TIER_ONE` into
    `request.params`, and this route read it back and reported Pro forever, even
    for an Ultra account.
    """
    return {
        # A stable shape, not a promise. See the docstring.
        "email": None,
        "name": None,
        "picture": None,
        "verified_email": None,
        "paygate_tier": flow_client.paygate_tier,
        # Not readable on the migrated transport: there is no credits RPC. Null
        # rather than 0 — "unknown" and "none left" call for opposite actions.
        "sku": None,
        "credits": None,
        # What the last probe found, or None if none has run. Ask
        # POST /api/auth/flow-probe for a fresh answer.
        "flow_tab": flow_client.flow_tab,
    }


@router.post("/logout")
async def logout() -> dict:
    """Disconnect the extension's identity from the agent.

    Clears the agent-side cached profile + tier so /api/auth/me
    returns null fields immediately. Sends a `logout` message to the
    extension over WS so it drops its own in-memory cachedUserInfo +
    flowKey — the next time the user wants to reconnect they pick up
    fresh credentials, not stale ones.

    The extension's WS connection itself stays open. We don't tear it
    down because the user might log back in with a different account
    and we want to be ready to push the new identity.
    """
    extension_notified = await flow_client.notify({"type": "logout"})
    flow_client.clear_extension()
    return {
        "ok": True,
        "extension_notified": extension_notified,
    }


@router.get("/credits")
async def credits() -> dict:
    """The account's live Flow credit balance (nzlxg). Read-only, 0 credit.

    Closes the long-standing gap where the batch path could only report the
    configured tier, never the real number. None means unreadable, not zero.
    """
    if not flow_client.connected:
        return {"ok": False, "error": "extension_disconnected", "credits": None}
    from flowboard.services.flow_sdk import get_flow_sdk
    try:
        result = await get_flow_sdk().get_credits()
    except Exception as exc:  # report, never crash the panel
        return {"ok": False, "error": str(exc)[:200], "credits": None}
    return {"ok": result.get("credits") is not None, **result}


@router.post("/flow-probe")
async def flow_probe() -> dict:
    """Can the browser sign a Flow call right now?

    Replaces ``POST /refresh-token``. There is no token to refresh: the
    credential lives in the page and never reaches the agent, so the old
    "open a background tab until a Bearer appears" routine has nothing to wait
    for. This asks the page directly instead.

    Costs nothing and generates nothing. Reports presence, never values —
    whether ``at`` is on the page, not what it is.
    """
    if not flow_client.connected:
        return {
            "ok": False,
            "error": "extension_disconnected",
            "note": "Extension chưa nối — mở chrome://extensions và bấm Reload.",
        }
    probe = await flow_client.flow_probe()
    ok = bool(probe.get("atTokenPresent"))
    note = None
    if not probe.get("flowTabPresent"):
        note = (
            "Chưa có tab Flow nào. Mở https://flow.google.com/ , đăng nhập, và "
            "để tab đó mở — mọi lệnh gửi Flow đều chạy bên trong nó."
        )
    elif not ok:
        note = (
            "Có tab Flow nhưng trang chưa ký được: hoặc chưa đăng nhập, hoặc app "
            "đang tải. Đăng nhập rồi đợi app load xong, sau đó bấm lại."
        )
    return {"ok": ok, "probe": probe, "note": note}


@router.post("/captcha-test")
async def captcha_test(action: str = "VIDEO_GENERATION") -> dict:
    """Ask the page for one captcha token and report whether it came back.

    Exists because the reasons generation stalls look alike from here: Flow
    being slow, the page not being signed in (ask ``/flow-probe``), and the page
    having stopped issuing captcha tokens. They call for different fixes, and
    nothing could distinguish them before — the solver ran inside every request
    and never reported separately.

    Costs nothing: a captcha token is issued by the page, not bought from
    Google, so this is safe to press while a run is paused.
    """
    if not flow_client.connected:
        return {"ok": False, "error": "extension_disconnected"}
    result = await flow_client.solve_captcha(action)
    if result.get("error") and "ok" not in result:
        # An older extension build has no `solve_captcha` method, so it
        # answers nothing and this times out. Say which, rather than reporting
        # a captcha failure that never happened.
        return {
            "ok": False,
            "error": result["error"],
            "note": (
                "Nếu lỗi là timeout: extension đang chạy bản cũ chưa có "
                "solve_captcha — mở chrome://extensions và bấm Reload."
            ),
        }
    return result


@router.post("/scan")
async def scan_extension() -> dict:
    """One round-trip that answers "why can't I generate?".

    Three states the frontend renders differently. There used to be four — the
    userinfo nudge and the credits re-fetch went with the token that made them
    possible:

      - ``extension_connected=False``: the bridge is not running. Show the
        install / reload instructions.
      - connected, ``flow_tab_signed=False``: the bridge is up but the page
        cannot sign. This is the state that looked healthy for a week, and it is
        fixed in the browser rather than in the app.
      - connected and signed: nothing to do.
    """
    probe = await flow_client.flow_probe() if flow_client.connected else {}
    return {
        "extension_connected": flow_client.connected,
        "extension_version": flow_client.ws_stats.get("extension_version"),
        "flow_tab_present": bool(probe.get("flowTabPresent")),
        "flow_tab_signed": bool(probe.get("atTokenPresent")),
        # Set once the user has chosen one; a label, never a gate.
        "has_paygate_tier": flow_client.paygate_tier is not None,
        "probe_error": probe.get("error"),
    }


# ── the free read probe ───────────────────────────────────────────────


#: RPCs this route may send, and what each one needs. A closed set, because the
#: route exists to answer questions about a live account and anything wider is a
#: "send arbitrary commands to Google as me" endpoint on an unauthenticated
#: localhost port.
#:
#: All three are **reads**: they poll, list or resolve. Nothing here generates,
#: so nothing here can be billed — which is the property that makes the probe
#: safe to run before the paid steps rather than after.
_PROBE_RPCS: dict[str, tuple[str, ...]] = {
    fb.RPC_PROJECT_MEDIA: ("project_id",),
    fb.RPC_OPERATION: ("operation_id",),
    fb.RPC_MEDIA: ("media_id",),
}


class BatchProbe(BaseModel):
    rpcid: str
    project_id: Optional[str] = None
    operation_id: Optional[str] = None
    media_id: Optional[str] = None
    #: Required for the project listing. That payload is past 17 MB, so a probe
    #: without one would pull the whole thing through the bridge to answer a
    #: yes/no question.
    match: Optional[str] = None


@router.post("/batch-probe")
async def batch_probe(body: BatchProbe) -> dict:
    """Send one free, read-only RPC and report the SHAPE of the answer.

    Written for the acceptance run: several behaviours this build has to assume
    can be measured in one unbilled call each — whether Flow errors or answers
    empty for an unknown project, whether an operation id that has decayed still
    resolves, whether a media record carries a video url yet.

    Two rules make it safe to keep:

    * the envelope is **built here** from `flow_batch`, never accepted from the
      caller. A route that forwarded a raw ``f.req`` would let anything that can
      reach localhost issue arbitrary Flow commands as the signed-in user.
    * the response is **described, not echoed**. A project listing window
      contains media titles and prompts; dumping it would print the user's own
      prompts into a console and an activity log. So this reports whether a match
      was found and how long the window was — never its contents.
    """
    if body.rpcid not in _PROBE_RPCS:
        return {
            "ok": False,
            "error": f"rpcid_not_probeable: {body.rpcid}",
            "note": (
                "Chỉ cho phép RPC đọc, không tính tiền: "
                + ", ".join(sorted(_PROBE_RPCS))
            ),
        }
    for field in _PROBE_RPCS[body.rpcid]:
        if not getattr(body, field, None):
            return {"ok": False, "error": f"missing_{field}"}
    if body.rpcid == fb.RPC_PROJECT_MEDIA and not body.match:
        return {
            "ok": False,
            "error": "missing_match",
            "note": "Listing 17 MB — probe phải kèm `match`.",
        }
    if not flow_client.connected:
        return {"ok": False, "error": "extension_disconnected"}

    if body.rpcid == fb.RPC_PROJECT_MEDIA:
        freq = fb.project_media_request(str(body.project_id))
    elif body.rpcid == fb.RPC_OPERATION:
        freq = fb.operation_request(str(body.operation_id))
    else:
        freq = fb.media_request(str(body.media_id))

    result = await flow_client.batch_rpc(
        body.rpcid, freq, match=body.match, timeout=120
    )
    raw = result.get("data") or ""
    out: dict = {
        "ok": not result.get("error"),
        "rpcid": body.rpcid,
        "transport_error": result.get("error"),
        # Length only. The window can hold titles and prompts.
        "response_chars": len(raw) if isinstance(raw, str) else None,
        "looks_like_envelope": isinstance(raw, str) and raw.lstrip().startswith(")]}"),
    }
    if body.rpcid == fb.RPC_PROJECT_MEDIA and isinstance(raw, str):
        found = fb.find_media_id_in_text(raw, str(body.match))
        # The answer the acceptance run is after: does an unknown project come
        # back as an error, or as a successful empty window? They send
        # `/api/flow/projects` to opposite conclusions about a missing project.
        out["match_found"] = bool(found)
        out["empty_window"] = not raw
    if body.rpcid in (fb.RPC_OPERATION, fb.RPC_MEDIA) and isinstance(raw, str) and raw:
        try:
            payload = fb.first_payload(raw, body.rpcid)
        except (fb.FlowBatchError, fb.RpcError, ValueError) as exc:
            out["read_error"] = str(exc)[:200]
        else:
            if body.rpcid == fb.RPC_OPERATION:
                try:
                    operation = fb.read_operation(payload)
                except fb.FlowBatchError as exc:
                    out["read_error"] = str(exc)[:200]
                else:
                    out["status"] = operation.status
                    out["done"] = operation.done
                    out["complaint"] = operation.error
            else:
                urls = fb.read_media_urls(payload, str(body.media_id))
                # Presence, not the urls: they are signed and short-lived, and a
                # signed url in a log is a download anybody with the log can do.
                out["has_video_url"] = urls.video is not None
                out["has_image_url"] = urls.image is not None
    return out
