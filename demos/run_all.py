"""`make demo` runner — SEE every Phase 00-01 capability run, keyless, in one shot.

This executes one runnable slice per shipped capability and prints a clear,
labeled block for each so a reader can watch every feature actually run with **no
API key**:

* offline slices (echo, stream, router, deepagents) run live against the kernel /
  a fake model — no network;
* real-model slices (single, graph, custom) replay a committed, redacted VCR
  cassette — the exact recorded round-trip, no key, no network.

It exits non-zero if any slice fails, so `make demo` is a real gate, not a demo
reel. Honest labels are printed inline: `graph` is the authoring scaffold (durable
multi-agent runtime = Phase 02); `deepagents` is compiles-only today (autonomous
tool-using turn = Phase 03).

Run it with:  make demo   (or: python demos/run_all.py)
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Keyless & hermetic: never let a stray real key change behaviour, and make the
# cassette replays' OpenAI client construct without one. Set before importing the
# framework / vcr so replay is deterministic.
os.environ.pop("OPENAI_API_KEY", None)
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm  # noqa: E402

litellm.disable_aiohttp_transport = True

import vcr  # noqa: E402  (pulled in by pytest-recording; used to replay cassettes here)
from agentship import build_agent  # noqa: E402
from agentship.context import Caller, RunContext, RunMode  # noqa: E402
from agentship.engines.base import (  # noqa: E402
    ENGINES,
    Engine,
    EngineCapabilities,
    Result,
)
from agentship.primitives.model_router import (  # noqa: E402
    DefaultModelRouter,
    stamp_routed_model,
)
from agentship.spec import AgentSpec  # noqa: E402
from langchain_core.language_models.fake_chat_models import (  # noqa: E402
    FakeListChatModel,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS = REPO_ROOT / "agents"
CASSETTES = REPO_ROOT / "tests" / "cassettes"

# A placeholder key so the OpenAI SDK can build a client for cassette *replay*
# (VCR matches on URI + body, not the redacted auth header).
os.environ["OPENAI_API_KEY"] = "sk-test-placeholder-for-replay"

# VCR replay config mirrors tests/conftest.py: strip the key query param, match on
# method+host+path+body (not query), and never reach the network (record_mode=none).
_VCR = vcr.VCR(
    filter_headers=[
        ("authorization", "REDACTED"),
        ("api-key", "REDACTED"),
        ("x-api-key", "REDACTED"),
        ("x-goog-api-key", "REDACTED"),
    ],
    filter_query_parameters=[("key", "REDACTED"), ("api_key", "REDACTED")],
    match_on=["method", "host", "path", "body"],
    record_mode="none",
)


def _banner(n: int, title: str, label: str) -> None:
    """Print the labeled header block for capability slice ``n``."""
    print()
    print("=" * 72)
    print(f"  [{n}] {title}")
    print(f"      {label}")
    print("=" * 72)


async def slice_echo() -> None:
    """1. Echo walking skeleton — keyless kernel run, real `echo: <input>` output."""
    _banner(1, "Echo walking skeleton (engine: echo)", "keyless · real kernel output · no model")
    agent = build_agent(str(AGENTS / "echo.yaml"))
    result = await agent.run("hello from the demo")
    print(f"  run  agents/echo.yaml --input 'hello from the demo'")
    print(f"  -> {result.output}")
    assert result.output == "echo: hello from the demo"


async def slice_stream() -> None:
    """2. Streaming — the echo agent streamed, keyless; show events arrive."""
    _banner(2, "Streaming (engine: echo, --stream)", "keyless · real streamed events")
    agent = build_agent(str(AGENTS / "echo.yaml"))
    print("  run  agents/echo.yaml --input 'stream this' --stream")
    chunks: list[str] = []
    async for event in agent.stream("stream this"):
        print(f"  -> event: type={event.type!r}" + (f" data={event.data!r}" if event.data else ""))
        if event.type == "content":
            chunks.append(event.data)
    assert "".join(chunks) == "echo: stream this"


async def slice_single() -> None:
    """3. `template: single` — the real-model assistant, replayed from a cassette."""
    _banner(3, "template: single (real gpt-4o-mini)", "keyless · replayed cassette · zero author code")
    cassette = CASSETTES / "test_smoke" / "test_demo_assistant_returns_a_non_empty_answer.yaml"
    with _VCR.use_cassette(str(cassette)):
        agent = build_agent(str(AGENTS / "assistant.yaml"))
        result = await agent.run("Give one productivity tip.")
    print("  run  agents/assistant.yaml --input 'Give one productivity tip.'")
    print(f"  -> {result.output.strip()}")
    assert result.output.strip() != ""


async def slice_graph() -> None:
    """4. `template: graph` — routed coordinator -> worker turn, replayed."""
    _banner(
        4,
        "template: graph (supervisor scaffold, real gpt-4o-mini)",
        "keyless · replayed cassette · SCAFFOLD only — durable multi-agent runtime = Phase 02",
    )
    cassette = CASSETTES / "test_graph" / "test_graph_scaffold_routes_and_returns_a_non_empty_answer.yaml"
    with _VCR.use_cassette(str(cassette)):
        agent = build_agent(str(AGENTS / "graph.yaml"))
        assert agent.spec.template == "graph"
        result = await agent.run("Help me plan a weekend trip to the mountains.")
    print("  run  agents/graph.yaml --input 'Help me plan a weekend trip to the mountains.'")
    print(f"  -> coordinator routed -> worker answered: {result.output.strip()[:200]}")
    assert result.output.strip() != ""


def slice_deepagents() -> None:
    """5. `template: deepagents` — HONEST compiles-only proof (no live-turn claim)."""
    _banner(
        5,
        "template: deepagents (prebuilt autonomous agent)",
        "keyless · fake model · COMPILES ONLY — autonomous tool-using turn = Phase 03",
    )
    try:
        import deepagents  # noqa: F401
    except ImportError:
        print("  -> deepagents extra not installed (pip install deepagents==0.6.12); slice SKIPPED")
        return

    # Build offline with a fake model — no live-turn claim, just a compiled graph.
    import agentship_langgraph.models as models_module

    fake = FakeListChatModel(responses=["ok"])
    original = models_module.resolve_model
    models_module.resolve_model = lambda *a, **k: fake  # type: ignore[assignment]
    try:
        agent = build_agent(str(AGENTS / "deepagents.yaml"))
    finally:
        models_module.resolve_model = original  # type: ignore[assignment]
    assert agent.compiled is not None
    print("  build agents/deepagents.yaml (offline, fake model)")
    print(f"  -> deepagents graph compiled: {type(agent.compiled).__name__}")


async def slice_custom() -> None:
    """6. Custom `build_graph` — the author's native LangGraph answered, replayed."""
    _banner(
        6,
        "custom build_graph (native LangGraph via code:)",
        "keyless · replayed cassette · the AUTHOR'S graph answered",
    )
    cassette = CASSETTES / "test_custom" / "test_custom_build_graph_answers_via_the_authors_graph.yaml"
    cwd = os.getcwd()
    os.chdir(REPO_ROOT)  # the code: path is repo-root-relative
    try:
        with _VCR.use_cassette(str(cassette)):
            agent = build_agent(str(AGENTS / "custom" / "custom.yaml"))
            assert agent.spec.name == "custom-assistant"
            result = await agent.run("Name three primary colors.")
    finally:
        os.chdir(cwd)
    print("  run  agents/custom/custom.yaml --input 'Name three primary colors.'")
    print(f"  -> {result.output.strip()}")
    assert result.output.strip() != ""


