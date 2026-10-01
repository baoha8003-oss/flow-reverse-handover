"""Three models draft the prompt; one of them picks the winner.

`run_llm` sends one feature to one provider. This runs the same brief past
every provider that is available, in parallel, then has a judge choose or
merge — and keeps every draft, because a prompt whose alternatives you
cannot see is one you cannot argue with.

Nearly free: Claude and Codex are signed in through OAuth, so their marginal
cost is a subscription already paid for. Gemini bills per call, which is why
the single-provider path stays the default.
"""
from __future__ import annotations

import json

import pytest

from flowboard.services import prompt_ensemble
from flowboard.services.llm.base import LLMError


class _Provider:
    """A provider that answers, fails, or is simply not signed in."""

    def __init__(self, reply: str = "", *, available: bool = True, fails: bool = False):
        self.reply, self._available, self.fails = reply, available, fails
        self.calls: list[dict] = []

    async def is_available(self) -> bool:
        return self._available

    async def run(self, prompt, *, system_prompt=None, attachments=None, timeout=90.0):
        self.calls.append({"prompt": prompt, "system": system_prompt})
        if self.fails:
            raise LLMError("provider down")
        return self.reply


def _judgement(choice=2, prompt="bản gộp", why="rõ hơn") -> str:
    return json.dumps({"choice": choice, "prompt": prompt, "why": why})


@pytest.fixture
def providers(monkeypatch):
    """Install a controllable set, keyed by the registry's own names."""
    from flowboard.services.llm import registry

    table: dict[str, _Provider] = {}
    monkeypatch.setattr(registry, "get_provider", lambda name: table.get(name))
    return table


# ── who takes part ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_only_signed_in_providers_are_asked(providers):
    """A CLI that is installed but not signed in must be skipped, not
    waited on — that is what the auth detection is for."""
    providers["claude"] = _Provider("bản A")
    providers["openai"] = _Provider("bản B", available=False)
    providers["gemini"] = _Provider(_judgement())

    assert await prompt_ensemble.available_providers() == ["claude", "gemini"]


@pytest.mark.asyncio
async def test_a_provider_that_raises_while_probing_is_skipped(providers):
    """Availability is a probe, and a probe that throws must not take the
    ensemble down with it."""

    class _Angry(_Provider):
        async def is_available(self):
            raise RuntimeError("boom")

    providers["claude"] = _Angry()
    providers["gemini"] = _Provider("bản A")
    assert await prompt_ensemble.available_providers() == ["gemini"]


@pytest.mark.asyncio
async def test_no_provider_at_all_says_what_to_do(providers):
    with pytest.raises(LLMError) as exc:
        await prompt_ensemble.compose("một brief")
    assert "Cài đặt" in str(exc.value)


# ── drafting ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_available_provider_drafts(providers):
    providers["claude"] = _Provider("bản A")
    providers["openai"] = _Provider("bản B")
    providers["gemini"] = _Provider(_judgement())

    result = await prompt_ensemble.compose("một brief", system_prompt="hệ thống")

    assert {d.provider for d in result.usable_drafts} == {"claude", "openai", "gemini"}
    # And each got the same brief — an ensemble whose members answer
    # different questions is not an ensemble.
    assert providers["claude"].calls[0]["prompt"] == "một brief"
    assert providers["openai"].calls[0]["system"] == "hệ thống"


@pytest.mark.asyncio
async def test_one_provider_failing_does_not_cost_the_others(providers):
    providers["claude"] = _Provider(fails=True)
    providers["openai"] = _Provider("bản B")
    providers["gemini"] = _Provider("bản C")

    result = await prompt_ensemble.compose("một brief")

    assert len(result.usable_drafts) == 2
    failed = [d for d in result.drafts if not d.ok]
    assert failed and failed[0].error, "the failure has to be visible, not dropped"


@pytest.mark.asyncio
async def test_every_provider_failing_is_an_error(providers):
    providers["claude"] = _Provider(fails=True)
    providers["gemini"] = _Provider(fails=True)
    with pytest.raises(LLMError):
        await prompt_ensemble.compose("một brief")


