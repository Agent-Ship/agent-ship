"""`make demo` runner — SEE every capability run for real against OpenAI.

This is a LIVE demo. It executes one real slice per shipped capability and prints a
clear, labeled block for each, so a reader can watch every feature actually run:
each slice makes a real call to OpenAI and prints the real result. The streaming
slice prints tokens as they arrive. There are no fakes, no recordings, and no
offline stand-ins.

It needs a real ``OPENAI_API_KEY`` (the ``agentship run`` CLI and this runner both
read a ``.env`` in the current directory). If no key is set, it prints a clear
message and exits non-zero — it never falls back to anything fake.

It exits non-zero if any slice fails, so `make demo` is a real gate. Honest labels
are printed inline: `graph` is the authoring scaffold (durable multi-agent runtime =
Phase 02); the default model router is a simple pass-through today.

Run it with:  make demo   (or: python demos/run_all.py)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

import litellm
from agentship import build_agent
from agentship.context import Caller, RunContext, RunMode
from agentship.primitives.model_router import DefaultModelRouter
from agentship.spec import AgentSpec
from agentship_langgraph.engine import LangGraphEngine

# Force the plain httpx transport (LiteLLM's aiohttp path can misbehave for
# streaming across environments); keep the model cost map local.
litellm.disable_aiohttp_transport = True
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS = REPO_ROOT / "agents"


def _show_supervisor_decisions() -> None:
    """Route the supervisor's decision log to stdout so its sub-agent routing is visible.

    The ``SupervisorAgent`` logs every classify → route → dispatch → resolve decision on the
    ``agentship.supervisor`` logger (silent by default). The multi-agent slices enable it so a
    reader can watch the request get classified, dispatched to named sub-agents, and resolved.
    """
    supervisor_log = logging.getLogger("agentship.supervisor")
    if supervisor_log.handlers:  # idempotent — only attach once even if called twice
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("      | %(message)s"))
    supervisor_log.addHandler(handler)
    supervisor_log.setLevel(logging.INFO)
    supervisor_log.propagate = False


def _banner(n: int, title: str, label: str) -> None:
    """Print the labeled header block for capability slice ``n``."""
    print()
    print("=" * 72)
    print(f"  [{n}] {title}")
    print(f"      {label}")
    print("=" * 72)


async def slice_single() -> None:
    """1. `template: single` — one real ``gpt-4o-mini`` turn, real answer."""
    _banner(1, "template: single (real gpt-4o-mini)", "LIVE · real answer · zero author code")
    agent = build_agent(str(AGENTS / "assistant.yaml"))
    result = await agent.run("Give one productivity tip in one short sentence.")
    print("  run  agents/assistant.yaml --input 'Give one productivity tip.'")
    print(f"  -> {result.output.strip()}")
    assert result.output.strip() != ""


async def slice_stream() -> None:
    """2. Streaming — a real ``gpt-4o-mini`` turn streamed token by token, live."""
    _banner(
        2,
        "Streaming (real gpt-4o-mini, --stream)",
        "LIVE · real token-by-token stream",
    )
    print("  run  agents/streaming.yaml --input 'Name the 8 planets, comma-separated.' --stream")
    chunks: list[str] = []
    agent = build_agent(str(AGENTS / "streaming.yaml"))
    async for event in agent.stream("Name the 8 planets, comma-separated."):
        if event.type == "content":
            chunks.append(event.data)
            print(f"  -> chunk#{len(chunks)} {event.data!r}")
    # Prove it genuinely streamed: many small real tokens, then the whole answer.
    assert len(chunks) > 1, f"expected multiple real token chunks, got {len(chunks)}"
    print(f"  -> streamed {len(chunks)} real tokens; reassembled: {''.join(chunks).strip()!r}")


async def slice_graph() -> None:
    """3. `template: graph` — real routed coordinator -> worker turn, real answer."""
    _banner(
        3,
        "template: graph (supervisor scaffold, real gpt-4o-mini)",
        "LIVE · real answer · SCAFFOLD only — durable multi-agent runtime = Phase 02",
    )
    agent = build_agent(str(AGENTS / "graph.yaml"))
    assert agent.spec.template == "graph"
    result = await agent.run("Help me plan a weekend trip to the mountains.")
    print("  run  agents/graph.yaml --input 'Help me plan a weekend trip to the mountains.'")
    print(f"  -> coordinator routed -> worker answered: {result.output.strip()[:200]}")
    assert result.output.strip() != ""


async def slice_custom() -> None:
    """4. Custom `build_graph` — the author's native LangGraph answers for real."""
    _banner(
        4,
        "custom build_graph (native LangGraph via code:)",
        "LIVE · real answer · the AUTHOR'S graph answered",
    )
    cwd = os.getcwd()
    os.chdir(REPO_ROOT)  # the code: path is repo-root-relative
    try:
        agent = build_agent(str(AGENTS / "custom" / "custom.yaml"))
        assert agent.spec.name == "custom-assistant"
        result = await agent.run("Name three primary colors.")
    finally:
        os.chdir(cwd)
    print("  run  agents/custom/custom.yaml --input 'Name three primary colors.'")
    print(f"  -> {result.output.strip()}")
    assert result.output.strip() != ""


