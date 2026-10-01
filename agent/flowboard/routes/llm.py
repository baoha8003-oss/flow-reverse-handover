"""HTTP routes for the multi-LLM provider Settings UI.

Endpoints:
  GET  /api/llm/providers           — list with state per provider
  PUT  /api/llm/providers/{name}    — set/clear API key
  POST /api/llm/providers/{name}/test — connection ping
  GET  /api/llm/config              — read active feature → provider mapping
  PUT  /api/llm/config              — update mapping

Frontend ↔ backend contract is documented in detail in
``.omc/plans/multi-llm-provider-legacy.md`` (UI Specification → Frontend
↔ backend contract section).

API keys are accepted only via PUT /providers/{name} and never echoed
back. The list endpoint reports `configured: true/false` instead.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from flowboard.services.llm import cli_auth, registry, secrets
from flowboard.services.llm.base import LLMError
from flowboard.services import claude_cli

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm", tags=["llm"])


# ── request/response models ───────────────────────────────────────────


class _ApiKeyBody(BaseModel):
    """PUT /api/llm/providers/{name}: `apiKey: null` clears the key."""
    apiKey: Optional[str] = None


class _ConfigBody(BaseModel):
    """PUT /api/llm/config: any subset of the three features."""
    auto_prompt: Optional[str] = None
    vision: Optional[str] = None
    planner: Optional[str] = None


# Whitelist for the writable feature → provider mapping. Hand-edited
# secrets.json with garbage values is tolerated by `read_active_providers`,
# but the HTTP surface must reject input that wouldn't route anywhere.
_VALID_PROVIDER_NAMES = {"claude", "gemini", "openai"}
# Providers with a REST mode that a pasted key unlocks. Claude is CLI-only in
# this build, so it stays out.
_API_KEY_PROVIDERS = {"openai", "gemini"}
_VALID_FEATURES = ("auto_prompt", "vision", "planner")


# ── GET /api/llm/providers ────────────────────────────────────────────


async def _auth_mode_of(provider) -> str:
    """Which identity a provider is signed in with.

    CLI providers answer for themselves. Providers without an `auth_mode`
    are key-based by construction, so their identity follows from whether
    a key is configured — there is no third state to discover.
    """
    probe = getattr(provider, "auth_mode", None)
    if callable(probe):
        try:
            return await probe()
        except Exception:
            logger.warning(
                "llm: auth probe failed for %s", provider.name, exc_info=True
            )
            return cli_auth.NONE
    return cli_auth.APIKEY if await provider.is_available() else cli_auth.NONE


@router.post("/debug/reset-probe")
async def debug_reset_probe() -> dict:
    """Force re-probe Claude CLI (debug endpoint)."""
    claude_cli.reset_availability_cache()
    available = await claude_cli.is_available(force=True)
    return {"ok": True, "claude_available": available}


@router.post("/recheck")
async def recheck_providers() -> list[dict]:
    """Drop every cached probe and report fresh provider state.

    Exists for the moment right after `codex login` / `claude auth login`:
    the caches hold for 60 seconds, and a user who has just signed in and
    still sees "API key" on the card concludes the sign-in failed. This
    makes the panel agree with reality immediately.
    """
    claude_cli.reset_availability_cache()
    for provider in registry.list_providers():
        reset = getattr(provider, "reset_cache", None)
        if callable(reset):
            reset()
    return await list_providers()


@router.get("/providers")
async def list_providers() -> list[dict]:
    """Snapshot per-provider state for the Settings panel.

    Each entry carries everything the UI needs to render the right row
    state without follow-up calls. `configured` reports whether the user
    has done setup (CLI: same as `available`; API: key present, regardless
    of test outcome). `mode` is meaningful only for OpenAI ("cli"/"api"/"none").
    """
    out: list[dict] = []
    for provider in registry.list_providers():
        auth = await _auth_mode_of(provider)
        # CLI providers: available implies configured. API providers:
        # `configured` means a key exists; `available` adds "key works"
        # via the cached probe. Splitting the two lets the UI distinguish
        # "user has set things up but the key is bad" from "user hasn't
        # set anything up yet".
        available = await provider.is_available()
        # Ask the provider which transport it would actually use. Hardcoding
        # "cli" for everything but OpenAI made the Settings card claim a CLI
        # login for Gemini even when it was dispatching through an API key.
        mode = getattr(provider, "mode", None) or "cli"
        if provider.name == "openai":
            configured = (
                bool(secrets.get_api_key("openai"))
                or getattr(provider, "_cli_available", False)
            )
            requires_key = False  # CLI path doesn't require it
        else:
            configured = available
            requires_key = False

        entry = {
            "name": provider.name,
            "supportsVision": provider.supports_vision,
            "available": available,
            "configured": configured,
            "requiresKey": requires_key,
            "mode": mode,
            "authMode": auth,
        }
        # The binary answers but nobody is signed in. Both UI branches for
        # this already existed (ProviderCard's "Not signed in", the setup
        # panel's login guidance) and were unreachable, because no route
        # ever emitted `lastError`.
        if available and mode == "cli" and auth == cli_auth.NONE:
            entry["lastError"] = "not_authenticated"
        out.append(entry)
    return out


# ── GET /api/llm/health ───────────────────────────────────────────────


@router.get("/health")
async def provider_health() -> dict:
    """Which model serves each step right now, and what is broken.

    This exists because dispatch deliberately does NOT substitute providers.
    ``run_llm`` fails loudly when the pinned provider cannot serve, so that
    the user always knows which model produced their work — and the cost of
    that choice is that they need somewhere to SEE a provider has died.
    Without this view the first symptom of a dead CLI is a failed board run.

    Two sections, because the two kinds of routing fail differently:

    * ``features`` — the three the user pinned. A dead provider here means
      that feature is down until they re-pin, and ``ok`` says so.
    * ``internal`` — the steps nobody pins (rewrite, review, script, canvas
      actions). These walk a chain, so what matters is which provider would
      answer today, and whether anything in the chain can.
    """
    from flowboard.services.llm import registry as _registry

    provider_state: dict[str, dict] = {}
    for provider in _registry.list_providers():
        try:
            available = await provider.is_available()
        except Exception:
            logger.warning("llm: availability probe failed for %s",
                           provider.name, exc_info=True)
            available = False
        auth = await _auth_mode_of(provider)
        provider_state[provider.name] = {
            "name": provider.name,
            "available": available,
            "authMode": auth,
            "mode": getattr(provider, "mode", None) or "cli",
            "capabilities": {
                "text": True,
                "vision": bool(provider.supports_vision),
                # Audio is the one capability with a single supplier, which
                # is exactly why it is worth naming: subtitles and karaoke
                # timing have no second option on this stack.
                "audio": bool(getattr(provider, "supports_audio", False)),
            },
        }

    def _usable(state: Optional[dict]) -> bool:
        """Installed AND signed in.

        `is_available()` probes the binary with `--version`, which answers
        for a CLI nobody has logged into — and this panel exists precisely
        so a dead login is visible before a board run finds it. Reporting
        `ok: true` there was the panel saying the opposite of its purpose.
        """
        if not state or not state["available"]:
            return False
        return state["authMode"] != cli_auth.NONE

    def _first_usable(names: list[str], *, needs_vision: bool = False) -> Optional[str]:
        for name in names:
            state = provider_state.get(name)
            if _usable(state) and (
                not needs_vision or state["capabilities"]["vision"]
            ):
                return name
        return None

    saved = secrets.read_active_providers()
    features = []
    for feature in _VALID_FEATURES:
        pinned = saved.get(feature)
        state = provider_state.get(pinned) if pinned else None
        features.append({
            "feature": feature,
            "provider": pinned,
            "ok": _usable(state),
            "reason": (
                "not pinned" if not pinned
                else "unknown provider" if state is None
                else "provider unavailable" if not state["available"]
                else "not signed in" if state["authMode"] == cli_auth.NONE
                else None
            ),
        })

    internal = []
    for feature in sorted(_registry.DEFAULT_CHAINS):
        chain = _registry.chain_for(feature)
        served_by = _first_usable(chain)
        internal.append({
            "feature": feature,
            "chain": chain,
            "servedBy": served_by,
            "ok": served_by is not None,
        })

    # Audio has no chain and no pin: `transcribe` reaches for Gemini
    # directly. Reporting it here keeps "why did subtitles stop working"
    # a one-glance question.
    audio_providers = [
        n for n, s in provider_state.items()
        if s["capabilities"]["audio"] and s["available"]
    ]
    # Image generation sits outside the registry entirely — it produces
    # pixels, not text, so it is not a `Feature` and has no pinned provider.
    # Reported here anyway because this is the page someone opens to ask
    # "what can the app do right now", and because both of its paths ship
    # off: a node set to the OpenAI engine while both switches are off fails
    # at run time, and this is where that becomes visible before the run.
    from flowboard.services import openai_images

    image_sources = openai_images.sources()
    return {
        "providers": list(provider_state.values()),
        "features": features,
        "internal": internal,
        "audio": {"providers": audio_providers, "ok": bool(audio_providers)},
        "images": {
            "sources": image_sources,
            "ok": bool(image_sources),
            "model": openai_images.model_name(),
            "quality": openai_images.quality(),
        },
    }


# ── PUT /api/llm/providers/{name} ─────────────────────────────────────


@router.put("/providers/{name}")
async def set_provider_key(name: str, body: _ApiKeyBody) -> dict:
    """Save (or clear, when `apiKey: null`) a provider's API key.

    OpenAI and Gemini both have an API mode alongside their CLI. Claude is
    CLI-only here, so a key for it is a 400 — the UI shouldn't reach this
    endpoint for it in the first place, but defend in depth.

    Gemini used to be refused too, which was a mistake worth naming: it left
    a user who could not finish the CLI's browser login with no way in at
    all, while working keys sat unused on disk.
    """
    if name not in _VALID_PROVIDER_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown provider {name!r}")
    if name not in _API_KEY_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail=f"{name} doesn't accept API keys; uses CLI auth instead",
        )
    secrets.set_api_key(name, body.apiKey)
    # Bust the relevant provider's availability cache so the next /providers
    # poll reflects the change immediately rather than waiting up to 60s.
    provider = registry.get_provider(name)
    if provider is not None and hasattr(provider, "reset_cache"):
        provider.reset_cache()
    logger.info("llm: api key %s for %s", "set" if body.apiKey else "cleared", name)
    return {"ok": True}


# ── POST /api/llm/providers/{name}/test ───────────────────────────────


@router.post("/providers/{name}/test")
async def test_provider(name: str) -> dict:
    """Ping the provider with a tiny prompt and report success / latency.

    Cost: ~1 token in + ~1 token out. Used by the Settings panel's "Test"
    button. Returns `{ok, latencyMs}` on success or `{ok: false, error}`
    on any failure mode.
    """
    if name not in _VALID_PROVIDER_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown provider {name!r}")
    provider = registry.get_provider(name)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"provider {name!r} not registered")
    if not await provider.is_available():
        return {"ok": False, "error": "provider not configured"}

    started = time.monotonic()
    try:
        # Single-character prompt to keep cost minimal. We do NOT pass
        # max_tokens because some providers (Claude CLI) ignore it; we
        # accept the small overage as a one-shot cost.
        # Timeout aligned with the slowest production feature ceiling
        # (auto_prompt_batch + vision both at 120s). The test endpoint
        # used to time out at 30s while Vision dispatches succeeded
        # because the Test path was tighter than what the user actually
        # runs. 120s keeps Test honest — if Vision passes here, it'll
        # pass at dispatch time too.
        # Gemini retries with exponential backoff on quota exhaustion (429),
        # so it uses 180s to account for multiple retries.
        test_timeout = getattr(provider, "test_timeout_secs", 120.0)
        await provider.run(".", timeout=test_timeout)
    except LLMError as exc:
        return {"ok": False, "error": str(exc)[:200]}
    except Exception as exc:
        # Wrapped so the Test endpoint never 500s — UI can render the
        # error inline regardless of which exception type leaked through.
        logger.exception("llm: test endpoint hit unexpected error for %s", name)
        return {"ok": False, "error": f"unexpected: {type(exc).__name__}"}
    latency_ms = int((time.monotonic() - started) * 1000)
    return {"ok": True, "latencyMs": latency_ms}


# ── GET /api/llm/config ───────────────────────────────────────────────


@router.get("/config")
def get_config() -> dict:
    """Return the feature → provider mapping plus the ``configured`` flag.

    Per-feature values are ``str | null`` — null means the user hasn't
    pinned a provider for that feature yet. ``configured`` is True only
    when all three features are pinned at the same provider (single-
    provider UI invariant); the frontend uses this to gate the forced
    AI Provider setup dialog on first run.
    """
    saved = secrets.read_active_providers()
    out: dict = {f: saved.get(f) for f in _VALID_FEATURES}
    out["configured"] = secrets.is_active_providers_configured()
    return out


# ── PUT /api/llm/config ───────────────────────────────────────────────


@router.put("/config")
def set_config(body: _ConfigBody) -> dict:
    """Update one or more feature → provider assignments.

    Validates names against the whitelist + feature keys against the
    enum. Provider availability is NOT checked here — picking an
    unconfigured provider is allowed (the dispatch path will fail loud
    when invoked, surfacing the gap to the user). Lets the user pre-pin
    a provider before completing setup.
    """
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")

    for feature, provider_name in updates.items():
        if feature not in _VALID_FEATURES:
            raise HTTPException(status_code=400, detail=f"unknown feature {feature!r}")
        if provider_name not in _VALID_PROVIDER_NAMES:
            raise HTTPException(
                status_code=400, detail=f"unknown provider {provider_name!r}"
            )
    for feature, provider_name in updates.items():
        secrets.set_feature_provider(feature, provider_name)
    logger.info("llm: config updated providers=%s", updates)
    return {"ok": True}
