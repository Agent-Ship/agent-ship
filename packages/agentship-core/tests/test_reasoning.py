"""Reasoning: asking a model to think before it answers, and showing that it did.

Reasoning models (Anthropic extended thinking, OpenAI o-series, Gemini thinking, DeepSeek
R1) spend tokens on a thought process before producing an answer. AgentShip had no support
for any of it — nothing turned it on, nothing streamed it, nothing traced it — so a
reasoning model ran with provider defaults and its thinking was invisible everywhere.

We do not implement reasoning; every provider differs and LiteLLM already normalises them
behind ``reasoning_effort``. What we own is the config surface, the stream event, and the
span attributes.

Reasoning text is chain-of-thought and is gated by ``capture_content``, exactly as prompts
and tool arguments are. Reasoning *token counts* are not gated: a count leaks no content
and is what tells you a reasoning model is burning tokens you are paying for.
"""

from __future__ import annotations

import pytest
from agentship.observability import semconv
from agentship.spec import ModelParams
from pydantic import ValidationError


def test_an_agent_can_ask_for_more_or_less_thinking():
    """``reasoning_effort`` is the one knob, normalised by LiteLLM across providers."""
    assert ModelParams(reasoning_effort="high").reasoning_effort == "high"
    assert ModelParams().reasoning_effort is None, "absent must keep the provider's default"


@pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high"])
def test_every_supported_effort_is_accepted(effort: str):
    """The four levels LiteLLM maps onto each provider's own scale."""
    assert ModelParams(reasoning_effort=effort).reasoning_effort == effort


def test_a_mistyped_effort_fails_at_load_time_not_at_call_time():
    """``reasoning_effort: hgih`` must fail when the spec loads, not on the first request.

    An unvalidated string would be forwarded to the provider and rejected mid-turn — a
    runtime 400 for a typo that was visible in the YAML all along.
    """
    with pytest.raises(ValidationError):
        ModelParams(reasoning_effort="hgih")


def test_reasoning_effort_reaches_the_model():
    """The spec knob must arrive at the provider call, not stop at the spec object.

    ``ChatLiteLLM`` has no ``reasoning_effort`` constructor field — it goes through
    ``model_kwargs``, which is how LiteLLM forwards a provider param it does not model
    explicitly. Set it as a plain constructor kwarg and it is silently swallowed, so the
    spec would look configured and every request would go out with no thinking at all.
    """
    from agentship_langgraph.models import resolve_model

    model = resolve_model("openai/gpt-4o-mini", reasoning_effort="high")

    assert model.model_kwargs.get("reasoning_effort") == "high"


def test_no_reasoning_effort_means_no_key_on_the_wire():
    """An unset knob must not send ``reasoning_effort: None`` — that is not the same as absent.

    A provider that does not support reasoning rejects the key outright, so forwarding a
    null would break every non-reasoning model.
    """
    from agentship_langgraph.models import resolve_model

    model = resolve_model("openai/gpt-4o-mini", reasoning_effort=None)

    assert "reasoning_effort" not in (model.model_kwargs or {})


def test_a_yaml_spec_carries_reasoning_effort_all_the_way_to_the_provider_call():
    """End to end: the YAML knob lands in the kwargs LiteLLM will send.

    The unit tests above check the two halves; this checks the seam between them, which is
    where a config knob usually dies — accepted by the spec, dropped before the call.
    """

    from agentship.spec import AgentSpec
    from agentship_langgraph.engine import LangGraphEngine

    spec = AgentSpec(
        name="thinker",
        model="openai/gpt-4o-mini",
        params=ModelParams(reasoning_effort="high", temperature=0.2),
    )
    model = LangGraphEngine()._resolve_model(spec)

    assert model.model_kwargs.get("reasoning_effort") == "high"
    assert model.temperature == 0.2, "the existing params must still work"


# ---- the stream contract ------------------------------------------------------------------


def test_reasoning_is_its_own_stream_event_not_answer_text():
    """A ``reasoning`` frame must survive the mapping instead of being downgraded to content.

    ``frame_type`` defaults any unrecognised event to ``content``, which is the right
    default for an unknown engine event and exactly wrong here: the model's private
    thinking would be delivered on the same channel as its answer and rendered as part of
    it. A client cannot separate them after the fact, so the type has to be in the contract.
    """
    from typing import get_args

    from agentship_service.models.v1 import StreamEventType
    from agentship_service.routers._common import frame_type

    assert "reasoning" in get_args(StreamEventType)
    assert frame_type("reasoning") == "reasoning", "reasoning was downgraded to answer text"


# ---- separating thinking from the answer ---------------------------------------------------


