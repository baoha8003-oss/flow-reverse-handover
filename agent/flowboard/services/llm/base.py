"""Protocol + shared types for the multi-LLM provider layer.

Every provider implementation (Claude / Gemini / OpenAI Codex) conforms to
``LLMProvider``. The registry (``registry.py``) is the only thing that
knows the concrete classes; everything else routes through ``run_llm``.

Caller signature is identical across providers — ``attachments`` is a list
of absolute file paths, and each provider converts internally based on its
transport (CLI flag vs. base64 data URL). See the plan at
``.omc/plans/multi-llm-provider-legacy.md`` for the full hybrid-attachment
rationale.
"""
from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


class LLMError(RuntimeError):
    """Base error type for the multi-LLM layer.

    Provider implementations raise subclasses (or this directly) so the
    HTTP layer can surface a single error shape regardless of which
    provider failed. Never carries the API key or any token.
    """


def safe_error_message(resp: Any) -> str:
    """The human-readable part of an OpenAI-shaped error body, and nothing else.

    Deliberately narrow. An error response is one of the easiest places for
    a credential to end up in a log, because an auth failure is exactly when
    a server likes to echo what it was sent. So this reads only the two
    fields OpenAI documents as prose and truncates them, rather than
    formatting the body — anything unrecognised becomes a fixed string.

    Lives here rather than in ``openai.py`` because the image path needs the
    same treatment and importing a private function across modules to get it
    is how a safety property quietly becomes optional.

    ``resp`` is an ``httpx.Response``; typed loosely so ``base`` does not
    have to import httpx just for an annotation.
    """
    try:
        body = resp.json()
    except ValueError:
        return "(non-JSON body)"
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            msg = err.get("message")
            if isinstance(msg, str):
                return msg[:200]
        msg = body.get("message")
        if isinstance(msg, str):
            return msg[:200]
    return "(unrecognised body)"


@runtime_checkable
class LLMProvider(Protocol):
    """Every provider implementation conforms to this surface."""

    name: str
    supports_vision: bool

    #: Whether this provider accepts an audio attachment. Distinct from
    #: `supports_vision` because they genuinely diverge: on this stack only
    #: Gemini takes audio, so subtitles and karaoke timing depend on one
    #: provider in a way image work does not. Optional on the Protocol so a
    #: provider that predates the attribute still conforms; readers use
    #: ``getattr(provider, "supports_audio", False)``.
    supports_audio: bool

    async def run(
        self,
        user_prompt: str,
        *,
        system_prompt: Optional[str] = None,
        attachments: Optional[list[str]] = None,
        timeout: float = 90.0,
    ) -> str:
        """Return the model's plain-text response.

        ``attachments`` are absolute file paths. Vision-capable providers
        translate them to whatever transport their backend uses. Text-only
        providers MUST raise ``LLMError`` if attachments are non-empty —
        the registry guards against this too, but defense in depth.
        """
        ...

    async def is_available(self) -> bool:
        """Cheap, cached check: is this provider usable on this host?

        For CLI providers: probe the binary with ``--version``.
        For API providers: check that an API key is configured.

        Must NOT actually call the model — that's what the test endpoint is for.
        """
        ...
