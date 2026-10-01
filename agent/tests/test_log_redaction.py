"""Credentials must never reach a log file.

httpx logs every request at INFO with the full URL, and Google's credits
endpoint takes the API key as a query parameter — so `?key=AIza...` was being
written to the agent log in clear text on every startup. These tests pin the
redaction so a future logging change cannot quietly undo it.
"""
from __future__ import annotations

import logging
import sys

import pytest

from flowboard.main import _RedactSecrets, redact


def _through_filter(msg: str, *args) -> str:
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )
    assert _RedactSecrets().filter(record) is True
    return record.getMessage()


@pytest.mark.parametrize(
    "raw",
    [
        'HTTP Request: GET https://x.test/v1/credits?key=AIzaSyBtrm0o5ab1cSECRET "200 OK"',
        "GET https://x.test/a?foo=1&key=AIzaSyBtrm0o5ab1cSECRET&bar=2",
        "GET https://x.test/a?API_KEY=AIzaSyBtrm0o5ab1cSECRET",
        "GET https://x.test/a?access_token=AIzaSyBtrm0o5ab1cSECRET",
        "authorization: Bearer ya29.AIzaSyBtrm0o5ab1cSECRET",
    ],
)
def test_the_secret_never_survives(raw):
    assert "SECRET" not in _through_filter(raw)
    assert "<redacted>" in _through_filter(raw)


def test_the_rest_of_the_line_survives():
    """A filter that eats the whole message would hide the request too."""
    out = _through_filter(
        'HTTP Request: GET https://x.test/v1/credits?key=AIzaSECRET "HTTP/1.1 200 OK"'
    )
    assert "https://x.test/v1/credits" in out
    assert '"HTTP/1.1 200 OK"' in out


def test_a_secret_arriving_through_args_is_redacted_too():
    """Log calls are usually `logger.info("%s", url)`, so the secret is in
    `args`, not `msg` — redacting only `msg` would miss every real case."""
    out = _through_filter("HTTP Request: %s", "https://x.test/a?key=AIzaSECRET")
    assert "SECRET" not in out


def test_a_clean_line_is_left_exactly_alone():
    raw = "worker started (max_concurrent=4, cooldown=7.0s)"
    assert _through_filter(raw) == raw


def test_a_secret_inside_a_traceback_is_redacted():
    """The half the first version missed. `exc_info` is formatted by the
    Formatter and never passes through the filter, so a `logger.exception`
    around an httpx call printed the URL — key included — in full."""
    try:
        raise ValueError(
            "GET https://x.test/v1/credits?key=AIzaSyBtrm0o5ab1cSECRETSECRET failed"
        )
    except ValueError:
        record = logging.LogRecord(
            name="flowboard",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="request failed",
            args=(),
            exc_info=sys.exc_info(),
        )
    assert _RedactSecrets().filter(record) is True
    assert record.exc_text is not None
    assert "SECRET" not in record.exc_text
    assert "<redacted>" in record.exc_text
    # Still a usable traceback.
    assert "ValueError" in record.exc_text


def test_an_already_formatted_traceback_is_cleaned_too():
    record = logging.LogRecord(
        name="flowboard", level=logging.ERROR, pathname=__file__, lineno=1,
        msg="x", args=(), exc_info=None,
    )
    record.exc_text = "Traceback: key=AIzaSyBtrm0o5ab1cSECRETSECRET"
    _RedactSecrets().filter(record)
    assert "SECRET" not in record.exc_text


@pytest.mark.parametrize(
    "raw",
    [
        'headers={"x-goog-api-key": "AIzaSyBtrm0o5ab1cSECRETSECRET"}',
        "x-goog-api-key: AIzaSyBtrm0o5ab1cSECRETSECRET",
        'X-Callback-Secret: SECRETSECRETSECRET',
        '{"api_key": "AIzaSyBtrm0o5ab1cSECRETSECRET"}',
        "using AIzaSyBtrm0o5ab1cSECRETSECRET now",
        "openai sk-SECRETSECRETSECRETSECRET",
    ],
)
def test_secrets_outside_a_query_string_are_redacted(raw):
    """The first version only matched `?key=` and `Bearer `. A header, a JSON
    body or a bare paste went through untouched."""
    assert "SECRET" not in redact(raw)


def test_a_bare_word_key_is_not_mangled():
    """`key=` only redacts as a query parameter. Prose that happens to contain
    the word must survive, or the logs become unreadable."""
    raw = "flow key present: True (monkey=business)"
    assert _through_filter(raw) == raw
