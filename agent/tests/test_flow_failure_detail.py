"""A Flow failure has to say what failed.

`ws_stats` reported `last_error: "API_404"` and nothing else, thirty-one
times in a row, while every piece of the answer was already in hand: the
agent chose the URL, and the extension forwards Google's response body
verbatim. Both were being dropped on the floor in `_resolve`.

Three digits with no endpoint attached is not a diagnosis. A 404 on
`batchAsyncGenerateVideo` (the dispatch was rejected) and a 404 on
`batchCheckAsyncVideoGenerationStatus` (the operation vanished mid-poll)
are different problems, and telling them apart is the whole job.
"""
from __future__ import annotations

from flowboard.services.flow_client import _endpoint_of, _error_detail


# ── the URL must lose its credentials, keep its meaning ───────────────


def test_the_api_key_never_survives_into_a_log_line():
    """Flow URLs carry `?key=…`. This particular key is public, but a
    logger that keeps query strings is one endpoint change away from
    writing a real one."""
    got = _endpoint_of(
        "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideo"
        "?key=AIzaSyREALLOOKINGSECRET&alt=json"
    )
    assert "key=" not in got
    assert "AIzaSy" not in got


def test_the_endpoint_is_still_identifiable():
    """Stripping too much would trade one useless message for another."""
    got = _endpoint_of(
        "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideo?key=x"
    )
    assert got == "v1/video:batchAsyncGenerateVideo"


def test_two_endpoints_that_fail_the_same_way_stay_distinguishable():
    dispatch = _endpoint_of("https://host/v1/video:batchAsyncGenerateVideo?key=x")
    poll = _endpoint_of(
        "https://host/v1/video:batchCheckAsyncVideoGenerationStatus?key=x"
    )
    assert dispatch != poll


def test_the_host_is_dropped():
    assert not _endpoint_of("https://aisandbox-pa.googleapis.com/v1/x").startswith(
        "aisandbox"
    )


def test_a_missing_url_is_not_a_crash():
    assert _endpoint_of("") == "?"


# ── the body must give the reason, not the payload ────────────────────


def test_googles_own_reason_is_kept():
    detail = _error_detail(
        {"error": {"status": "NOT_FOUND", "message": "Requested entity was not found."}}
    )
    assert "NOT_FOUND" in detail
    assert "Requested entity was not found." in detail


def test_a_plain_message_field_is_read():
    assert "quota" in _error_detail({"message": "quota exceeded"})


def test_the_request_payload_is_not_echoed_into_the_log():
    """The extension mirrors the response body back, and a Flow error
    response can carry the request that produced it — prompt text and media
    ids included. Only the error fields are read."""
    body = {
        "error": {"status": "INVALID_ARGUMENT", "message": "bad aspect"},
        "request": {"prompt": "SECRET PROMPT TEXT", "mediaId": "media-abc"},
    }
    detail = _error_detail(body)
    assert "SECRET PROMPT TEXT" not in detail
    assert "media-abc" not in detail
    assert "INVALID_ARGUMENT" in detail


def test_an_html_error_page_still_says_something():
    """Not every 404 is JSON — a proxy or a wrong host answers HTML."""
    assert _error_detail("<html><title>404</title></html>").startswith("<html>")


def test_an_empty_body_says_so_rather_than_lying():
    assert _error_detail("") == "(empty body)"
    assert _error_detail(None) == "(no detail in body)"


def test_the_detail_is_bounded():
    assert len(_error_detail({"message": "x" * 5000})) <= 200
