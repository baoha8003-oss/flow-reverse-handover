"""What the extension may reach, and what it must no longer contain.

The bridge runs with the user's own browser session, so its surface is a
security boundary rather than a convenience. Since Google's September 2026
migration it is also a *smaller* boundary: the Bearer token, the aisandbox
proxy, the userinfo fetch and the 302-capture listener all served a transport
that no longer authenticates, and code reachable only through a dead path is
code nobody will notice going wrong.

These read the extension as TEXT. That is deliberate: there is no JS runtime in
this suite, and the property worth pinning is "this string is not in the
bundle", which text answers exactly. Precedent: test_token_auto_refresh.py.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

EXT = Path(__file__).resolve().parents[2] / "extension"


#: `//` to end of line, and `/* … */` blocks.
_COMMENT = re.compile(r"//.*?$|/\*.*?\*/", re.S | re.M)


@pytest.fixture(scope="module")
def background() -> str:
    return (EXT / "background.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def background_code(background: str) -> str:
    """`background.js` with comments stripped.

    The dead-path assertions below run against this, not the raw text: the
    comments explaining WHY something was removed necessarily name the thing
    that was removed, and a test that forbids the word forbids the explanation.
    What matters is that no code reaches it.
    """
    return _COMMENT.sub("", background)


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))


# ── the dead transport is gone ────────────────────────────────────────


@pytest.mark.parametrize("dead", [
    # The REST host Flow stopped authenticating.
    "aisandbox-pa",
    # The token it needed. Nothing in this worker holds one any more.
    "ya29",
    # Identity came from Google's userinfo endpoint with that token.
    "oauth2/v2/userinfo",
    # Flow's tRPC surface went unauthenticated in the migration.
    "getMediaUrlRedirect",
    "fx/api/trpc",
    # Chunked video upload rode the same dead path (see the gaps register).
    "upload-video",
])
def test_no_code_path_reaches_the_dead_transport(background_code, dead):
    assert dead not in background_code, (
        f"{dead!r} is back in background.js code — it is only reachable "
        "through a transport Google switched off"
    )


def test_the_bearer_capture_listener_is_gone(background_code):
    """It read `Authorization` off every outbound Google request. The rewritten
    frontend sends no such header, so this listener saw nothing for a week
    while looking perfectly healthy."""
    assert "requestHeaders?.length" not in background_code
    assert "noteAuthScheme" not in background_code
    assert "authSeen" not in background_code


def test_the_captcha_solver_survived(background):
    """The one piece of the old path the new one still needs: every generate
    RPC carries a single-use reCAPTCHA minted in the page."""
    assert "async function solveCaptcha(" in background
    assert "'solve_captcha'" in background


def test_grok_keeps_its_own_header_sniffer(background, manifest):
    """`webRequest` is still a permission, and this is why: Grok answers only
    to its own per-session headers, read off the network layer."""
    assert "urls: ['https://grok.com/rest/*']" in background
    assert "webRequest" in manifest["permissions"]


# ── the new transport is wired ────────────────────────────────────────


def test_the_batch_runner_is_present_and_reachable(background):
    assert "async function runBatchRpc(cmd) {" in background
    assert "msg.method === 'batch_rpc'" in background
    assert "/_/AiSandboxAngularFrontend/data/batchexecute" in background


def test_the_main_world_function_reads_the_page_signed_state(background):
    """`at` / `f.sid` / `bl` live on the page and nowhere else."""
    for key in ("WIZ_global_data", "SNlM0e", "FdrFJe", "cfb2h", "x-same-domain"):
        assert key in background, key


def test_main_world_is_only_used_by_the_runner_and_the_probe(background):
    """A MAIN-world injection runs with the page's full authority. Two callers
    is the whole budget: the RPC runner and the read-only probe."""
    assert background.count("world: 'MAIN'") == 2


def test_the_captcha_placeholder_is_substituted_not_built_here(background):
    """The mint has to happen in the page moments before the request leaves —
    a token minted earlier and replayed comes back UNUSUAL_ACTIVITY."""
    assert "const CAPTCHA_SLOT = '__CAPTCHA__';" in background
    assert "freq.split(CAPTCHA_SLOT).join(solved.token)" in background


def test_the_listing_response_is_cut_down_inside_the_tab(background):
    """The project listing is past 17 MB and all we want is one entry; moving
    the whole thing across the bridge on every poll is the alternative."""
    assert "MAX_RPC_TEXT = 32000000" in background
    assert "text.slice(found, found + 800)" in background


def test_the_probe_reports_presence_and_never_a_value(background):
    """It answers "can this tab sign an RPC?" — which used to be answerable by
    trying the token the agent held."""
    assert "async function runFlowProbe() {" in background
    assert "atTokenPresent: !!wiz.SNlM0e" in background
    # The email is reported as a boolean, never as an address.
    assert "emailPresent" in background


# ── permissions shrank with the surface ───────────────────────────────


def test_the_dead_hosts_are_not_requested(manifest):
    for gone in (
        "https://aisandbox-pa.googleapis.com/*",
        "https://*.googleapis.com/*",
        "https://storage.googleapis.com/*",
    ):
        assert gone not in manifest["host_permissions"], f"{gone} is back — nothing calls it"


def test_the_hosts_that_remain_are_the_ones_still_used(manifest):
    assert set(manifest["host_permissions"]) == {
        "https://flow.google.com/*",
        # A tab pinned before the move; it redirects, and solveCaptcha can
        # still find it.
        "https://labs.google/*",
        "https://grok.com/*",
        "https://shopee.vn/*",
        "http://127.0.0.1:8101/*",
        "http://localhost:8101/*",
    }


def test_declarative_net_request_went_with_its_rules(manifest):
    """`rules.json` rewrote Referer/Origin on aisandbox calls. No such calls
    remain, so both the rules and the permission are gone."""
    assert "declarativeNetRequest" not in manifest["permissions"]
    assert "declarative_net_request" not in manifest
    assert not (EXT / "rules.json").exists()


def test_the_version_says_a_reload_is_needed(manifest):
    """An agent talking to a pre-batch extension gets nothing but errors, so the
    version has to be able to tell them apart, and the popup has to render it —
    "which build is loaded" is the question a forgotten reload makes unanswerable.

    The exact number is deliberately NOT pinned. It was, at "0.1.0", and every
    legitimate bump then had to edit this line; a test that must be edited for
    every correct change teaches people to edit it without reading it. What is
    worth holding is the floor: anything below the batch-transport baseline is a
    downgrade the agent cannot talk to."""
    parts = tuple(int(x) for x in manifest["version"].split("."))
    assert parts >= (0, 1, 0), f"extension downgraded below the batch baseline: {parts}"
    assert "chrome.runtime.getManifest().version" in (
        EXT / "popup.js"
    ).read_text(encoding="utf-8")


# ── the content-script bridge ─────────────────────────────────────────


def test_the_captcha_mint_is_serialised():
    """Two overlapping mints can hand back the same token, and a replayed
    captcha token comes back PUBLIC_ERROR_UNUSUAL_ACTIVITY."""
    injected = (EXT / "injected.js").read_text(encoding="utf-8")
    assert "captchaMintTail" in injected
    assert "async function mintCaptcha(" in injected


def test_the_content_reply_outlives_the_grecaptcha_wait():
    """grecaptcha loads lazily; a content timeout shorter than the wait reports
    failure for a token that was about to arrive."""
    injected = (EXT / "injected.js").read_text(encoding="utf-8")
    content = (EXT / "content.js").read_text(encoding="utf-8")
    assert "timeout = 22000" in injected
    assert "}, 30000);" in content


def test_the_popup_no_longer_offers_to_refresh_a_token():
    """There is nothing to refresh. What a stalled run needs is whether the
    Flow tab can sign at all."""
    js = (EXT / "popup.js").read_text(encoding="utf-8")
    html = (EXT / "popup.html").read_text(encoding="utf-8")
    assert "REFRESH_TOKEN" not in js
    assert "formatTokenAge" not in js
    assert "CHECK_FLOW_TAB" in js
    assert "flow-tab-row" in html
