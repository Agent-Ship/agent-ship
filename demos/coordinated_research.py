"""The flagship demo: a coordinator routes a request to a QUICK or a DEEP research agent.

This ties the three agents together into the ecosystem the demo is about:

* ``agents/coordinator.yaml`` — a thin classifier that labels the request ``quick`` or ``deep``;
* ``agents/quick_search.yaml`` — a single-turn web search answering in seconds (durability none);
* ``agents/deep_research.yaml`` — a long, iterative, checkpointed agent that runs several rounds
  and **pauses to ask the human "go deeper?"**, surviving crashes and long waits (durability
  checkpoint).

The coordinator does NOT own the deep agent as a sub-agent — a supervisor's dispatch would swallow
the sub-agent's human pause. Instead this driver *routes then dispatches*: it asks the coordinator
for a label, then runs the matching standalone agent as its own top-level run, so the deep agent's
interrupt/resume works natively. On the deep path the driver drives the pause→decision loop, showing
the run pause, resume with the human's answer, and finally synthesize a report.

Usage (needs a real OPENAI_API_KEY; BRAVE_API_KEY optional for real search)::

    python demos/coordinated_research.py "Who won the 2026 Super Bowl?"        # -> quick path
    python demos/coordinated_research.py "Compare small modular reactor vendors in 2026"  # -> deep
    # Approve N extra deep rounds instead of declining at the first pause:
    DEEP_APPROVE_ROUNDS=2 python demos/coordinated_research.py "State of EU AI regulation"
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import litellm
from agentship.context import Caller, RunContext, RunMode

from agentship import build_agent

litellm.disable_aiohttp_transport = True
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

REPO_ROOT = Path(__file__).resolve().parents[1]
COORDINATOR = REPO_ROOT / "agents" / "coordinator.yaml"
QUICK = REPO_ROOT / "agents" / "quick_search.yaml"
DEEP = REPO_ROOT / "agents" / "deep_research.yaml"


def normalize_label(raw: str) -> str:
    """Map the coordinator's free-text reply to a ``"quick"`` or ``"deep"`` route.

    The coordinator is asked for exactly one word, but models add punctuation or a stray sentence;
    this looks for the word ``deep`` anywhere in the reply and routes there, otherwise defaults to
    ``quick``. Defaulting to quick is the safe bias — a fast single search is cheap to re-run if it
    was the wrong call, whereas wrongly launching the deep loop wastes rounds.
    """
    return "deep" if "deep" in raw.strip().lower() else "quick"


def _resume_ctx(session_id: str, run_id: str) -> RunContext:
    """A RunContext for resuming the deep run on the same checkpoint thread (``session_id``)."""
    return RunContext(
        caller=Caller(user_id="demo"),
        session_id=session_id,
        run_id=run_id,
        agent_name="deep-research",
        mode=RunMode.INVOKE,
    )


async def _run_quick(question: str) -> None:
    """Run the quick single-turn search agent and print its answer."""
    print("→ route: QUICK (single-turn web search)\n")
    agent = build_agent(str(QUICK))
    result = await agent.run(question, session_id="coord-quick")
    print(f"ANSWER:\n  {str(result.output).strip()}\n")


async def _run_deep(question: str) -> None:
    """Run the durable deep-research agent, driving the human pause→decision loop to a report.

    The agent runs its automatic rounds, then pauses (returns an interrupt payload + resume token).
    This driver approves up to ``DEEP_APPROVE_ROUNDS`` extra rounds (default 0 — decline at the
    first pause), resuming each time with the human's decision, until the agent synthesizes the
    final report. Each resume re-hydrates from the checkpoint, so this same loop would continue a
    run that had crashed between rounds.
    """
    print("→ route: DEEP (iterative, checkpointed, human-in-the-loop)\n")
    approvals_left = int(os.environ.get("DEEP_APPROVE_ROUNDS", "0"))
    session = "coord-deep"
    agent = build_agent(str(DEEP))
    result = await agent.run(question, session_id=session)

    pause = 0
    while result.interrupt is not None:
        pause += 1
        go_deeper = approvals_left > 0
        info = result.interrupt
        print(
            f"  ⏸ paused after round {info.get('round')} "
            f"({info.get('queries_run')} queries run) — go deeper? "
            f"{'YES (approving)' if go_deeper else 'NO (wrapping up)'}"
        )
        approvals_left -= 1 if go_deeper else 0
        ctx = _resume_ctx(session, run_id=f"resume-{pause}")
        result = await agent.engine.resume(
            agent.compiled, result.resume_token, ctx, resume_value={"go_deeper": go_deeper}
        )

    print(f"\nREPORT:\n  {str(result.output).strip()}\n")


async def main() -> int:
    """Classify the CLI question with the coordinator, then run the quick or deep agent."""
    if not os.environ.get("OPENAI_API_KEY"):
        print("This is live — set OPENAI_API_KEY in .env (or the shell) to run it.")
        return 1

    question = " ".join(sys.argv[1:]) or "Compare small modular reactor vendors in 2026"
    os.chdir(REPO_ROOT)  # the code: path in deep_research.yaml is repo-root-relative

    print(f"\nTASK: {question}\n")
    coordinator = build_agent(str(COORDINATOR))
    decision = await coordinator.run(question, session_id="coord-classify")
    route = normalize_label(str(decision.output))
    print(f"coordinator says: {str(decision.output).strip()!r} → {route}\n")

    if route == "deep":
        await _run_deep(question)
    else:
        await _run_quick(question)
    return 0


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