@pytest.mark.asyncio
async def test_a_single_answer_skips_the_judge(providers):
    """Asking a judge to choose between one option and nothing is a call
    spent on a foregone conclusion."""
    providers["claude"] = _Provider("bản duy nhất")
    result = await prompt_ensemble.compose("một brief")
    assert result.prompt == "bản duy nhất"
    assert len(providers["claude"].calls) == 1, "drafted once, never judged"


# ── judging ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_judge_sees_every_draft(providers):
    providers["claude"] = _Provider(_judgement(prompt="bản cuối"))
    providers["openai"] = _Provider("bản B")

    result = await prompt_ensemble.compose("một brief")

    assert result.prompt == "bản cuối"
    judged = providers["claude"].calls[-1]["prompt"]
    assert "bản B" in judged and "một brief" in judged


@pytest.mark.asyncio
async def test_the_judges_reason_is_kept(providers):
    providers["claude"] = _Provider(_judgement(why="giữ được bố cục"))
    providers["openai"] = _Provider("bản B")
    result = await prompt_ensemble.compose("một brief")
    assert result.why == "giữ được bố cục"
    assert result.chosen == 2


@pytest.mark.asyncio
async def test_a_fenced_judgement_parses(providers):
    providers["claude"] = _Provider(f"```json\n{_judgement()}\n```")
    providers["openai"] = _Provider("bản B")
    result = await prompt_ensemble.compose("một brief")
    assert result.prompt == "bản gộp"


@pytest.mark.asyncio
async def test_a_judge_that_answers_prose_does_not_lose_the_drafts(providers):
    """The drafts cost real calls. Falling back to the preferred
    provider's is better than throwing them away."""
    providers["claude"] = _Provider("tôi thích bản 2 hơn")
    providers["openai"] = _Provider("bản B")

    result = await prompt_ensemble.compose("một brief")

    assert result.prompt == "tôi thích bản 2 hơn"
    assert result.judge == ""
    assert len(result.usable_drafts) == 2


@pytest.mark.asyncio
async def test_an_out_of_range_choice_does_not_index_out_of_bounds(providers):
    providers["claude"] = _Provider(_judgement(choice=99))
    providers["openai"] = _Provider("bản B")
    result = await prompt_ensemble.compose("một brief")
    assert result.chosen == 1


@pytest.mark.asyncio
async def test_the_winner_is_length_capped(providers):
    providers["claude"] = _Provider(_judgement(prompt="x" * 5000))
    providers["openai"] = _Provider("bản B")
    result = await prompt_ensemble.compose("một brief", max_chars=100)
    assert len(result.prompt) <= 101  # the ellipsis


# ── the route ─────────────────────────────────────────────────────────


def test_the_route_returns_the_winner_and_the_drafts(client, providers):
    """Note what is stubbed and what is not: the PROVIDERS are, the route
    and the synthesiser are not. An earlier version of this test stubbed
    nothing and spawned the real CLIs — 33 seconds, and its result depended
    on whether the machine happened to be signed in."""
    providers["claude"] = _Provider(_judgement(prompt="bản cuối", why="rõ hơn"))
    providers["openai"] = _Provider("bản B")

    board = client.post("/api/boards", json={"name": "e"}).json()["id"]
    node = client.post("/api/nodes", json={
        "board_id": board, "type": "image", "x": 0, "y": 0, "data": {},
    }).json()

    r = client.post("/api/prompt/auto/ensemble", json={"node_id": node["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prompt"] == "bản cuối"
    assert body["why"] == "rõ hơn"
    assert {d["provider"] for d in body["drafts"]} == {"claude", "openai"}


def test_the_route_reports_no_provider_rather_than_hanging(client, providers):
    """`providers` is empty here, so nothing is signed in."""
    board = client.post("/api/boards", json={"name": "e"}).json()["id"]
    node = client.post("/api/nodes", json={
        "board_id": board, "type": "image", "x": 0, "y": 0, "data": {},
    }).json()
    r = client.post("/api/prompt/auto/ensemble", json={"node_id": node["id"]})
    assert r.status_code == 502
    assert "Cài đặt" in r.json()["detail"]


def test_an_unknown_node_is_an_error_not_an_empty_prompt(client, providers):
    providers["claude"] = _Provider("bản A")
    r = client.post("/api/prompt/auto/ensemble", json={"node_id": 999999})
    assert r.status_code == 502
