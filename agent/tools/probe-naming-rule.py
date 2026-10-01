"""Isolate what the batch path knows about a model NAME, at zero credit.

Two questions this answers, both recorded as open in
`plans/reports/review-260920-1215-p13-parallel-audit.md`:

1. Is the `_fl` (first-to-last) segment actually unknown to the RPC? The 19/09
   round concluded it was, but every name sent that day ALSO carried
   `_relaxed` — which was independently shown to be unknown. So the `_fl`
   verdict was confounded with the `_relaxed` one and never isolated.
2. Does `nprQif` know `omni_flash_i2v_<N>s_first_last`? That single name carries
   the entire Start-End surface of this build and has no measurement anywhere:
   no capture date on the builder, no entry in `docs/flow-capture.md`.

WHY THIS CANNOT SPEND A CREDIT — and the first answer to that was wrong.

The first design gave every request a bogus media id, reasoning that a generation
with no source frame cannot render. True, and useless: Flow answered `[5]`
NOT_FOUND for the missing media even when the model name was one this build
accepts, so a bad name and a bad media are the SAME answer and no verdict could
be attributed to the name. That run is kept in the report as a negative result.

What works is ENTITLEMENT. The 19/09 round measured that this Pro account holds no
free image-to-video lane, so every `_low_priority` i2v name here is outside the
plan and cannot render even with a real source frame. Cost stays zero while the
NAME becomes the only variable.

WHY IT RUNS THE REAL APP. The extension POSTs every RPC result to a hard-coded
`http://127.0.0.1:8101/api/ext/callback`, so a probe hosting only the WebSocket
side would never receive an answer. The read-only allowlist on
`POST /api/auth/batch-probe` (`Zzl0ze`/`jwpduf`/`as29s`) exists precisely to stop
a probe path from reaching a generation RPC, so widening it for a one-off
measurement is the wrong trade: it would leave a production endpoint able to
spend credits forever. Instead this starts the real app — same wiring, allowlist
untouched — and issues the requests through `flow_client` in-process.

Stop the running agent first; this binds the same ports.

READING THE ANSWER. Four requests; the test is only interpretable against the
three controls:

  A  bogus name      + real media  -> the shape for an unknown NAME
  B  name outside the plan + bogus media -> which check runs FIRST
  C  name outside the plan + real media  -> the shape for a known name
  1  C's name with `_fl` inserted  + real media

Measured 20/09: A `[5]`, B `[7] MODEL_ACCESS_DENIED`, C `[7]`, test `[5]`. So
`_fl` is genuinely unknown — this time isolated, because test 1 differs from C by
that segment alone. B also settled the ordering, which nobody had measured: name,
then plan, then media. `[5]` therefore means EITHER an unknown name OR a missing
media, which is why the user-facing label for it must not claim one of them.

A fifth and sixth request answer the reference-to-video question, which is about
the SLOT rather than the name — see `_r2v_envelope`. Measured 20/09: bogus name in
that slot `[5]`, `veo_3_1_r2v_lite_low_priority` in that slot `[7]`. So `MZZa6b`
DOES read a Veo name from `request[2]`, the lane is real, and the only thing
missing is threading the key through the builder.

Question 2 (`omni_flash_i2v_<N>s_first_last`) has no free oracle: the account does
hold Omni, so an entitled name with bogus media also answers `[5]`. It therefore
lives behind an opt-in flag — see `_measure_first_last`, which spends 15 credits
only in the branch where the feature turns out to work.

Run from `agent/` (free):
    .venv/Scripts/python.exe -u tools/probe-naming-rule.py

The one that can spend (15 credits, ask first):
    .venv/Scripts/python.exe -u tools/probe-naming-rule.py spend-first-last
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

sys.path.insert(0, ".")

import uvicorn

from flowboard.main import app
from flowboard.services import flow_batch as fb
from flowboard.services.flow_client import flow_client

#: A media id of the right shape that cannot exist.
#:
#: Kept because the first run of this probe used it as the safety mechanism and
#: the result killed that idea: Flow answered `[5]` NOT_FOUND for a bogus media
#: even when the model name was one this build accepts. A missing media and an
#: unknown name are the SAME answer, so bad media can make a probe free but can
#: never isolate a name.
BOGUS_MEDIA = "media-probe-does-not-exist-000000000000"

#: The oracle that does work, and why it is still free: the 19/09 round measured
#: that this Pro account has NO free image-to-video lane —
#: `veo_3_1_i2v_lite_low_priority` answered `PUBLIC_ERROR_MODEL_ACCESS_DENIED`.
#: So every `_low_priority` i2v name below is un-entitled and cannot render even
#: with a real source frame, which is what keeps the cost at zero while leaving
#: the NAME as the only thing under test.
NOT_ENTITLED_KNOWN = "veo_3_1_i2v_lite_low_priority"

#: `NOT_ENTITLED_KNOWN` with `_fl` inserted and nothing else changed. That is the
#: isolation the 19/09 round lacked: every name it used to condemn `_fl` also
#: carried `_relaxed`, which was independently shown to be unknown, so the two
#: verdicts were confounded.
FL_NAME = "veo_3_1_i2v_lite_fl_low_priority"

#: A name no catalogue can hold, to fix the shape of "unknown name".
BOGUS_NAME = "veo_3_1_i2v_zzqq_nonexistent"

#: The reference-to-video question, and why it is also free.
#:
#: `MZZa6b`'s model slot is `request[2]`, and we know the RPC reads it: this
#: build put `abra_r2v_8s` there on 19/09 and got three real clips. So the open
#: question is not the slot but whether a VEO name is accepted in it — the thing
#: `gen_video_omni`'s prefix guard currently assumes it is not, while three
#: comments in the same file assume it is.
#:
#: `veo_3_1_r2v_lite_low_priority` answered `[7] MODEL_ACCESS_DENIED` on 19/09,
#: i.e. this plan does NOT hold it, so it cannot render here either.
R2V_VEO_NAME = "veo_3_1_r2v_lite_low_priority"
R2V_BOGUS_NAME = "veo_3_1_r2v_zzqq_nonexistent"


def _real_image_media_id() -> str | None:
    """Newest uploaded/generated image media id on this machine.

    A real source frame is what makes the NAME the only variable: with a bogus
    one Flow answers NOT_FOUND and the name is never reached.
    """
    ids = _real_image_media_ids(1)
    return ids[0] if ids else None


def _real_image_media_ids(count: int) -> list[str]:
    """The newest `count` image media ids on this machine, newest first."""
    import sqlite3

    from flowboard.config import DB_PATH

    con = sqlite3.connect(str(DB_PATH))
    try:
        rows = con.execute(
            "select uuid_media_id from asset "
            "where kind = 'image' and uuid_media_id is not null "
            "and uuid_media_id != '' order by id desc limit ?",
            (count,),
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


def _r2v_envelope(project_id: str, media: str, model: str) -> str:
    """`MZZa6b` with the model slot overridden.

    Mirrors `fb.omni_reference_video_request` slot for slot, so the only
    difference from what the product sends is `request[2]`.

    The question this answers is NOT "does the RPC know the name" — 19/09 already
    got `[7]` for `veo_3_1_r2v_lite_low_priority`, so it does. It is whether
    `request[2]` is the slot the RPC READS a Veo name from. We know it reads Omni
    names there (this build put `abra_r2v_8s` in it on 19/09 and got three real
    clips), and `gen_video_omni`'s prefix guard assumes a Veo name is not accepted
    while three comments in the same file assume it is. One of them is wrong.

    Free for the same reason as the rest of this tool: the name is outside this
    plan, so Flow cannot render from it however well it understands it.
    """
    request = [
        [None, None, [[["probe"]]]],
        [[None, media]],
        model,
        fb.resolve_video_aspect(fb.VIDEO_ASPECT_LANDSCAPE),
        None,
        [None, None, None, None, fb._client_uuid(), fb._client_uuid()],
    ]
    return fb.build_envelope(fb.RPC_GEN_VIDEO_REFERENCES, [
        [request],
        fb._context(project_id),
        [fb._client_uuid(), 2],
    ])


def _first_last_envelope(project_id: str, model: str) -> str:
    """`nprQif` with the model slot overridden.

    Assembled from the module's own helpers rather than typed out, so the only
    difference from what the product sends is the one string under test.
    """
    request = [
        [None, None, [[["probe"]]]],
        model,
        fb.resolve_video_aspect(fb.VIDEO_ASPECT_LANDSCAPE),
        None,
        [None, BOGUS_MEDIA, None, None, None, fb.FULL_FRAME_CROP],
        [None, BOGUS_MEDIA, None, None, None, fb.FULL_FRAME_CROP],
        [None, None, None, None, fb._client_uuid(), fb._client_uuid()],
    ]
    return fb.build_envelope(fb.RPC_GEN_VIDEO_FIRST_LAST, [
        [request],
        fb._context(project_id),
        [fb._client_uuid(), 2],
    ])


async def _send(label: str, rpcid: str, freq: str) -> dict[str, Any]:
    """One request, unwrapped the way `FlowSDK._payload` unwraps it.

    `batch_rpc` is only the transport: it hands back `{error, data}` and it is
    `fb.first_payload` that raises `RpcError` carrying Flow's own code. Reading
    the dict as if it were the answer is how the first run of this probe
    reported four identical "accepted" rows for four different names.

    Reports the error shape; never echoes the envelope or the body.
    """
    out: dict[str, Any] = {"label": label, "rpcid": rpcid}
    try:
        result = await flow_client.batch_rpc(
            rpcid, freq, captcha_action="VIDEO_GENERATION", timeout=90,
        )
    except Exception as exc:
        out["transport_exception"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return out
    if result.get("error"):
        # The bridge could not deliver it — says nothing about the name.
        out["bridge_error"] = str(result["error"])[:160]
        return out
    body = result.get("data") or ""
    out["body_chars"] = len(body) if isinstance(body, str) else None
    try:
        fb.first_payload(body, rpcid)
    except Exception as exc:
        out["exception"] = type(exc).__name__
        detail = getattr(exc, "detail", None)
        if detail is not None:
            out["detail"] = detail
            if isinstance(detail, list) and detail:
                out["code"] = detail[0]
            out["reason"] = fb.read_rpc_reason(detail)
        out["text"] = str(exc)[:300]
        return out
    # Flow returned a usable payload. With a bogus media id nothing can render,
    # so this means the name passed validation and the refusal (if any) is
    # elsewhere in the response.
    out["accepted"] = True
    return out


def _verdict(a: dict, b: dict, t: dict) -> str:
    """What a test row means, given the two controls."""
    ka, ra = a.get("code"), a.get("reason")
    kb, rb = b.get("code"), b.get("reason")
    kt, rt = t.get("code"), t.get("reason")
    if (ka, ra) == (kb, rb):
        return ("KHONG KET LUAN DUOC - hai control tra cung mot cau tra loi, "
                "nen loi ten va loi media khong phan biet duoc")
    if (kt, rt) == (ka, ra):
        return "TEN LA - khop control A (RPC khong biet ten nay)"
    if (kt, rt) == (kb, rb):
        return "TEN DUOC NHAN - khop control B (loi o media, khong o ten)"
    return "CAU TRA LOI THU BA - khong khop control nao, doc bang tay o duoi"


async def _probe() -> int:
    for _ in range(40):
        if flow_client.connected:
            break
        await asyncio.sleep(0.5)
    else:
        print("Extension khong noi lai trong 20 giay. Mo popup extension roi thu lai.")
        return 2

    probe = await flow_client.flow_probe()
    if not probe.get("atTokenPresent"):
        print("Tab Flow chua ky duoc. Mo flow.google.com va dang nhap.")
        return 2
    project_id = (probe.get("sourcePath") or "").rsplit("/", 1)[-1]
    if len(project_id) != 36:
        print(f"Khong doc duoc project uuid tu sourcePath: {probe.get('sourcePath')!r}")
        return 2
    media = _real_image_media_id()
    if not media:
        print("Khong tim thay media anh that trong DB. Upload mot anh roi chay lai.")
        return 2
    print(f"project: ...{project_id[-12:]}")
    print(f"media that: ...{media[-8:]} (anh da upload san)")
    print("moi ten duoi day deu KHONG thuoc goi nay -> khong the render -> 0 credit\n")

    plan = [
        ("A control: ten bogus", fb.RPC_GEN_VIDEO,
         lambda: fb.video_request("probe", project_id, media, model=BOGUS_NAME)),
        ("B control: media gia, ten da biet", fb.RPC_GEN_VIDEO,
         lambda: fb.video_request("probe", project_id, BOGUS_MEDIA,
                                  model=NOT_ENTITLED_KNOWN)),
        ("C control: ten da biet, ngoai goi", fb.RPC_GEN_VIDEO,
         lambda: fb.video_request("probe", project_id, media,
                                  model=NOT_ENTITLED_KNOWN)),
        ("1 test: _fl chen vao dung ten do", fb.RPC_GEN_VIDEO,
         lambda: fb.video_request("probe", project_id, media, model=FL_NAME)),
        # The r2v question is about the SLOT, not the name. Same two-control
        # shape, on `MZZa6b` and in the slot `omni_reference_video_request` writes.
        ("R-A control: r2v ten bogus", fb.RPC_GEN_VIDEO_REFERENCES,
         lambda: _r2v_envelope(project_id, media, R2V_BOGUS_NAME)),
        ("R-1 test: r2v ten Veo vao o model", fb.RPC_GEN_VIDEO_REFERENCES,
         lambda: _r2v_envelope(project_id, media, R2V_VEO_NAME)),
    ]
    rows: list[dict[str, Any]] = []
    for label, rpcid, build in plan:
        row = await _send(label, rpcid, build())
        rows.append(row)
        tail = "" if row.get("code") is not None else str(row.get("text", ""))[:110]
        print(f"{label:38} -> code={row.get('code')!r} reason={row.get('reason')!r} "
              f"{'ACCEPTED ' if row.get('accepted') else ''}{tail}")
        await asyncio.sleep(2)

    bogus_name, bad_media, known_name, fl, r2v_bogus, r2v_veo = rows
    print("\nKet luan:")
    print(f"  _fl      : {_verdict(bogus_name, known_name, fl)}")
    # For r2v the "known name" reference is the [7] shape from the i2v controls:
    # both are PUBLIC_ERROR_MODEL_ACCESS_DENIED when the name is understood and
    # the plan lacks it, whichever RPC asked.
    print(f"  r2v slot : {_verdict(r2v_bogus, known_name, r2v_veo)}")
    if r2v_veo.get("reason") == "PUBLIC_ERROR_MODEL_ACCESS_DENIED":
        print("   -> O MODEL DUNG: MZZa6b doc ten Veo tu request[2]. Lan r2v la that;")
        print("      chi can luon key qua builder roi noi guard (dung thu tu).")
    elif r2v_veo.get("code") == 5:
        print("   -> O MODEL SAI: MZZa6b khong doc ten Veo o day. Lan r2v xoa duoc.")
    if (bad_media.get("code"), bad_media.get("reason")) == (
            bogus_name.get("code"), bogus_name.get("reason")):
        print("  (xac nhan lai: media gia va ten la tra CUNG mot cau -> khong the")
        print("   dung media gia de do ten; day la ly do vong probe dau that bai)")
    print()
    print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
    return 0


async def _measure_first_last() -> int:
    """The one measurement that cannot be free. Costs 15 credits when it works.

    `omni_flash_i2v_<N>s_first_last` carries the entire Start-End surface of this
    build and has no measurement anywhere: no capture date on the builder, no
    entry in `docs/flow-capture.md`. It cannot be probed by entitlement like every
    other name here, because this account DOES hold Omni — so the name passes the
    plan check and falls through to the media check, where a bogus id answers `[5]`
    exactly as an unknown name would.

    The bet is self-limiting, which is why it is worth taking: an unknown name
    answers `[5]` and costs NOTHING, and the only branch that spends is the one
    where the feature turns out to work. So the 15 credits buy a working Start-End
    tab, or they are not spent at all.

    4 seconds, the cheapest length (`OMNI_FLASH_CREDIT_COST[4] == 15`). Built with
    the product's own `fb.omni_first_last_request`, not a hand-rolled envelope —
    measuring anything else would leave the real path untested.
    """
    probe = await flow_client.flow_probe()
    if not probe.get("atTokenPresent"):
        print("Tab Flow chua ky duoc.")
        return 2
    project_id = (probe.get("sourcePath") or "").rsplit("/", 1)[-1]
    media = _real_image_media_ids(2)
    if len(media) < 2:
        print("Can 2 anh that trong DB cho khung dau va khung cuoi.")
        return 2
    start, end = media[0], media[1]
    print("== DO first-last: TON 15 CREDIT neu key nay chay duoc ==")
    print(f"project ...{project_id[-12:]}  start ...{start[-8:]}  end ...{end[-8:]}")
    print("4 giay, key omni_flash_i2v_4s_first_last, dung builder that cua san pham\n")

    row = await _send(
        "first-last 4s (co the ton tien)",
        fb.RPC_GEN_VIDEO_FIRST_LAST,
        fb.omni_first_last_request("a slow pan between the two frames",
                                  project_id, start, end, duration_s=4),
    )
    print(f"code={row.get('code')!r} reason={row.get('reason')!r} "
          f"{'ACCEPTED' if row.get('accepted') else ''}")
    if row.get("code") == 5:
        print("\n-> TEN CHET: nprQif khong biet omni_flash_i2v_4s_first_last.")
        print("   Toan bo mat Start-End la [5] vinh vien. KHONG TON CREDIT.")
    elif row.get("accepted"):
        print("\n-> TEN SONG: Flow nhan va da tao operation. DA TON ~15 credit.")
        print("   Mat Start-End chay duoc. Key trong builder la dung.")
    else:
        print("\n-> Cau tra loi khac: doc JSON duoi roi ket luan bang tay.")
    print()
    print(json.dumps(row, ensure_ascii=False, indent=2, default=str))
    return 0


async def main() -> int:
    spend = "spend-first-last" in sys.argv[1:]
    config = uvicorn.Config(app, host="127.0.0.1", port=8101, log_level="warning")
    server = uvicorn.Server(config)
    serving = asyncio.create_task(server.serve())
    try:
        # uvicorn exposes `started` as a flag, not an event, so polling it is
        # the available option. noqa ASYNC110 for that reason.
        while not server.started and not serving.done():  # noqa: ASYNC110
            await asyncio.sleep(0.1)
        if serving.done():
            print("Khong bind duoc 8101 - agent cu con chay? Dung no truoc.")
            return 2
        for _ in range(40):
            if flow_client.connected:
                break
            await asyncio.sleep(0.5)
        else:
            print("Extension khong noi lai trong 20 giay.")
            return 2
        # The spending measurement is opt-in on the command line, never a side
        # effect of the free run.
        return await (_measure_first_last() if spend else _probe())
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=20)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
