"""T3 proof: the LangGraph engine, offline, with a fake chat model injected.

No network: these monkeypatch :func:`agentship_langgraph.models.resolve_model` so the engine
compiles a graph over a deterministic ``FakeListChatModel``. They assert the real
mechanism — ``run`` returns the model's answer through the compiled graph;
``stream`` yields >=1 content chunk then a terminal ``done``; and the capability
gate rejects ``output_schema:``/``members:``/``durability:`` on this engine, which
honestly declares only ``streaming`` (structured output, multi-agent, and
durability are unbuilt — declare, don't fake).
"""

from __future__ import annotations

import agentship_langgraph.models as models_module
import pytest
from agentship.errors import CapabilityError, ModelError
from agentship.runtime import build_agent
from agentship.spec import AgentSpec, MemberSpec, ModelParams
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


@pytest.fixture
def fake_model(monkeypatch):
    """Inject a deterministic fake chat model in place of the real LiteLLM one.

    Patches :func:`agentship_langgraph.models.resolve_model` (the engine's model seam) so
    ``build_agent`` compiles its graph over a ``FakeListChatModel`` — proving the
    wiring end to end with zero network calls.
    """
    fake = FakeListChatModel(responses=["Red, green, and blue."])
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)
    return fake


async def test_run_returns_the_models_answer_through_the_graph(fake_model):
    """run() returns the fake model's answer, routed through the compiled graph."""
    agent = build_agent(
        AgentSpec(name="a", engine="langgraph", model="x", prompt="You are helpful.")
    )
    result = await agent.run("Name three primary colors.")
    assert result.output == "Red, green, and blue."


async def test_stream_yields_content_chunks_then_done(fake_model):
    """stream() yields >=1 content chunk (token-level) then exactly one terminal done."""
    agent = build_agent(
        AgentSpec(name="a", engine="langgraph", model="x", prompt="p", streaming=True)
    )
    events = [e async for e in agent.stream("hi")]

    content = [e for e in events if e.type == "content"]
    assert len(content) >= 1
    # Token-level streaming: the answer arrives across multiple content chunks.
    assert len(content) > 1
    joined = "".join(e.data for e in content)
    assert joined == "Red, green, and blue."

    assert events[-1].type == "done"
    assert sum(1 for e in events if e.type == "done") == 1


