"""Multi-LLM provider layer.

Public surface (consumed by ``prompt_synth``, ``vision``, ``planner``):

- ``run_llm(feature, prompt, ...)`` — dispatch to the provider the user
  pinned for a visible feature, failing loudly rather than substituting
- ``run_llm_chain(feature, prompt, ...)`` — dispatch an *internal* step
  (rewrite, review, script, canvas actions) to the first provider in a
  preference order that can answer; returns ``(answer, provider_name)``
- ``LLMProvider`` — Protocol every provider implements
- ``LLMError`` — single error type the registry + providers raise

The HTTP routes layer (``routes/llm.py``) additionally imports ``secrets``
and the per-provider classes from ``registry`` for status / test endpoints.
"""
from __future__ import annotations

from .base import LLMError, LLMProvider
from .registry import chain_for, get_provider, list_providers, run_llm, run_llm_chain

__all__ = [
    "LLMError", "LLMProvider", "get_provider", "list_providers",
    "run_llm", "run_llm_chain", "chain_for",
]
