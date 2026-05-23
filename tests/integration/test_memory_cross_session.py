"""End-to-end cross-session recall test.

The headline success criterion from design.md §2.3:

    "A demo agent recalls a fact stated in session A when asked about it
     in session B for the same user_id. Demonstrated in an integration
     test that does not mock the backend."

This test exercises the real personal_assistant_agent against a real
Mem0 Platform account. It's gated on the relevant env vars being set
so it stays opt-in for CI / dev machines without credentials.

Each run uses a unique `user_id` (UUID suffix) so concurrent runs don't
pollute each other's memories, and we tear down the test user's
memories after the assertion.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from src.agent_framework.memory.base import MemoryScope
from src.service.models.base_models import AgentChatRequest

from tests.integration.conftest import project_root_cwd


# ---------------------------------------------------------------------------
# Gating: skip when any required env var is missing.
# ---------------------------------------------------------------------------

REQUIRED_ENV_VARS = (
    "AGENT_LTM_MEM0_PLATFORM_API_KEY",   # backend
    "OPENAI_API_KEY",                    # LLM (personal_assistant_agent uses gpt-4o)
    "AGENT_SESSION_STORE_URI",           # ADK session store
)

requires_full_stack = pytest.mark.skipif(
    any(not os.getenv(var) for var in REQUIRED_ENV_VARS),
    reason=(
        "Cross-session integration test needs all of: "
        f"{', '.join(REQUIRED_ENV_VARS)}. Set them in .env and ensure "
        "postgres is reachable to run this test."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# How long to wait between session A's response and session B's query so
# Mem0 finishes its async extraction. Anecdotally 5–10s is plenty; we
# pad to 15 for CI flake-tolerance.
MEM0_EXTRACTION_WAIT_SECONDS = 15


async def _chat(agent, user_id: str, session_id: str, message: str) -> str:
    """Send one chat turn, return the assistant's reply text."""
    request = AgentChatRequest(
        agent_name="personal_assistant_agent",
        user_id=user_id,
        session_id=session_id,
        query=message,
    )
    response = await agent.chat(request)
    assert response.success, f"Agent chat failed: {response.agent_response}"
    # `agent_response` is the agent's output schema (PersonalAssistantOutput).
    # Pull out the answer text from it.
    reply = response.agent_response
    if hasattr(reply, "answer"):
        return reply.answer
    if hasattr(reply, "response"):
        return reply.response
    return str(reply)


async def _cleanup_memories(agent, user_id: str) -> None:
    """Best-effort: wipe the test user's memories so reruns start clean."""
    try:
        from src.agent_framework.factories import MemoryFactory
        backend = MemoryFactory.create(memory_config=agent.agent_config.memory)
        await backend.delete_scope(
            MemoryScope(user_id=user_id, agent_id="personal_assistant_agent")
        )
    except Exception:  # noqa: BLE001 — test-cleanup, don't fail the test on cleanup error
        pass


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


@requires_full_stack
@pytest.mark.integration
@pytest.mark.asyncio
async def test_cross_session_recall_adk_engine():
    """Session A says a fact; session B asks for it back; recall works.

    Uses the real personal_assistant_agent (ADK engine, gpt-4o, Mem0
    Platform). The agent in session B has no conversation history with
    the fact — so a correct answer can only come from memory recall.
    """
    with project_root_cwd():
        # Import here so the conftest's agent-cache reset has already run
        # by the time we instantiate.
        from src.all_agents.personal_assistant_agent.main_agent import PersonalAssistantAgent

        agent = PersonalAssistantAgent()

        # Unique user per run so we don't read other test runs' state.
        user_id = f"itest-{uuid.uuid4().hex[:8]}"
        session_a = f"sess-A-{uuid.uuid4().hex[:6]}"
        session_b = f"sess-B-{uuid.uuid4().hex[:6]}"

        try:
            # ---- Session A: deposit a fact -------------------------------
            await _chat(
                agent, user_id, session_a,
                "I live in Bangalore and my partner is Tanya.",
            )

            # Mem0 extracts asynchronously after the response goes back.
            # Wait for the extraction + indexing to settle.
            await asyncio.sleep(MEM0_EXTRACTION_WAIT_SECONDS)

            # ---- Session B: ask for the fact back ------------------------
            reply_b = await _chat(
                agent, user_id, session_b,
                "Where do I live and who is my partner?",
            )

            # The agent in session B never heard "Bangalore" or "Tanya" in
            # its conversation history (different session_id, no shared
            # state in the checkpointer). A correct answer can ONLY come
            # from cross-session recall via Mem0.
            reply_lower = reply_b.lower()
            assert "bangalore" in reply_lower, (
                f"Expected 'Bangalore' in session B reply but got: {reply_b!r}"
            )
            assert "tanya" in reply_lower, (
                f"Expected 'Tanya' in session B reply but got: {reply_b!r}"
            )
        finally:
            await _cleanup_memories(agent, user_id)
