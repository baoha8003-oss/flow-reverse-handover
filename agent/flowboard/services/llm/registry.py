"""Provider registry + ``run_llm`` dispatch.

The single entry point used by ``prompt_synth``, ``vision``, ``planner``.
Looks up the configured provider for a feature, runs the capability gates
(vision attachment vs. text-only provider), then delegates to the provider's
``run()``.

Three CLI-backed providers are registered: Claude, Gemini, OpenAI Codex.
xAI Grok was previously wired up but never shipped a usable end-user
CLI, so it was dropped from both UI and registry.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal, Optional

from .base import LLMError, LLMProvider
from .claude import ClaudeProvider
from .gemini import GeminiProvider
from .openai import OpenAIProvider
from . import secrets

logger = logging.getLogger(__name__)


#: Features the user pins in Settings. Each dispatch through ``run_llm``
#: goes to exactly the provider they chose, and fails loudly when that
#: provider cannot serve — see ``run_llm`` for why that is deliberate.
Feature = Literal["auto_prompt", "vision", "planner"]

#: Features the user never sees a switch for, because they are steps inside
#: a larger action rather than things to configure: rewriting a prompt from
#: a review, drafting a script, emitting canvas actions.
#:
#: They still need a provider, and there is no pin to honour — so these use
#: ``run_llm_chain``, which walks a preference order and takes the first
#: provider that can actually answer. That is not the silent-substitution
#: `run_llm` deliberately refuses: nobody chose a provider here, so there is
#: no choice to override.
#:
#: **Scoring a clip is NOT here**, though it reads like it belongs: it
#: dispatches through the pinned ``vision`` feature (`video_review`), and the
#: user's standing decision is that a pinned feature reports a dead provider
#: rather than quietly using another one. A `review` chain existed here with
#: no caller, and the health panel drew it as a live route.
InternalFeature = Literal["revise", "script_writer", "canvas_agent"]

#: Preference order per internal feature, most-preferred first.
#:
#: The ordering is by fit, not by brand. Claude leads where the answer must
#: be structured JSON or must read an image. OpenAI Codex leads for script
#: writing, where Vietnamese phrasing and dialogue carry the result. Gemini
#: sits last everywhere it appears because it is the only one of the three
#: that bills per call — the other two are OAuth subscriptions already paid
#: for, so trying them first costs nothing.
DEFAULT_CHAINS: dict[str, tuple[str, ...]] = {
    "revise": ("claude", "openai", "gemini"),
    "script_writer": ("openai", "claude", "gemini"),
    # Gemini last here too. It sat second, against the rule stated just
    # above — and second is where a chain actually lands, because the leader
    # being signed out is the ordinary case this mechanism was built for.
    "canvas_agent": ("claude", "openai", "gemini"),
}

#: Which pinned feature each internal one takes its lead from. The user's
#: choice still comes first in the chain even though they never pinned the
#: internal feature itself — rewriting a prompt is planner-shaped work, so
#: a user who pinned the planner to Codex gets Codex for rewrites too.
_INHERITS_PIN_FROM: dict[str, str] = {
    "revise": "planner",
    "canvas_agent": "planner",
    "script_writer": "auto_prompt",
}


# Module-level singletons. Each provider class has cheap probe state
# (e.g. cached `--version` result) so re-instantiating per call would
# defeat the cache. Same lifetime as the agent process.
_PROVIDERS: dict[str, LLMProvider] = {
    "claude": ClaudeProvider(),
    "gemini": GeminiProvider(),
    "openai": OpenAIProvider(),
}


def get_provider(name: str) -> Optional[LLMProvider]:
    """Lookup by name. None if the name is unknown."""
    return _PROVIDERS.get(name)


def list_providers() -> list[LLMProvider]:
    """All registered providers, in deterministic order."""
    return list(_PROVIDERS.values())


async def run_llm(
    feature: Feature,
    user_prompt: str,
    *,
    system_prompt: Optional[str] = None,
    attachments: Optional[list[str]] = None,
    timeout: float = 90.0,
) -> str:
    """Feature-routed LLM dispatch.

    Resolution chain:
      1. Look up the configured provider for ``feature`` in
         ``~/.flowboard/secrets.json``. No defaults — if the user hasn't
         picked one yet, raise loud so the UI's forced-setup gate
         intercepts before the call lands.
      2. Vision capability gate — if ``attachments`` is non-empty and the
         provider declares ``supports_vision = False``, raise immediately
         (no model call). Defense in depth alongside the per-provider
         attachment-rejection inside ``run()``.
      3. Availability gate — if the provider's CLI is missing or its API
         key isn't configured, raise immediately so the caller doesn't
         eat a longer subprocess / HTTP timeout.
      4. Dispatch.
    """
    config = secrets.read_active_providers()
    provider_name = config.get(feature)
    if provider_name is None:
        raise LLMError(
            f"No AI provider configured for {feature}; "
            f"open the AI Provider settings to set one up."
        )
    provider = _PROVIDERS.get(provider_name)
    if provider is None:
        raise LLMError(
            f"Unknown provider {provider_name!r} configured for {feature}; "
            f"reconfigure in Settings → AI Providers."
        )

    if attachments and not provider.supports_vision:
        raise LLMError(
            f"{provider_name} doesn't support vision; "
            f"reconfigure Vision provider in Settings → AI Providers."
        )

    if not await provider.is_available():
        raise LLMError(
            f"{provider_name} is not configured "
            f"(CLI missing or API key not set); "
            f"reconfigure in Settings → AI Providers."
        )

    logger.info(
        "llm: provider=%s feature=%s attachments=%d",
        provider_name, feature, len(attachments) if attachments else 0,
    )
    return await provider.run(
        user_prompt,
        system_prompt=system_prompt,
        attachments=attachments,
        timeout=timeout,
    )


def chain_for(feature: str, *, providers: Optional[list[str]] = None) -> list[str]:
    """Preference order for an internal feature, best first.

    The user's pin for the related visible feature leads, then the rest of
    the default order. Duplicates are dropped so a pinned provider is never
    tried twice.
    """
    if providers:
        return [p for p in providers if p in _PROVIDERS]

    # A feature name with no chain is a typo, and a generic default would
    # hide it — which is the exact shape of the bug this function was added
    # to fix. `revise_prompt` spent its whole life calling a feature named
    # "text" that never existed; because the dispatcher tolerated it, the
    # review loop silently stopped rewriting instead of failing. Unknown
    # names raise here so the next one dies at its first call, loudly.
    if feature not in DEFAULT_CHAINS:
        raise LLMError(
            f"No provider chain defined for {feature!r}; "
            f"known: {', '.join(sorted(DEFAULT_CHAINS))}"
        )
    order = list(DEFAULT_CHAINS[feature])
    inherited = _INHERITS_PIN_FROM.get(feature)
    if inherited:
        pinned = secrets.read_active_providers().get(inherited)
        if pinned in _PROVIDERS:
            order = [pinned] + [p for p in order if p != pinned]
    return [p for p in order if p in _PROVIDERS]


async def run_llm_chain(
    feature: str,
    user_prompt: str,
    *,
    system_prompt: Optional[str] = None,
    attachments: Optional[list[str]] = None,
    timeout: float = 90.0,
    providers: Optional[list[str]] = None,
) -> tuple[str, str]:
    """Dispatch an internal feature to the first provider that can answer.

    Returns ``(answer, provider_name)`` — the caller gets to record and show
    WHICH provider served, because a fallback nobody can see is the failure
    mode this whole mechanism is supposed to avoid.

    Skips, in order, providers that: are not registered, cannot take the
    attachments this call carries, or report themselves unavailable. Then
    tries to run, and moves on if the run itself fails.

    Raises ``LLMError`` naming every provider tried and why each was passed
    over. "No provider could answer" with no detail is the error that costs
    an hour; the list turns it into a fix.
    """
    names = chain_for(feature, providers=providers)
    if not names:
        raise LLMError(
            f"No provider is registered for {feature}; "
            f"open Settings → AI Providers."
        )

    reasons: list[str] = []
    for name in names:
        provider = _PROVIDERS[name]
        if attachments and not provider.supports_vision:
            reasons.append(f"{name}: cannot read attachments")
            continue
        try:
            if not await provider.is_available():
                reasons.append(f"{name}: not configured")
                continue
        except Exception as exc:
            logger.warning("llm: %s availability probe failed", name, exc_info=True)
            reasons.append(f"{name}: probe failed ({type(exc).__name__})")
            continue

        try:
            answer = await provider.run(
                user_prompt,
                system_prompt=system_prompt,
                attachments=attachments,
                timeout=timeout,
            )
        except (LLMError, asyncio.TimeoutError) as exc:
            reasons.append(f"{name}: {str(exc)[:120]}")
            continue
        except Exception as exc:
            # Anything else is still just this provider failing. Letting it
            # through aborted the whole chain and escaped the callers' guards
            # — `prompt_relay` catches `LLMError` to degrade gracefully, so a
            # `JSONDecodeError` from one CLI turned "slightly worse prompt"
            # into HTTP 500.
            logger.warning("llm: %s raised %s", name, type(exc).__name__,
                           exc_info=True)
            reasons.append(f"{name}: {type(exc).__name__}: {str(exc)[:100]}")
            continue

        if reasons:
            # Loud on purpose. A fallback that leaves no trace is how a user
            # ends up believing one model produced work another one did.
            logger.warning(
                "llm: feature=%s fell back to %s after %s",
                feature, name, "; ".join(reasons),
            )
        else:
            logger.info("llm: feature=%s provider=%s", feature, name)
        return answer, name

    raise LLMError(
        f"No provider could serve {feature} — tried " + "; ".join(reasons)
    )
