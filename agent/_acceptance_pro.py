"""Acceptance run against a live Pro account. Temporary; delete after.

Spend authorisation for this run is **45 credits**: three OMNI Flash 4s clips
(15 each, the only published price in `flow_sdk.OMNI_FLASH_CREDIT_COST`). Every
phase that could cost more refuses to proceed instead of asking forgiveness.

**The balance is readable again** (P16.1 recovered it: `nzlxg`, a free read with
no captcha). This paragraph used to say the opposite, which was true for the
window between the transport migration and that recovery. So the before/after
comparison is back, and `GET /api/auth/credits` is where a phase reads it.

The count of Request rows stays as the second measurement, because it answers a
different question: the estimate says three, and three is what the run must
create. A balance delta says what was spent; the row count says whether anything
was dispatched that nobody asked for.

Phases, run one at a time, cheapest first:

    preflight   is the bridge up, and can the page sign. No dispatch, no cost.
    probe       the 0-credit group: a project-listing lookup with an unknown
                uuid, create-project (jHPbke), one image upload (maseQ). Nothing
                here is billed, and each answers a question the code currently
                has to assume.
    image       ONE image via ogiZ0b. Priced per the /estimate table.
    refusal     a `lite_relaxed` board with a character socket must be REFUSED.
                Costs nothing — the refusal happens before any Flow call. This is
                the money rule, checked live.
    dry         build the fan-out board and print the estimate. No dispatch.
    run <id>    run that board. Aborts unless the estimate is exactly
                3 billable jobs / 45 known credits.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8101"

#: The whole authorisation, in credits, for everything OMNI in this session.
#: A ceiling on the TOTAL, not per phase — the arithmetic below spends against
#: it rather than resetting.
OMNI_BUDGET_TOTAL = 45

#: Spent by the `one` phase: a single 4s clip, dispatched and verified
#: (720x1280, 4.01s, h264+aac). Recorded here so the fan-out cannot quietly
#: re-spend the whole ceiling — 15 + 45 is 60, and 60 was not authorised.
OMNI_ALREADY_SPENT = 15

#: What is left, and therefore how many 4s clips the fan-out may order.
OMNI_REMAINING = OMNI_BUDGET_TOTAL - OMNI_ALREADY_SPENT
EXPECT_JOBS = OMNI_REMAINING // 15
BUDGET_CREDITS = EXPECT_JOBS * 15


def call(method: str, path: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return {"_status": exc.code, "_body": exc.read().decode("utf-8", "replace")}
    return json.loads(raw) if raw else {}


def me() -> dict:
    return call("GET", "/api/auth/me")


def preflight() -> dict:
    """Can a dispatch happen at all. Three states, three different fixes.

    This used to stop on `credits is None`, which is now the permanent state —
    that check would refuse every run forever. What it asks instead is the
    condition that really blocks a dispatch: whether the page can sign one.
    """
    health = call("GET", "/api/health")
    ws = health.get("ws_stats") or {}
    scan = call("POST", "/api/auth/scan")
    who = me()
    print("extension connected :", scan.get("extension_connected"))
    print("extension build     :", scan.get("extension_version"))
    print("transport           :", ws.get("transport"))
    print("flow tab present    :", scan.get("flow_tab_present"))
    print("flow tab can sign   :", scan.get("flow_tab_signed"))
    print("probe error         :", scan.get("probe_error"))
    print("paygate tier (nhãn) :", who.get("paygate_tier"))
    print("credits             : không đọc được trên đường mới (xem trong Flow)")

    if not scan.get("extension_connected"):
        print("\nSTOP: extension chưa nối — mở chrome://extensions và bấm Reload.")
        sys.exit(2)
    build = scan.get("extension_version") or ""
    if build < "0.1.0":
        print(
            f"\nSTOP: extension đang là bản {build!r}, cần **0.1.0**. Bản cũ không "
            "có `batch_rpc` nên mọi lệnh Flow sẽ timeout, và timeout đọc giống "
            "hệt 'Flow đang chậm'."
        )
        sys.exit(2)
    if not scan.get("flow_tab_signed"):
        print(
            "\nSTOP: mở https://flow.google.com/ , đăng nhập, mở một project và để "
            "tab đó mở — mọi lệnh Flow chạy bên trong nó."
        )
        sys.exit(2)
    return who


def new_board(name: str) -> int:
    board_id = call("POST", "/api/boards", {"name": name})["id"]
    project = call("POST", f"/api/boards/{board_id}/project")
    print(f"board {board_id} · project {str(project)[:100]}")
    return board_id


def add_node(board_id: int, node_type: str, data: dict, x: int = 0, y: int = 0) -> dict:
    return call("POST", "/api/nodes", {
        "board_id": board_id, "type": node_type, "x": x, "y": y, "data": data,
    })


def wire(board_id: int, src: int, dst: int, port: str | None = None) -> dict:
    body = {"board_id": board_id, "source_id": src, "target_id": dst, "kind": "ref"}
    if port:
        body["target_port"] = port
    return call("POST", "/api/edges", body)


def estimate(board_id: int) -> dict:
    est = call("GET", f"/api/boards/{board_id}/estimate")
    if "_status" in est:
        raise SystemExit(f"estimate failed: {est}")
    print(
        f"estimate: billable={est['billableJobs']} notReady={est['notReadyJobs']} "
        f"credits={est['knownCredits']} unpriced={est['unpricedJobs']} "
        f"llm={est.get('llmJobs')} local={est['localJobs']}"
    )
    for item in est["items"]:
        print(f"   {item['shortId']:6} {item['type']:12} jobs={item['jobs']} "
              f"credits={item['credits']} {(item['note'] or '')[:70]}")
    return est


def nodes_of(board_id: int) -> list[dict]:
    return call("GET", f"/api/boards/{board_id}")["nodes"]


def requests_for(board_id: int) -> list[dict]:
    """Request rows for this board, read straight from the DB.

    The count is the measurement: the estimate says three, and three is what
    the run must create — no more.
    """
    from sqlmodel import select

    from flowboard.db import get_session
    from flowboard.db.models import Node, Request

    with get_session() as s:
        node_ids = [
            n.id for n in s.exec(select(Node).where(Node.board_id == board_id)).all()
        ]
        rows = [
            {"id": r.id, "type": r.type, "status": r.status, "node": r.node_id,
             "error": r.error}
            for r in s.exec(select(Request)).all()
            if r.node_id in node_ids
        ]
    return rows


def upload_reference(board_id: int) -> str:
    """Upload one packaged still as the OMNI reference. Uploading is free."""
    import mimetypes
    import uuid

    from flowboard.services import assets

    candidate = None
    for root in (assets.ASSET_ROOT, assets.DATA_DIR):
        if not root.is_dir():
            continue
        for pattern in ("*.png", "*.jpg", "*.jpeg"):
            found = sorted(root.rglob(pattern))
            # Small file, so the upload is quick and the picture is a person.
            found = [p for p in found if 20_000 < p.stat().st_size < 3_000_000]
            if found:
                candidate = found[0]
                break
        if candidate:
            break
    if candidate is None:
        raise SystemExit("không tìm thấy ảnh mẫu nào trong ASSET_ROOT")

    project = call("GET", f"/api/boards/{board_id}/project")
    project_id = project.get("flow_project_id") or project.get("projectId")
    if not project_id:
        raise SystemExit(f"board chưa có project Flow: {project}")

    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(candidate.name)[0] or "image/png"
    payload = b"".join([
        f"--{boundary}\r\nContent-Disposition: form-data; "
        f"name=\"project_id\"\r\n\r\n{project_id}\r\n".encode(),
        (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{candidate.name}\"\r\nContent-Type: {mime}\r\n\r\n"
        ).encode(),
        candidate.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    req = urllib.request.Request(
        BASE + "/api/upload", data=payload, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as resp:
            out = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"upload failed {exc.code}: {exc.read().decode()[:400]}") from None
    media_id = out.get("media_id") or out.get("mediaId")
    print(f"uploaded {candidate.name} ({candidate.stat().st_size} B) → {media_id}")
    if not media_id:
        raise SystemExit(f"upload trả về không có media_id: {out}")
    return media_id


# ── phase: the 0-credit probe group ───────────────────────────────────


def probe() -> None:
    """Answer, for free, four things the code currently has to assume.

    Every call here is unbilled. They are grouped because each one turns a
    documented guess into a measurement, and because running them first means a
    wrong assumption surfaces before anything costs money.
    """
    preflight()

    print("\n-- 1. flow_probe: does the page carry its signing token --")
    print("  ", call("POST", "/api/auth/flow-probe"))

    print("\n-- 2. Zzl0ze with an unknown project uuid --")
    # The question: does Flow answer an error, or an empty listing? It decides
    # whether `/api/flow/projects` can ever say "this project is gone" instead of
    # the `exists_on_flow: null` it reports today.
    unknown = "00000000-0000-4000-8000-000000000000"
    print("  ", call("POST", "/api/auth/batch-probe", {
        "rpcid": "Zzl0ze", "project_id": unknown, "match": "nothing-matches-this",
    }))

    print("\n-- 3. jHPbke: create a project for a fresh board --")
    # Ported from a commit upstream reverted for an unrelated reason, so it has
    # never run here. If it fails, the board falls back to the pinned
    # FLOW_PROJECT_ID and SAYS so — that fallback is what this checks.
    board_id = new_board("NGHIỆM THU · tạo project")
    project = call("GET", f"/api/boards/{board_id}/project")
    print("  ", project)
    if project.get("pinned_fallback"):
        print("  → Flow TỪ CHỐI tạo; đang dùng FLOW_PROJECT_ID dán tay.")

    print("\n-- 4. maseQ: upload one still (free, but mints a captcha) --")
    media_id = upload_reference(board_id)
    print("  media_id:", media_id)
    print("\nboard", board_id, "— dùng lại cho phase `image` nếu muốn.")


# ── phase: one image ──────────────────────────────────────────────────


def image() -> None:
    """ONE image through `ogiZ0b`. The cheapest thing here that is not free.

    Checks the part of the image path no fake can: that Flow returns a signed
    url inline, and that one variant means exactly one RPC.
    """
    preflight()
    board_id = new_board("NGHIỆM THU · 1 ảnh")
    node = add_node(board_id, "image", {
        "title": "Ảnh",
        "prompt": "một cô gái Việt mặc áo dài trắng đứng trước cổng tam quan, ánh sáng sớm",
        "sourceSettings": {"ratio": "IMAGE_ASPECT_RATIO_PORTRAIT"},
    })
    print("\n-- estimate --")
    est = estimate(board_id)
    if est["billableJobs"] != 1:
        raise SystemExit(f"STOP: estimate nói {est['billableJobs']} job, phải là 1.")

    plan = call("POST", f"/api/boards/{board_id}/plan")
    print("\n-- run --")
    print("  ", str(call("POST", f"/api/plans/{plan['id']}/run"))[:200])
    for n in nodes_of(board_id):
        if n["id"] == node["id"]:
            data = n["data"] or {}
            print(f"  status={n['status']} media={data.get('mediaId')} "
                  f"error={data.get('error')}")
    print("\n-- request rows (phải đúng 1) --")
    rows = requests_for(board_id)
    for row in rows:
        print("  ", row)
    print(f"\n{len(rows)} request row cho 1 job.")


# ── phase: the refusal, checked live ──────────────────────────────────


def refusal() -> None:
    """`lite_relaxed` + a character socket. Refused for free — but by WHOM changed.

    The rule never changed: serving Omni instead would bill 15-30 credits to a
    board that asked for a free lane. What changed twice is who says no.

    First it was us, because Pro had no relaxed lane and `resolve_video_model`
    swapped in the paid `lite` key with only a log line while the review loop
    still believed it was free. Then this docstring claimed Veo's whole r2v
    family had no captured payload "on any account" — which P13.12 disproved:
    `veo_3_1_r2v_lite_low_priority` IS understood, `VEO_R2V_LANES` maps this
    lane onto it, and what this account lacks is the ENTITLEMENT, not the
    payload.

    So the shape to expect now is a round trip: one Request row, one call to
    Flow, and Google refusing for free. Not "nothing dispatched" — the older
    reading of this phase — and the Request rows below are what shows which of
    the two happened.
    """
    preflight()
    board_id = new_board("NGHIỆM THU · từ chối lane relaxed")

    media_id = upload_reference(board_id)
    character = add_node(board_id, "character", {"title": "NV", "mediaId": media_id})
    video = add_node(
        board_id, "video",
        {
            "title": "Clip relaxed",
            "prompt": "người phụ nữ bước tới gần cửa sổ, ánh sáng sớm",
            "sourceSettings": {
                "quality": "lite_relaxed",
                "duration": 4,
                "ratio": "VIDEO_ASPECT_RATIO_PORTRAIT",
            },
        },
        x=400,
    )
    wire(board_id, character["id"], video["id"], "character_1")

    print("\n-- estimate trước khi chạy --")
    estimate(board_id)

    print("\n-- chạy (không có call nào tới Flow nếu từ chối đúng) --")
    plan = call("POST", f"/api/boards/{board_id}/plan")
    result = call("POST", f"/api/plans/{plan['id']}/run")
    print("run:", str(result)[:200])

    for node in nodes_of(board_id):
        if node["type"] == "video":
            print(f"node {node['short_id']}: status={node['status']} "
                  f"error={(node['data'] or {}).get('error')}")
    print("\n-- request rows --")
    for row in requests_for(board_id):
        print("  ", row)

    # No balance to compare any more. The Request rows above ARE the
    # measurement: a refusal that dispatched nothing leaves none for the
    # video node, and that is checkable without reading a number Flow no
    # longer exposes.
    print(f"\nboard {board_id} — kiểm số dư trong Flow UI nếu muốn chắc.")


# ── phase: one clip ───────────────────────────────────────────────────


ONE_CLIP_CREDITS = 15


def one() -> None:
    """ONE Omni 4s clip, down the same path the fan-out board will take.

    Deliberately the same lane and the same node shape as `dry`/`run`, so a
    fault in the reference dispatch or in the three-signal poll shows up at 15
    credits instead of 45. It is also the only step that proves the poll at all:
    every cheaper phase either refuses or answers inline.
    """
    preflight()
    board_id = new_board("NGHIỆM THU · 1 clip OMNI 4s")
    media_id = upload_reference(board_id)

    character = add_node(board_id, "character", {"title": "NV", "mediaId": media_id})
    video = add_node(
        board_id, "video",
        {
            "title": "Clip 1",
            "prompt": "người phụ nữ mở cửa sổ, nắng sớm tràn vào",
            "sourceSettings": {
                "quality": "omni",
                "duration": 4,
                "ratio": "VIDEO_ASPECT_RATIO_PORTRAIT",
            },
        },
        x=400,
    )
    edge = wire(board_id, character["id"], video["id"], "character_1")
    if "_status" in edge or not edge.get("id"):
        raise SystemExit(f"STOP: không tạo được dây character_1: {edge}")

    est = estimate(board_id)
    if est["billableJobs"] != 1 or est["knownCredits"] != ONE_CLIP_CREDITS:
        raise SystemExit(
            f"STOP: estimate {est['billableJobs']} job / {est['knownCredits']} credit, "
            f"phải là 1 / {ONE_CLIP_CREDITS}."
        )

    plan = call("POST", f"/api/boards/{board_id}/plan")
    print(f"\n-- run 1 clip ({ONE_CLIP_CREDITS} credit) --")
    started = time.time()
    print("  ", str(call("POST", f"/api/plans/{plan['id']}/run"))[:160])
    print(f"  (đã phóng sau {time.time() - started:.0f}s; executor chạy nền)")
    print(f"\nboard {board_id} — theo dõi bằng: python _acceptance_pro.py watch {board_id}")


def watch(board_id: int) -> None:
    """Poll the board's rows until they settle. Reads only; costs nothing."""
    import time as _t

    deadline = _t.time() + 900
    seen: dict[int, str] = {}
    while _t.time() < deadline:
        rows = requests_for(board_id)
        for row in rows:
            if seen.get(row["id"]) != row["status"]:
                seen[row["id"]] = row["status"]
                print(f"[{_t.strftime('%H:%M:%S')}] #{row['id']} {row['type']} "
                      f"→ {row['status']} {row['error'] or ''}")
        if rows and all(
            r["status"] in ("done", "failed", "timeout", "canceled") for r in rows
        ):
            break
        _t.sleep(8)

    print("\n-- node --")
    for node in nodes_of(board_id):
        data = node["data"] or {}
        if node["type"] == "video":
            print(f"  {node['short_id']} status={node['status']} "
                  f"media={data.get('mediaId')} error={data.get('error')}")
    print("\n-- request rows --")
    rows = requests_for(board_id)
    for row in rows:
        print("  ", row)
    print(f"\n{len(rows)} request row.")


