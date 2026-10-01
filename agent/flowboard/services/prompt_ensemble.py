"""Three models draft the prompt; one of them picks the winner.

`run_llm` sends one feature to one configured provider. This runs the same
brief past every provider that is actually available, in parallel, then has
a judge choose or merge. The drafts and the judge's reasoning are kept, not
just the winner — a prompt you cannot see the alternatives to is a prompt
you cannot argue with.

**Why this is close to free.** Claude and Codex are signed in through OAuth
on this machine, so their marginal cost is a subscription already paid for.
Gemini bills per call, which is why it is counted in the cost table and why
a single-provider run stays the default.

Availability is asked, never assumed: `cli_auth` already knows which CLIs
are signed in, and a provider that cannot answer is skipped rather than
failing the ensemble. One surviving draft is a valid ensemble of one.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

#: Tried in this order, and the order is the judge preference too: the
#: first available provider judges.
PROVIDERS: tuple[str, ...] = ("claude", "openai", "gemini")

_JUDGE_SYSTEM = """\
You are choosing between drafts of a prompt for an AI video/image generator, \
written from the same brief by different models.

Pick the strongest draft, or merge the best parts into one. Judge on:
- faithfulness to the brief — a beautiful prompt for the wrong thing loses;
- concrete visual detail a generator can act on, over adjectives it cannot;
- one continuous described action, not a list of shots;
- no contradictions (a locked camera that then pans).

Answer with JSON and nothing else:
{"choice": <1-based index of the draft you took most from>, \
"prompt": "<the final prompt>", "why": "<one sentence, in Vietnamese>"}"""


@dataclass
class Draft:
    provider: str
    text: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text.strip())


@dataclass
class Ensemble:
    prompt: str
    drafts: list[Draft] = field(default_factory=list)
    judge: str = ""
    why: str = ""
    chosen: Optional[int] = None

    @property
    def usable_drafts(self) -> list[Draft]:
        return [d for d in self.drafts if d.ok]


async def available_providers() -> list[str]:
    """The providers that could answer right now, in preference order."""
    from flowboard.services.llm import registry

    async def _check(name: str) -> Optional[str]:
        provider = registry.get_provider(name)
        if provider is None:
            return None
        try:
            return name if await provider.is_available() else None
        except Exception:  # pragma: no cover — availability must never raise
            logger.warning("ensemble: %s availability probe failed", name, exc_info=True)
            return None

    checked = await asyncio.gather(*(_check(n) for n in PROVIDERS))
    return [name for name in checked if name]


async def _draft(name: str, user_prompt: str, system_prompt: str, timeout: float) -> Draft:
    from flowboard.services.llm import registry
    from flowboard.services.llm.base import LLMError

    provider = registry.get_provider(name)
    if provider is None:
        return Draft(provider=name, error="unknown provider")
    try:
        text = await provider.run(
            user_prompt, system_prompt=system_prompt, timeout=timeout
        )
    except (LLMError, asyncio.TimeoutError) as exc:
        # One provider being down must not cost the ensemble the others.
        logger.info("ensemble: %s did not answer (%s)", name, exc)
        return Draft(provider=name, error=str(exc)[:200])
    return Draft(provider=name, text=(text or "").strip().strip('"').strip("'"))


async def compose(
    user_prompt: str,
    *,
    system_prompt: str = "",
    timeout: float = 120.0,
    max_chars: int = 900,
) -> Ensemble:
    """Draft with every available provider, then judge.

    Raises nothing for a partial ensemble: with one usable draft that draft
    is the answer, and the judge is skipped rather than asked to choose
    between one option and nothing.
    """
    from flowboard.services.llm.base import LLMError

    names = await available_providers()
    if not names:
        raise LLMError(
            "Không có provider nào sẵn sàng — mở Cài đặt để đăng nhập Claude/Codex "
            "hoặc thêm khoá Gemini."
        )

    drafts = list(
        await asyncio.gather(
            *(_draft(n, user_prompt, system_prompt, timeout) for n in names)
        )
    )
    usable = [d for d in drafts if d.ok]
    if not usable:
        raise LLMError("Mọi provider đều không trả lời được.")
    if len(usable) == 1:
        return Ensemble(
            prompt=_trim(usable[0].text, max_chars),
            drafts=drafts,
            judge=usable[0].provider,
            why=f"Chỉ {usable[0].provider} trả lời được.",
            chosen=1,
        )

    verdict = await _judge(usable, names[0], user_prompt, timeout)
    if verdict is None:
        # A judge that cannot answer is not a reason to lose the drafts.
        # First available provider's draft wins by the preference order.
        return Ensemble(
            prompt=_trim(usable[0].text, max_chars),
            drafts=drafts,
            judge="",
            why="Không chấm được — lấy bản của provider ưu tiên đầu.",
            chosen=1,
        )
    choice, prompt, why = verdict
    return Ensemble(
        prompt=_trim(prompt, max_chars),
        drafts=drafts,
        judge=names[0],
        why=why,
        chosen=choice,
    )


def _trim(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


async def _judge(
    drafts: list[Draft], judge_name: str, brief: str, timeout: float
) -> Optional[tuple[int, str, str]]:
    import json

    from flowboard.services.llm import registry
    from flowboard.services.llm.base import LLMError

    provider = registry.get_provider(judge_name)
    if provider is None:
        return None

    listing = "\n\n".join(
        f"--- BẢN {i} ({d.provider}) ---\n{d.text}" for i, d in enumerate(drafts, 1)
    )
    try:
        raw = await provider.run(
            f"BRIEF:\n{brief}\n\n{listing}",
            system_prompt=_JUDGE_SYSTEM,
            timeout=timeout,
        )
    except (LLMError, asyncio.TimeoutError) as exc:
        logger.info("ensemble: the judge did not answer (%s)", exc)
        return None

    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
        prompt = str(parsed["prompt"]).strip()
    except (json.JSONDecodeError, KeyError, TypeError, IndexError):
        logger.info("ensemble: the judge did not answer with JSON")
        return None
    if not prompt:
        return None
    choice = parsed.get("choice")
    if not isinstance(choice, int) or not 1 <= choice <= len(drafts):
        choice = 1
    return choice, prompt, str(parsed.get("why") or "").strip()
