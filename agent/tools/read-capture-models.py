"""Pull the model catalogue out of a captured `HTrJv` reply.

`HTrJv` answers with `[key, [display name]]` pairs. Captured 20/09: 52 keys,
including whole families this build had never seen — `extend`, `reshoot`,
`object_insertion`/`object_removal`, `camera_control`, a 1080p upsampler, and a
start+end `interpolation` key.

READ IT AS A CATALOGUE, NOT AS THE ACCEPT-SET. Measured the same day: NONE of the
keys this build dispatches with (`veo_3_1_i2v_lite`, `veo_3_1_t2v_lite`, …) appear
in it — and those keys demonstrably work, one of them having rendered a real 8s
clip on 19/09. So `HTrJv` is some other surface's list (it is fetched beside the
community-applet RPCs `tRARke`/`qJcgMc`, which pin their own model keys). Useful
for learning which CAPABILITIES exist; not evidence that a key is or is not
dispatchable.
"""
from __future__ import annotations

import base64
import json
import re
import sys
from collections import defaultdict

sys.path.insert(0, ".")

from flowboard.services import flow_batch as fb

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def body_text(entry: dict) -> str:
    content = (entry.get("response") or {}).get("content") or {}
    text = content.get("text", "") or ""
    if content.get("encoding") == "base64":
        try:
            text = base64.b64decode(text).decode("utf-8", "replace")
        except Exception:
            pass
    return text


def walk(node, out: dict[str, str]) -> None:
    """Collect every `[<model key>, [<label>, ...]]` pair, at any depth."""
    if isinstance(node, list):
        if (
            len(node) == 2
            and isinstance(node[0], str)
            and re.fullmatch(r"[a-z0-9_]{6,}", node[0])
            and isinstance(node[1], list)
            and node[1]
            and isinstance(node[1][0], str)
        ):
            out[node[0]] = node[1][0]
        for item in node:
            walk(item, out)


def main() -> int:
    har = json.load(open(sys.argv[1], encoding="utf-8", errors="replace"))
    found: dict[str, str] = {}
    for entry in har["log"]["entries"]:
        req = entry["request"]
        if "batchexecute" not in req.get("url", ""):
            continue
        query = {q["name"]: q["value"] for q in req.get("queryString", [])}
        rid = query.get("rpcids", "")
        try:
            walk(fb.first_payload(body_text(entry), rid), found)
        except Exception:
            continue

    families: dict[str, list[str]] = defaultdict(list)
    for key in sorted(found):
        if key.startswith("veo_"):
            parts = key.split("_")
            fam = parts[3] if len(parts) > 3 else "?"
        elif key.startswith(("abra", "omni", "narwhal", "harbor", "gem_pix")):
            fam = "image/omni"
        else:
            fam = "other"
        families[fam].append(key)

    print(f"TONG: {len(found)} model key\n")
    for fam in sorted(families):
        print(f"--- {fam}  ({len(families[fam])}) ---")
        for key in families[fam]:
            print(f"  {key:46} {found[key]}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