# ── phase: the fan-out board, priced then run ─────────────────────────


#: One prompt per clip the remaining budget allows. The third line is kept in
#: the file rather than deleted: the fan-out is supposed to make N nodes from N
#: lines, so the list being longer than the budget is exactly the case where the
#: `run` guard has to refuse instead of trusting the estimate.
ALL_LINES = [
    "người phụ nữ mở cửa sổ, nắng sớm tràn vào",
    "người phụ nữ rót trà, khói bay lên",
    "người phụ nữ ngồi xuống bậc thềm, nhìn ra sân",
]
LINES = ALL_LINES[:EXPECT_JOBS]


def dry() -> int:
    preflight()
    board_id = new_board(f"NGHIỆM THU · fan-out {len(LINES)} dòng OMNI 4s")

    media_id = upload_reference(board_id)
    character = add_node(board_id, "character", {"title": "NV", "mediaId": media_id})
    prompt = add_node(
        board_id, "prompt", {"title": "3 dòng", "prompt": "\n".join(LINES)}, x=300
    )
    video = add_node(
        board_id, "video",
        {
            "title": "Clip 1",
            "sourceSettings": {
                "quality": "omni",
                "duration": 4,
                "ratio": "VIDEO_ASPECT_RATIO_PORTRAIT",
            },
        },
        x=600,
    )
    wire(board_id, character["id"], video["id"], "character_1")
    wire(board_id, prompt["id"], video["id"], "prompt")

    print("\n-- xem trước fan-out --")
    preview = call("GET", f"/api/boards/{board_id}/fan-out/{prompt['id']}")
    print("  ", preview)
    print("\n-- fan-out --")
    print("  ", call("POST", f"/api/boards/{board_id}/fan-out",
                     {"prompt_node_id": prompt["id"]}))

    print("\n-- estimate sau fan-out --")
    est = estimate(board_id)
    print(f"\nboard {board_id} — chạy: python _acceptance_pro.py run {board_id}")
    if est["billableJobs"] != EXPECT_JOBS or est["knownCredits"] != BUDGET_CREDITS:
        print(f"CHÚ Ý: estimate {est['billableJobs']} job / {est['knownCredits']} "
              f"credit KHÁC mức đã xin phép ({EXPECT_JOBS}/{BUDGET_CREDITS}) — "
              f"`run` sẽ từ chối.")
    return board_id