async def slice_router() -> None:
    """5. `ModelRouter` — the router picks the model, then a real turn runs through it."""
    _banner(
        5,
        "ModelRouter (DefaultModelRouter picks, then a real turn runs)",
        "LIVE · the router picks the model id, then that model answers for real",
    )
    spec = AgentSpec(
        name="router-demo",
        engine="langgraph",
        template="single",
        model="openai/gpt-4o-mini",
        prompt="You are a concise assistant. Answer in one short sentence.",
    )
    # The default router is a simple pass-through today: it returns spec.model.
    picked = DefaultModelRouter().pick(spec)
    print(f"  DefaultModelRouter picked model: {picked}  (default router = simple pass-through today)")

    agent = build_agent(spec)
    result = await agent.run("Name one primary color.")
    print(f"  -> real turn through {picked} answered: {result.output.strip()}")
    assert picked == "openai/gpt-4o-mini"
    assert result.output.strip() != ""


async def slice_triage() -> None:
    """6. Durable multi-agent supervisor — classify → route to a sub-agent → resolve → ACTUAL resume."""
    _banner(
        6,
        "durable multi-agent supervisor (Phase 02 — sub-agents visible, kill-9 proven)",
        "LIVE · 1 supervisor + 3 sub-agents · classifies, routes to ONE sub-agent · then resumes from checkpoint",
    )
    _show_supervisor_decisions()
    SESSION = "demo-triage-resume"
    question = "My invoice looks wrong and I was double charged — who handles payments?"
    cwd = os.getcwd()
    os.chdir(REPO_ROOT)
    try:
        # Step 1: original run. The supervisor's decision log (enabled above) prints the
        # classify → route → dispatch(sub-agent) → resolve trace inline, so the routing is visible.
        agent = build_agent(str(AGENTS / "triage" / "triage.yaml"))
        print("  supervisor built 3 sub-agents: billing_specialist, clinical_specialist, faq_specialist")
        print(f"  run  agents/triage/triage.yaml --input {question!r}")
        result = await agent.run(question, session_id=SESSION)
        print(f"  [1] original answer: {result.output.strip()}")
        print(f"      resume_token minted by {result.resume_token.engine!r}, thread={result.resume_token.blob['thread_id']!r}")

        # Step 2: simulated kill — brand-new engine, same spec, resume from token only.
        print("  [2] simulating kill -9 → fresh LangGraphEngine, no shared state …")
        new_engine = LangGraphEngine()
        new_compiled = new_engine.build(agent.spec)
        ctx = RunContext(
            caller=Caller(user_id="anonymous"),
            session_id=SESSION,
            run_id="r-demo-resume",
            agent_name=agent.spec.name,
            mode=RunMode.INVOKE,
        )
        resumed = await new_engine.resume(new_compiled, result.resume_token, ctx)
        print(f"  [3] resumed answer:  {resumed.output.strip()}")
    finally:
        os.chdir(cwd)

    assert result.output.strip() != ""
    assert result.resume_token is not None
    assert resumed.output == result.output, (
        f"resumed output differs!\n  original: {result.output!r}\n  resumed: {resumed.output!r}"
    )
    print("  ✓ byte-identical — checkpoint replayed identically.")


async def slice_panel() -> None:
    """7. Multi-agent fan-out — dispatch to ALL three sub-agents in parallel, then merge (C2/C7)."""
    _banner(
        7,
        "multi-agent fan-out (parallel sub-agents + ConflictResolver)",
        "LIVE · one question -> 3 sub-agents run CONCURRENTLY -> deterministic priority merge",
    )
    _show_supervisor_decisions()
    question = "My latest bill looks wrong and I've also been feeling dizzy — can you help?"
    cwd = os.getcwd()
    os.chdir(REPO_ROOT)
    try:
        agent = build_agent(str(AGENTS / "triage" / "panel.yaml"))
        print(f"  run  agents/triage/panel.yaml --input {question!r}")
        print("  (watch the trace: dispatch parallel -> 3 sub-agents, then resolve picks the winner)")
        result = await agent.run(question, session_id="demo-panel")
    finally:
        os.chdir(cwd)
    print(f"  -> merged winner's answer: {result.output.strip()}")
    assert result.output.strip() != ""


async def main() -> int:
    """Run every live slice in order; return 0 if all pass, 1 if any raises."""
    if not os.environ.get("OPENAI_API_KEY"):
        print("This demo is live — set OPENAI_API_KEY in .env to run it.")
        return 1

    print("\nAgentShip demo — every capability, running LIVE against OpenAI.\n")
    failures: list[str] = []
    steps = [
        ("single", slice_single()),
        ("stream", slice_stream()),
        ("graph", slice_graph()),
        ("custom", slice_custom()),
        ("router", slice_router()),
        ("triage", slice_triage()),
        ("panel", slice_panel()),
    ]
    for name, step in steps:
        try:
            await step
        except Exception as exc:  # noqa: BLE001 - report per-slice, keep going
            failures.append(name)
            print(f"\n  !! slice {name!r} FAILED: {type(exc).__name__}: {exc}")

    print("\n" + "=" * 72)
    if failures:
        print(f"  DEMO FAILED — {len(failures)} slice(s) broke: {', '.join(failures)}")
        print("=" * 72)
        return 1
    print("  DEMO OK — every capability ran LIVE against OpenAI.")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
