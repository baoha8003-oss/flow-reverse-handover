"""Mutation run for the five RPCs recovered from other people's captures.

Every payload here was read out of someone else's code, not off our own wire, so
these mutations exist to answer one question: is each decision actually pinned,
or does it only look pinned? Three groups, and the money ones come first.

* **What must never be billed by accident.** An extension defaults to the free
  lane; 4K upscale is refused outright because two independent captures disagree
  about its tier slot and 4K costs 50 credits; reading the balance must not burn
  a single-use captcha.
* **What must never be silently dropped.** A submit whose operation id cannot be
  read has to come back as an error — on a paid lane that clip is already being
  rendered, and "ok, zero operations" strands something the user paid for.
* **What the captures actually said.** The scene builder prefixes `projects/` and
  the character builder does not; the extension frame window is derived from the
  clip length rather than hard-coded at the 169/192 the capture happened to show.

Run from `agent/`: .venv/Scripts/python.exe tools/mutations/p16-recovered-rpcs.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Labels quote Vietnamese error copy, and a Windows console defaults to cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BATCH = "flowboard/services/flow_batch.py"
SDK = "flowboard/services/flow_sdk.py"

T_BATCH = ["tests/test_flow_batch_new_rpcs.py"]
T_SDK = ["tests/test_new_capabilities_sdk.py"]

# (label, file, old, new, tests)
MUTATIONS: list[tuple[str, str, str, str, list[str]]] = [
    # ── money: nothing gets billed that was not asked for ──────────────
    (
        "an extension defaults to the PAID lane",
        BATCH,
        "    model = EXTEND_MODEL_PAID if paid_lane else EXTEND_MODEL_FREE",
        "    model = EXTEND_MODEL_PAID",
        T_BATCH,
    ),
    (
        "4K upscale is dispatched on a guessed tier slot (50 credits)",
        BATCH,
        '    if resolution != "1080p":',
        "    if False:",
        T_BATCH,
    ),
    (
        "the tier leaves the one value both captures agree on",
        BATCH,
        "[None, media_id, None, None, _client_uuid()], None, 2,",
        "[None, media_id, None, None, _client_uuid()], None, 3,",
        T_BATCH,
    ),
    (
        "reading the balance burns a single-use captcha",
        SDK,
        "        result = await self._client.batch_rpc(fb.RPC_CREDITS, freq, None)",
        "        result = await self._client.batch_rpc("
        "fb.RPC_CREDITS, freq, fb.CAPTCHA_VIDEO)",
        T_SDK,
    ),
    (
        "an Omni source is dispatched instead of refused",
        SDK,
        "        if is_omni_model_key(source_model_key):",
        "        if False:",
        T_SDK,
    ),
    (
        "the refusal stops naming which clip, so the card cannot explain",
        SDK,
        'f"clip này là “{source_model_key}” (Omni) nên nút nối bị mờ"',
        '"clip này là Omni nên nút nối bị mờ"',
        T_SDK,
    ),
    # ── nothing accepted is silently dropped ───────────────────────────
    (
        "a submit with an unreadable id reports success and strands the clip",
        SDK,
        "        if not operation_id:\n"
        '            return _dispatch_error([], "extend_no_operation_returned")',
        "        if False:\n"
        '            return _dispatch_error([], "extend_no_operation_returned")',
        T_SDK,
    ),
    (
        "a character create with no id in the reply is accepted",
        BATCH,
        '        raise FlowBatchError("C4BZMd returned no identifiers")',
        '        return {"project_id": "", "character_id": ""}',
        T_BATCH,
    ),
    (
        "a scene reply with no id is accepted, so extend fails three calls later",
        BATCH,
        '        raise FlowBatchError("rqZuUc returned no scene id")',
        '        scene_id = ""',
        T_BATCH,
    ),
    (
        "an unreadable balance reads as zero instead of unknown",
        BATCH,
        "    if isinstance(payload, list) and payload "
        "and isinstance(payload[0], (int, float)):\n        return int(payload[0])",
        "    if isinstance(payload, list) and len(payload) > 1 "
        "and isinstance(payload[1], (int, float)):\n        return int(payload[1])",
        T_BATCH,
    ),
    (
        "the extend reader stops tolerating the second record slot",
        BATCH,
        "    for slot in (3, 2):",
        "    for slot in (2,):",
        T_BATCH + T_SDK,
    ),
    # ── the captures said what they said ──────────────────────────────
    (
        "the frame window is hard-coded to the 8s pair the capture showed",
        BATCH,
        "    end = max(1, round(float(duration_s) * EXTEND_FPS))\n"
        "    return max(0, end - EXTEND_CONTEXT_FRAMES), end",
        "    return 169, 192",
        T_BATCH,
    ),
    (
        "the character create gains the projects/ prefix the scene one needs",
        BATCH,
        "        [project_id, None, None, [1, name, []]],",
        '        [f"projects/{project_id}", None, None, [1, name, []]],',
        T_BATCH,
    ),
    (
        "the scene loses the projects/ prefix the character one must not have",
        BATCH,
        '        f"projects/{project_id}", list(media_ids), None, None,',
        "        project_id, list(media_ids), None, None,",
        T_BATCH,
    ),
    (
        "slot 2 of the upscale goes back to a constant instead of the aspect",
        BATCH,
        "        [None, operation_id], None, resolve_video_aspect(aspect), None,",
        "        [None, operation_id], None, 1, None,",
        T_BATCH,
    ),
    (
        "the upscale swaps the operation id and the media id",
        BATCH,
        "        [None, operation_id], None, resolve_video_aspect(aspect), None,\n"
        "        [None, media_id, None, None, _client_uuid()], None, 2,",
        "        [None, media_id], None, resolve_video_aspect(aspect), None,\n"
        "        [None, operation_id, None, None, _client_uuid()], None, 2,",
        T_BATCH,
    ),
    (
        "the scene id stops reaching the extend request object",
        BATCH,
        "        [scene_id, None, None, None, _client_uuid(), _client_uuid()],",
        "        [None, None, None, None, _client_uuid(), _client_uuid()],",
        T_BATCH,
    ),
    (
        "the chain position stops reaching the trailer",
        BATCH,
        "        [_client_uuid(), 2, None, [scene_id, int(position)]],",
        "        [_client_uuid(), 2, None, [scene_id, 1]],",
        T_BATCH,
    ),
    (
        "extend stops requiring a scene",
        BATCH,
        '    if not scene_id:\n        raise FlowBatchError("extend_video needs a scene id")',
        '    if False:\n        raise FlowBatchError("extend_video needs a scene id")',
        T_BATCH,
    ),
    (
        "a scene is built with no clips in it",
        BATCH,
        "    if not media_ids:\n"
        '        raise FlowBatchError("create_scene needs at least one media id")',
        "    if False:\n"
        '        raise FlowBatchError("create_scene needs at least one media id")',
        T_BATCH,
    ),
    (
        "a blank character name reaches Flow",
        SDK,
        '        if not clean:\n            raise ValueError("character name is required")',
        '        if False:\n            raise ValueError("character name is required")',
        T_SDK,
    ),
    (
        "an extension stops minting the captcha a generate needs",
        SDK,
        "                fb.RPC_EXTEND_VIDEO, freq, fb.CAPTCHA_VIDEO, timeout=120",
        "                fb.RPC_EXTEND_VIDEO, freq, None, timeout=120",
        T_SDK,
    ),
]


def run(tests: list[str]) -> bool:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:randomly", *tests],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0


def main() -> int:
    only = sys.argv[1:] or None
    survived: list[str] = []
    for index, (label, path, old, new, tests) in enumerate(MUTATIONS, 1):
        if only and str(index) not in only:
            continue
        file = Path(path)
        raw = file.read_bytes()
        # The repo carries both line endings; rewriting a whole file's endings
        # would turn a one-line mutation into a whole-file diff.
        newline = b"\r\n" if raw.count(b"\r\n") else b"\n"
        target = old.encode("utf-8").replace(b"\n", newline)
        replacement = new.encode("utf-8").replace(b"\n", newline)
        if raw.count(target) != 1:
            print(f"{index:2d}. ANCHOR MISSED ({raw.count(target)}x): {label}")
            survived.append(f"{index}. {label} (anchor)")
            continue
        file.write_bytes(raw.replace(target, replacement))
        try:
            passed = run(tests)
        finally:
            file.write_bytes(raw)
        if passed:
            print(f"{index:2d}. SURVIVED: {label}")
            survived.append(f"{index}. {label}")
        else:
            print(f"{index:2d}. caught:   {label}")

    total = len(only) if only else len(MUTATIONS)
    print(f"\n{total - len(survived)}/{total} caught")
    for s in survived:
        print("  survived:", s)
    return 1 if survived else 0


if __name__ == "__main__":
    raise SystemExit(main())