def run(board_id: int) -> None:
    preflight()
    est = estimate(board_id)
    if est["billableJobs"] != EXPECT_JOBS or est["knownCredits"] != BUDGET_CREDITS:
        raise SystemExit(
            f"STOP: estimate {est['billableJobs']} job / {est['knownCredits']} credit "
            f"vượt mức đã xin phép ({EXPECT_JOBS} job / {BUDGET_CREDITS} credit)."
        )
    if est["unpricedJobs"]:
        raise SystemExit(
            f"STOP: {est['unpricedJobs']} job không có giá công bố — không chạy."
        )

    plan = call("POST", f"/api/boards/{board_id}/plan")
    started = time.time()
    result = call("POST", f"/api/plans/{plan['id']}/run")
    print(f"run xong sau {time.time() - started:.0f}s:", str(result)[:200])

    print("\n-- node --")
    for node in nodes_of(board_id):
        data = node["data"] or {}
        if node["type"] == "video":
            print(f"  {node['short_id']} status={node['status']} "
                  f"media={data.get('mediaId')} error={data.get('error')}")
    print("\n-- request rows --")
    rows = requests_for(board_id)
    for row in rows:
        print("  ", row)

    # The count is the measurement. The estimate said N; N is what the run must
    # create, and one more than N is a clip charged for that nobody asked for.
    print(f"\nrequest rows: {len(rows)} (estimate nói {est['billableJobs']})")
    print(f"estimate nói {est['knownCredits']} credit — đọc số dư trong Flow UI để đối chiếu.")
    if len(rows) != est["billableJobs"]:
        print("\n⚠ SỐ REQUEST KHÁC ESTIMATE — ghi lại trước khi chạy thêm lần nào.")


def main() -> None:
    phase = sys.argv[1] if len(sys.argv) > 1 else "preflight"
    if phase == "preflight":
        preflight()
    elif phase == "probe":
        probe()
    elif phase == "image":
        image()
    elif phase == "one":
        one()
    elif phase == "watch":
        if len(sys.argv) < 3:
            raise SystemExit("watch cần board id")
        watch(int(sys.argv[2]))
    elif phase == "refusal":
        refusal()
    elif phase == "dry":
        dry()
    elif phase == "run":
        if len(sys.argv) < 3:
            raise SystemExit("run cần board id: python _acceptance_pro.py run 3")
        run(int(sys.argv[2]))
    else:
        raise SystemExit(f"phase lạ: {phase!r}")


if __name__ == "__main__":
    main()