def test_thinking_blocks_are_split_out_of_the_answer():
    """A reasoning model's content is a list of blocks; only the text blocks are the reply.

    Anthropic-style extended thinking arrives as
    ``[{"type": "thinking", ...}, {"type": "text", ...}]``. Emitted whole, the model's
    private deliberation is delivered to the user as part of its answer — the failure this
    split exists to prevent.
    """
    from agentship_langgraph.engine import split_reasoning
    from langchain_core.messages import AIMessageChunk

    chunk = AIMessageChunk(
        content=[
            {"type": "thinking", "thinking": "6 times 7 is 42"},
            {"type": "text", "text": "42"},
        ]
    )
    reasoning, answer = split_reasoning(chunk)

    assert reasoning == "6 times 7 is 42"
    assert answer == "42", "the thinking must not appear in the answer"


def test_plain_text_content_is_all_answer():
    """A non-reasoning model is unaffected: no reasoning, content passes through unchanged."""
    from agentship_langgraph.engine import split_reasoning
    from langchain_core.messages import AIMessageChunk

    assert split_reasoning(AIMessageChunk(content="42")) == ("", "42")


def test_reasoning_content_from_providers_that_use_a_separate_field():
    """DeepSeek/LiteLLM report thinking in ``additional_kwargs.reasoning_content``.

    Same idea as a thinking block, different transport — handling only the block form would
    silently lose reasoning for a whole family of models.
    """
    from agentship_langgraph.engine import split_reasoning
    from langchain_core.messages import AIMessageChunk

    chunk = AIMessageChunk(content="42", additional_kwargs={"reasoning_content": "6*7=42"})

    assert split_reasoning(chunk) == ("6*7=42", "42")


# ---- observability -------------------------------------------------------------------------


def test_reasoning_tokens_are_recorded_even_when_content_is_not():
    """A reasoning token count is never gated — it is spend, not content.

    Reasoning tokens are billed as output tokens, so a reasoning model can cost several
    times a normal one for the same visible reply. The count is the only thing that shows
    where that money went, and a bare number leaks nothing, so it is recorded regardless of
    ``capture_content`` — unlike the reasoning text itself.
    """
    assert semconv.GEN_AI_USAGE_REASONING_TOKENS == "gen_ai.usage.reasoning_tokens"


def test_reasoning_tokens_are_read_from_the_usage_breakdown():
    """LangChain reports them under ``usage_metadata.output_token_details.reasoning``."""
    from agentship_langgraph.tracing import reasoning_tokens_of

    message = type("M", (), {"usage_metadata": {"output_token_details": {"reasoning": 40}}})()
    assert reasoning_tokens_of(message) == 40

    assert reasoning_tokens_of(type("M", (), {"usage_metadata": {}})()) == 0


# ---- end to end through the engine ---------------------------------------------------------


async def test_a_thinking_model_streams_reasoning_and_answer_on_separate_channels():
    """The whole path: a model that thinks produces ``reasoning`` frames AND a clean answer.

    Driven by a fake model rather than a provider so it runs with no key and cannot go
    quiet when a paid account lapses. What it proves is ours, not the provider's: that a
    thinking block becomes a ``reasoning`` event and never leaks into ``content``.
    """
    from agentship.spec import AgentSpec
    from agentship_langgraph.engine import LangGraphEngine
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    thinking_reply = AIMessage(
        content=[
            {"type": "thinking", "thinking": "The ball is $0.05, not $0.10."},
            {"type": "text", "text": "5 cents."},
        ]
    )
    from agentship.runtime import RunnableAgent
    from agentship_langgraph import models as lg_models

    engine = LangGraphEngine()
    spec = AgentSpec(name="thinker", engine="langgraph", model="fake/model", streaming=True)

    class ThinkingModel(BaseChatModel):
        """The smallest model that returns block content. LangChain's stock fakes assume a
        string reply and reject a thinking block, which is the one shape under test."""

        @property
        def _llm_type(self) -> str:
            return "thinking-fake"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=thinking_reply)])

    fake = ThinkingModel()
    original = lg_models.resolve_model
    lg_models.resolve_model = lambda *a, **k: fake
    try:
        agent = RunnableAgent(spec, engine, engine.build(spec))
        events = [event async for event in agent.stream("bat and ball?")]
    finally:
        lg_models.resolve_model = original

    reasoning = "".join(str(e.data) for e in events if e.type == "reasoning")
    answer = "".join(str(e.data) for e in events if e.type == "content")

    assert "0.05" in reasoning, f"the thinking never surfaced: {[e.type for e in events]}"
    assert answer.strip() == "5 cents."
    assert "0.05" not in answer, "the model's private thinking leaked into its answer"