async def slice_router() -> None:
    """7. `ModelRouter` mechanism — route step stamps, adapter reads (keyless, real)."""
    _banner(
        7,
        "ModelRouter mechanism (DefaultModelRouter)",
        "keyless · real mechanism · no LLM call — route step stamps, engine reads",
    )
    spec = AgentSpec(name="a", engine="langgraph", model="openai/gpt-4o-mini")
    chosen = DefaultModelRouter().pick(spec)
    print(f"  DefaultModelRouter picked: {chosen}")

    # Prove the runtime stamps it *before* the engine, and the engine (adapter) reads it.
    seen: dict = {}

    class _RecordingEngine(Engine):
        """Records the routed_model the route step stamped, at run time."""

        name = "demo_runner_recording_router"
        capabilities = EngineCapabilities()

        def build(self, spec, authored=None):
            """Nothing to compile — the spec is the artifact."""
            return spec

        async def run(self, compiled, text, ctx):
            """Capture what the route step stamped on the context, then echo."""
            seen["routed_model"] = ctx.routed_model
            return Result(output=text)

    ENGINES.register("demo_runner_recording_router", _RecordingEngine)
    try:
        agent = build_agent(
            AgentSpec(name="a", engine="demo_runner_recording_router", model="openai/gpt-4o-mini")
        )
        await agent.run("route me")
    finally:
        ENGINES._providers.pop("demo_runner_recording_router", None)

    # Also show the stamping primitive directly on a bare context.
    ctx = RunContext(
        caller=Caller(user_id="u1"),
        session_id="s1",
        run_id="r1",
        agent_name="a",
        mode=RunMode.INVOKE,
    )
    stamp_routed_model(spec, ctx)
    print(f"  route step stamped ctx.routed_model: {ctx.routed_model}")
    print(f"  engine adapter READ routed_model at run time: {seen['routed_model']}")
    assert seen["routed_model"] == "openai/gpt-4o-mini"
    assert ctx.routed_model == "openai/gpt-4o-mini"


async def main() -> int:
    """Run every slice in order; return 0 if all pass, 1 if any raises."""
    print("\nAgentShip demo — every Phase 00-01 capability, running keyless.\n")
    failures: list[str] = []
    steps = [
        ("echo", slice_echo()),
        ("stream", slice_stream()),
        ("single", slice_single()),
        ("graph", slice_graph()),
        ("deepagents", slice_deepagents),  # sync
        ("custom", slice_custom()),
        ("router", slice_router()),
    ]
    for name, step in steps:
        try:
            if asyncio.iscoroutine(step):
                await step
            else:
                step()  # sync slice
        except Exception as exc:  # noqa: BLE001 - report per-slice, keep going
            failures.append(name)
            print(f"\n  !! slice {name!r} FAILED: {type(exc).__name__}: {exc}")

    print("\n" + "=" * 72)
    if failures:
        print(f"  DEMO FAILED — {len(failures)} slice(s) broke: {', '.join(failures)}")
        print("=" * 72)
        return 1
    print("  DEMO OK — every Phase 00-01 capability ran keyless.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
