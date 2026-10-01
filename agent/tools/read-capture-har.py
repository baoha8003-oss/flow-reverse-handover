"""Read a DevTools HAR and print every Flow RPC it contains, decoded.

Why a HAR instead of copying `f.req` by hand (which is what
`docs/flow-capture.md` step 2 describes): an Edit Video action is several
requests — a chunked upload, then the generate submit, then the background
polls — and hand-copying one field out of each is where a capture goes wrong
silently. A HAR has all of them, in order, with their query strings intact.

WHAT THIS NEVER PRINTS. A HAR carries the whole session: cookies, the `at` CSRF
token, and the reCAPTCHA token inside `f.req`. Rule 2 of `docs/flow-capture.md`
says record presence and length, never content, and that rule is what makes it
safe to save a HAR at all. So:

  * request/response headers are reported as names + value LENGTHS only;
  * `at`, `cookie`, and anything matching a token shape is replaced by a marker
    before anything is written to stdout;
  * the captcha slot inside a decoded envelope is replaced the same way.

Delete the HAR when the capture is read. It is a signed session on disk.

Run from `agent/`:
    .venv/Scripts/python.exe -u tools/read-capture-har.py <path-to.har>
    .venv/Scripts/python.exe -u tools/read-capture-har.py <path.har> --rpc nprQif
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

#: Fields that authenticate rather than describe. Never printed.
_SECRET_NAMES = {"at", "cookie", "set-cookie", "authorization", "x-goog-authuser"}

#: A reCAPTCHA token, an OAuth token, or anything else long and opaque. Matched
#: on shape because the slot it sits in has no name inside an envelope.
_TOKENISH = re.compile(r"[A-Za-z0-9_\-]{60,}")


def _redact(value: Any) -> Any:
    """Replace anything token-shaped, at any depth, with its length."""
    if isinstance(value, str):
        if _TOKENISH.fullmatch(value):
            return f"<redacted {len(value)} chars>"
        return value
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    return value


def _form(text: str) -> dict[str, str]:
    """Parse an `application/x-www-form-urlencoded` body."""
    out: dict[str, str] = {}
    for part in (text or "").split("&"):
        if not part:
            continue
        key, _, raw = part.partition("=")
        out[urllib.parse.unquote_plus(key)] = urllib.parse.unquote_plus(raw)
    return out


def _decode_envelope(freq: str) -> tuple[str | None, Any]:
    """`[[[rpcid, "<inner as JSON string>", null, "generic"]]]` -> (rpcid, inner).

    Tolerant on purpose: a capture is worth reading even when one layer differs
    from what the codec expects, and "could not parse" plus the raw prefix is more
    useful than an exception.
    """
    try:
        outer = json.loads(freq)
        entry = outer[0][0]
        rpcid = entry[0]
        inner = json.loads(entry[1]) if isinstance(entry[1], str) else entry[1]
        return rpcid, inner
    except Exception as exc:
        return None, {"_unparsed": f"{type(exc).__name__}: {exc}",
                      "_prefix": freq[:200]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("har")
    ap.add_argument("--rpc", help="only this rpcid")
    ap.add_argument("--all", action="store_true",
                    help="include the background polls (Zzl0ze/jwpduf/as29s)")
    args = ap.parse_args()

    path = Path(args.har)
    if not path.exists():
        print(f"Khong thay file: {path}")
        return 2
    har = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    entries = har.get("log", {}).get("entries", [])
    print(f"HAR: {path.name} — {len(entries)} request\n")

    #: Already-known RPCs. Hidden by default so a new one stands out; that is the
    #: whole job of a capture.
    noise = {"Zzl0ze", "jwpduf", "as29s"}
    seen: list[tuple[str, str]] = []
    shown = 0

    for entry in entries:
        req = entry.get("request", {})
        url = req.get("url", "")
        if "batchexecute" not in url and "upload" not in url.lower():
            continue
        query = {q["name"]: q["value"] for q in req.get("queryString", [])}
        rpcid = query.get("rpcids", "")
        status = entry.get("response", {}).get("status")
        seen.append((rpcid or url.split("?")[0].rsplit("/", 1)[-1], str(status)))

        if args.rpc and rpcid != args.rpc:
            continue
        if not args.rpc and not args.all and rpcid in noise:
            continue

        print("=" * 72)
        print(f"{req.get('method')} {url.split('?')[0]}")
        print(f"  status      : {status}")
        for key in ("rpcids", "source-path", "bl", "f.sid", "hl", "rt", "_reqid"):
            if key in query:
                # `f.sid` is a session id: presence, not value.
                value = query[key]
                if key == "f.sid":
                    value = f"<present, {len(value)} chars>"
                print(f"  {key:11} : {value}")

        # Headers: names and lengths only. Enough to see that a capture carries a
        # header the agent does not send, without copying any of them out.
        interesting = [
            h for h in req.get("headers", [])
            if h["name"].lower().startswith("x-") or h["name"].lower() in _SECRET_NAMES
        ]
        if interesting:
            print("  headers     :")
            for h in interesting:
                name = h["name"]
                if name.lower() in _SECRET_NAMES:
                    print(f"    {name}: <present, {len(h.get('value',''))} chars>")
                else:
                    print(f"    {name}: {h.get('value','')[:120]}")

        body = (req.get("postData") or {}).get("text") or ""
        if not body:
            print("  body        : (none)")
            print()
            shown += 1
            continue
        form = _form(body)
        if "at" in form:
            print(f"  at          : <present, {len(form['at'])} chars>")
        if "f.req" in form:
            rid, inner = _decode_envelope(form["f.req"])
            print(f"  decoded rpcid: {rid}")
            print("  inner payload (token-shaped values redacted):")
            print(json.dumps(_redact(inner), indent=2, ensure_ascii=False)[:6000])
        else:
            # Not a batchexecute form — likely the upload leg.
            print(f"  body bytes  : {len(body)}")
            print(f"  body prefix : {body[:200]!r}")
        print()
        shown += 1

    print("=" * 72)
    print(f"Da in {shown} request. Tat ca RPC thay trong HAR:")
    for rpcid, status in seen:
        print(f"  {rpcid or '(no rpcids)'}  -> HTTP {status}")
    if not args.all:
        print("\n(Da an cac RPC nen: Zzl0ze / jwpduf / as29s. Them --all de xem.)")
    print("\nXoa file HAR sau khi doc xong: no la mot phien da dang nhap tren dia.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
