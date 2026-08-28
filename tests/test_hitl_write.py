"""Live slice: HITL confirm-before-write — a side-effecting tool pauses for approval (Phase 03 C5).

The ``note-taker`` agent has a side-effecting ``save_note`` tool and ``confirm_writes: true``. Asking
it to save a note makes the model call the tool, which **pauses** (the turn returns a resume token,
nothing is written). Resuming with ``{"approved": true}`` fires the write exactly once. Live: it
calls OpenAI. Skips without a key.

Run it (with a key set)::

    set -a; source ../agentship/.env; set +a
    pytest tests/test_hitl_write.py -q
"""

from __future__ import annotations

import pytest

from pathlib import Path

from agentship import build_agent
from agentship.context import Caller, RunContext, RunMode
from agentship.tools import TOOLS, Tool
from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT = str(REPO_ROOT / "agents" / "hitl" / "agent.yaml")

_SAVED: list[str] = []


class _NoteArgs(BaseModel):
    text: str


# Register the demo's side-effecting write tool so `tools: [save_note]` resolves it.
TOOLS.register(
    "save_note",
    Tool(
        "save_note",
        "Save a note to the user's notebook.",
        lambda text: _SAVED.append(text) or f"saved: {text}",
        args_schema=_NoteArgs,
        side_effecting=True,
    ),
)


@pytest.mark.vcr
async def test_write_pauses_then_fires_only_after_approval():
    """The write pauses for approval (nothing saved), then fires once when approved."""
    _SAVED.clear()
    session = "demo-hitl-note"
    agent = build_agent(AGENT)
    result = await agent.run("Please save a note that says 'buy milk'.", session_id=session)

    # Paused for approval: a resume token + interrupt payload, and NOTHING written yet.
    assert result.interrupt is not None, "expected the write to pause for approval"
    assert result.resume_token is not None
    assert _SAVED == []

    ctx = RunContext(
        caller=Caller(user_id="anonymous"),
        session_id=session,
        run_id="r-approve",
        agent_name=agent.spec.name,
        mode=RunMode.INVOKE,
    )
    await agent.engine.resume(agent.compiled, result.resume_token, ctx, resume_value={"approved": True})

    assert _SAVED, "the write did not fire after approval"
    assert "milk" in _SAVED[0]
