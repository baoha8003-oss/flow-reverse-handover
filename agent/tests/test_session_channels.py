"""The session-fetch allowlist, now that it lives in one place.

The bridge fetches with the user's own cookies, so what it may reach is a
security boundary rather than a convenience. Two properties matter: hosts off the
list are refused, and the list is path-prefixed rather than domain-wide —
`grok.com/` alone would also cover account settings.

What changed with the September 2026 transport migration: the agent used to hold
a second copy of this list, guarding a `proxy` request kind that forwarded a raw
url and body through the bridge. That kind is gone along with the transport it
used, so the list exists once, in `extension/background.js`. These tests read it
from there and apply the extension's own matching rule — `url.startsWith(prefix)`
— so a domain-wide entry or a missing trailing slash fails here rather than in
somebody's cookie jar.

Reading the extension as text is deliberate: this suite has no JS runtime, and
the property being pinned is about the data, not the code around it. The rule
itself is one line and identical in both languages.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

EXTENSION = Path(__file__).resolve().parents[2] / "extension" / "background.js"

ALLOWED = [
    "https://grok.com/rest/app-chat/conversations/new",
    "https://shopee.vn/api/v4/item/get?itemid=1&shopid=2",
]

REFUSED = [
    # Right domain, wrong path — the whole point of prefixing by path.
    "https://grok.com/settings",
    "https://grok.com/api/auth/session",
    "https://shopee.vn/user/account/profile",
    # Flow's own tRPC endpoints. They were a channel until the migration
    # un-authenticated them; leaving them on the list would keep a Google-shaped
    # hole open for nothing.
    "https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=x",
    "https://labs.google/fx/tools/flow",
    "https://flow.google.com/fx/api/trpc/project.create",
    # Other hosts entirely.
    "https://evil.example/rest/",
    "http://127.0.0.1:8101/api/settings",
    # Look-alikes a naive `in` check would let through.
    "https://grok.com.evil.example/rest/",
    "https://notshopee.vn/api/v4/item/get",
]


def _prefixes() -> list[str]:
    """The channel prefixes as the extension declares them."""
    source = EXTENSION.read_text(encoding="utf-8")
    block = re.search(
        r"const SESSION_CHANNELS = \[(.*?)\];", source, re.S
    )
    assert block, "SESSION_CHANNELS not found — did the bridge stop declaring it?"
    found = re.findall(r"prefix:\s*'([^']+)'", block.group(1))
    assert found, "the channel list parsed empty, which would pass every test below"
    return found


def _matches(url: str) -> bool:
    """The extension's rule, verbatim: `url.startsWith(c.prefix)`."""
    return url.startswith(tuple(_prefixes()))


@pytest.mark.parametrize("url", ALLOWED)
def test_allowed_urls_are_recognised(url):
    assert _matches(url)


@pytest.mark.parametrize("url", REFUSED)
def test_refused_urls_are_not_recognised(url):
    assert not _matches(url)


def test_every_channel_is_path_scoped_not_domain_wide():
    """A bare `https://host/` entry would hand the bridge the whole site."""
    for prefix in _prefixes():
        without_scheme = prefix.removeprefix("https://")
        assert "/" in without_scheme.rstrip("/"), (
            f"{prefix} covers a whole domain; scope it to a path"
        )
        assert prefix.endswith("/"), f"{prefix} must end with / or it matches siblings"


def test_the_channels_are_https_only():
    """A cookie-bearing fetch over http would put the session on the wire."""
    for prefix in _prefixes():
        assert prefix.startswith("https://"), prefix


def test_no_google_host_is_a_session_channel():
    """The two channels left are third-party sites, and they must never see a
    Google credential. Keeping a google.com prefix here would be the one entry
    where a leak matters most."""
    for prefix in _prefixes():
        assert "google" not in prefix, prefix


def test_the_agent_no_longer_holds_a_generic_url_forwarder():
    """`_handle_proxy` took a url and a body and sent them through the bridge.

    Its transport is gone, and nothing in the app or the frontend ever enqueued
    a `proxy` request — so this removes a primitive, not a feature. Pinned
    because reintroducing one would move the security boundary back to two
    places that can disagree, and the disagreement is the hole.
    """
    from flowboard.worker import processor

    assert not hasattr(processor, "_handle_proxy")
    assert "proxy" not in processor._DEFAULT_HANDLERS
    assert not hasattr(processor, "_ALLOWED_TRPC_PREFIXES")
    assert not hasattr(processor, "_ALLOWED_URL_PREFIXES")


def test_the_session_path_never_attaches_an_authorization_header():
    """It used to carry the captured Google token for Flow's own tRPC calls.
    Those left the bridge; the header must not have stayed behind."""
    source = EXTENSION.read_text(encoding="utf-8")
    handler = source[source.index("async function handleTrpcRequest") :]
    handler = handler[: handler.index("\nasync function ")]
    stripped = re.sub(r"//.*?$|/\*.*?\*/", "", handler, flags=re.S | re.M)
    assert "Authorization" not in stripped
    assert "Bearer" not in stripped