class _NonStreamingModel(BaseChatModel):
    """A chat model that returns a whole ``AIMessage`` and never streams tokens.

    ``FakeListChatModel`` streams chunks by default (which is why it masked the
    real-provider zero-chunk bug). This model implements only ``_generate`` — with
    no ``_stream``/``_astream`` hook — so LangGraph's ``stream_mode="messages"``
    surfaces a single whole ``AIMessage`` (not an ``AIMessageChunk``), modelling a
    provider/model that ignores ``streaming=True`` and so exercising the engine's
    no-silent-empty fallback.
    """

    response: str = "The whole answer, undivided."

    @property
    def _llm_type(self) -> str:
        """LangChain's required model-type tag."""
        return "non-streaming-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        """Return the whole answer as a single non-streamed ``AIMessage``."""
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=self.response))]
        )


@pytest.fixture
def non_streaming_model(monkeypatch):
    """Inject a model that yields one whole AIMessage instead of token chunks."""
    fake = _NonStreamingModel()
    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: fake)
    return fake


async def test_stream_falls_back_to_full_message_when_model_does_not_stream(
    non_streaming_model,
):
    """A non-streaming model still yields its answer as one content event, then done.

    Guards the no-silent-empty contract: when the model returns a whole
    ``AIMessage`` (no ``AIMessageChunk`` tokens), ``stream`` must not yield an empty
    stream — it falls back to emitting that full content as a single ``content``
    event before the terminal ``done``.
    """
    agent = build_agent(
        AgentSpec(name="a", engine="langgraph", model="x", prompt="p", streaming=True)
    )
    events = [e async for e in agent.stream("hi")]

    content = [e for e in events if e.type == "content"]
    assert len(content) == 1  # exactly one fallback event — no double-emit
    assert content[0].data == "The whole answer, undivided."

    assert events[-1].type == "done"
    assert sum(1 for e in events if e.type == "done") == 1


async def test_stream_does_not_double_emit_when_model_streams(fake_model):
    """The streaming path is unaffected by the fallback: no extra full-message event.

    When real chunks arrive, the reassembled content must equal the answer exactly
    (no duplicated whole-message content appended by the fallback path).
    """
    agent = build_agent(
        AgentSpec(name="a", engine="langgraph", model="x", prompt="p", streaming=True)
    )
    events = [e async for e in agent.stream("hi")]
    content = [e for e in events if e.type == "content"]
    assert len(content) > 1
    assert "".join(e.data for e in content) == "Red, green, and blue."


@pytest.fixture
def capture_resolve(monkeypatch):
    """Patch resolve_model to record the (model, kwargs) it is called with.

    Returns a dict the test reads after ``build_agent``; the fake model it hands
    back keeps the graph offline. Proves the engine threads ``spec.params`` into
    the model call — the real mechanism, not a constant.
    """
    calls: dict = {}
    fake = FakeListChatModel(responses=["ok"])

    def fake_resolve(model, **kwargs):
        """Record the call and return a deterministic fake model."""
        calls["model"] = model
        calls["kwargs"] = kwargs
        return fake

    monkeypatch.setattr(models_module, "resolve_model", fake_resolve)
    return calls


def test_params_thread_into_resolve_model(capture_resolve):
    """spec.params (temperature/api_base) reach resolve_model as kwargs."""
    build_agent(
        AgentSpec(
            name="a",
            engine="langgraph",
            model="ollama/llama3",
            params=ModelParams(temperature=0.2, api_base="http://localhost:11434"),
        )
    )
    assert capture_resolve["model"] == "ollama/llama3"
    assert capture_resolve["kwargs"]["temperature"] == 0.2
    assert capture_resolve["kwargs"]["api_base"] == "http://localhost:11434"


def test_no_params_passes_no_extra_kwargs(capture_resolve):
    """A spec with no params threads no extra kwargs — the model keeps its defaults."""
    build_agent(AgentSpec(name="a", engine="langgraph", model="openai/gpt-4o-mini"))
    assert capture_resolve["model"] == "openai/gpt-4o-mini"
    assert capture_resolve["kwargs"] == {}


def test_none_params_are_dropped_before_threading(capture_resolve):
    """A params block with only None fields threads no kwargs (exclude_none)."""
    build_agent(
        AgentSpec(
            name="a",
            engine="langgraph",
            model="openai/gpt-4o-mini",
            params=ModelParams(temperature=0.5),
        )
    )
    # Only the supplied field is threaded; the unset (None) ones are dropped.
    assert capture_resolve["kwargs"] == {"temperature": 0.5}


def test_output_schema_rejected_by_capability_gate(fake_model):
    """This engine declares structured_output='none' (not built yet) — a schema fails fast.

    Structured output is a phase-04 deliverable; until the graph actually validates
    a target, declaring ``structured_output`` would be an over-claim (declare, don't
    fake). So an ``output_schema`` spec must be rejected by the capability gate.
    """
    with pytest.raises(CapabilityError) as exc:
        build_agent(
            AgentSpec(name="a", engine="langgraph", model="x", output_schema="mypkg:Answer")
        )
    assert "structured output" in str(exc.value).lower()


def test_durability_rejected_by_capability_gate(fake_model):
    """This engine declares durability='none' — a checkpoint request fails fast."""
    with pytest.raises(CapabilityError) as exc:
        build_agent(
            AgentSpec(name="a", engine="langgraph", model="x", durability="checkpoint")
        )
    assert "durable" in str(exc.value).lower() or "durability" in str(exc.value).lower()


def test_members_rejected_by_capability_gate(fake_model):
    """This engine is not multi-agent — a members: spec fails fast, not silently dropped."""
    spec = AgentSpec(
        name="team", engine="langgraph", model="x", members=[MemberSpec(name="m1")]
    )
    with pytest.raises(CapabilityError) as exc:
        build_agent(spec)
    assert "multi-agent" in str(exc.value).lower()


async def test_dead_api_base_surfaces_clean_model_error(monkeypatch):
    """A connection failure (e.g. dead api_base) surfaces as ModelError, not a traceback.

    Simulates the local-model "server not running" case offline: the fake model
    raises a connection-style error on invoke; the engine must map it through
    ``map_model_error``'s generic branch into a clean ModelError naming the model.
    """

    class _DeadModel:
        """A stand-in model whose call fails as if the api_base were unreachable."""

        def invoke(self, messages):
            """Raise a connection-style error, as a dead local server would."""
            raise ConnectionError("Connection refused to http://localhost:11434")

    monkeypatch.setattr(models_module, "resolve_model", lambda *a, **k: _DeadModel())
    agent = build_agent(
        AgentSpec(
            name="a",
            engine="langgraph",
            model="ollama/llama3",
            params=ModelParams(api_base="http://localhost:11434"),
        )
    )
    with pytest.raises(ModelError) as exc:
        await agent.run("hi")
    # The generic branch names the model and carries the cause — no credential demand.
    assert "ollama/llama3" in str(exc.value)
    assert "OPENAI_API_KEY" not in str(exc.value)
