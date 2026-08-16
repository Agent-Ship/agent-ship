"""A generic chat UI for any AgentShip agent — pick a YAML, send a message, get a reply.

This is the replacement for hand-written per-demo driver scripts. It is one small Gradio chat
app that can drive *any* agent in ``agents/``:

* a fast agent (``quick-search``) answers in a single turn;
* the durable ``deep-research`` agent runs a few rounds, then **pauses** to ask "go deeper?" —
  the pause shows up as a message in the chat, and your next reply ("yes" / "no") resumes the
  paused run from its checkpoint straight back into the conversation.

So the interaction is exactly what a user expects — *send a request, the agent works (deeply if
it needs to), and it answers* — with no bespoke script per demo. The pause→resume plumbing that
the old ``coordinated_research.py`` did by hand lives here once, generically, driven by the same
public API (``agent.run`` / ``agent.engine.resume``) any caller would use.

Run it (needs a real ``OPENAI_API_KEY``; ``BRAVE_API_KEY`` optional for real web results)::

    set -a; source ../agentship/.env; set +a
    make ui                       # or: python demos/chat_ui.py

Then open the printed ``http://127.0.0.1:7860`` in a browser.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import gradio as gr
import litellm
from agentship import build_agent
from agentship.context import Caller, RunContext, RunMode

# The deep-research spec references its graph by a repo-root-relative ``code:`` path, so resolve
# the working directory to the demo root before any agent is built (mirrors the CLI's behaviour).
REPO_ROOT = Path(__file__).resolve().parents[1]
os.chdir(REPO_ROOT)

# Match the demo scripts' LiteLLM setup so model calls behave identically here.
litellm.disable_aiohttp_transport = True
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

#: The agents offered in the picker, newest/flagship first. Label → YAML path (repo-relative).
AGENTS: dict[str, str] = {
    "deep-research (durable, pauses to ask 'go deeper?')": "agents/deep_research.yaml",
    "quick-search (single-turn web search)": "agents/quick_search.yaml",
    "assistant (plain single agent)": "agents/assistant.yaml",
}

#: Words that resume a "go deeper?" style pause as approve / decline. Anything else is passed to
#: the agent verbatim as the resume value, so this UI also drives non-boolean HITL agents.
_APPROVE = {"yes", "y", "go deeper", "deeper", "approve", "continue", "more", "keep going"}
_DECLINE = {"no", "n", "stop", "done", "enough", "decline", "that's enough"}


def _resume_value(reply: str, payload: dict) -> object:
    """Turn a human's chat reply into the value the paused ``interrupt()`` should receive.

    The deep-research agent's pause expects ``{"go_deeper": bool}``; when the pending interrupt
    looks like that (its payload mentions ``go_deeper`` or a "deeper" question) a yes/no reply is
    mapped to that shape. For any other HITL agent the raw reply text is passed straight through,
    so this one UI can resume arbitrary ``interrupt()`` payloads without knowing the agent.
    """
    text = reply.strip().lower()
    question = str(payload.get("question", "")).lower()
    looks_boolean = "go_deeper" in payload or "deeper" in question
    if looks_boolean:
        return {"go_deeper": text in _APPROVE and text not in _DECLINE}
    return reply.strip()


def _pause_message(payload: dict) -> str:
    """Render a pending ``interrupt()`` payload as an assistant chat message inviting a reply."""
    question = payload.get("question", "The agent paused and needs your input.")
    context_bits = [f"round {payload['round']}"] if "round" in payload else []
    if "queries_run" in payload:
        context_bits.append(f"{payload['queries_run']} searches so far")
    suffix = f"  _({', '.join(context_bits)})_" if context_bits else ""
    reply_hint = "Reply **yes** to go deeper or **no** to get the report now."
    return f"⏸️ **{question}**{suffix}\n\n{reply_hint}"


def _resume_ctx(agent, session_id: str) -> RunContext:
    """A RunContext that resumes the paused run on its own checkpoint thread (``session_id``)."""
    return RunContext(
        caller=Caller(user_id="ui"),
        session_id=session_id,
        run_id=uuid.uuid4().hex,
        agent_name=agent.spec.name,
        mode=RunMode.INVOKE,
    )


async def respond(message: str, history: list, agent_label: str, state: dict):
    """Handle one chat turn: either start a fresh run or resume a paused one, then reply.

    ``state`` carries the built agent, its conversation ``session_id``, and any pending resume
    token/payload between turns. When a token is pending, this turn's ``message`` resumes the
    paused run (mapping the reply to a resume value); otherwise it starts a fresh run on a new
    thread. Either outcome is appended to ``history`` as assistant messages — including the pause
    prompt, so the human simply keeps chatting to drive the agent deeper.
    """
    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        warning = "⚠️ Set OPENAI_API_KEY (source ../agentship/.env) and reload."
        history = history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": warning},
        ]
        return history, "", state

    history = history + [{"role": "user", "content": message}]

    # Resume a paused run: this reply is the human's decision, fed back into interrupt().
    if state.get("token") is not None:
        agent = state["agent"]
        ctx = _resume_ctx(agent, state["session_id"])
        decision = _resume_value(message, state["payload"])
        result = await agent.engine.resume(
            agent.compiled, state["token"], ctx, resume_value=decision
        )
    else:
        # Fresh turn: (re)build the selected agent on a new checkpoint thread and run it.
        if state.get("agent") is None or state.get("label") != agent_label:
            state["agent"] = build_agent(AGENTS[agent_label])
            state["label"] = agent_label
        state["session_id"] = uuid.uuid4().hex
        agent = state["agent"]
        result = await agent.run(message, session_id=state["session_id"])

    # A pending interrupt means the agent paused; show the question and keep the token for the
    # next reply. Otherwise the run finished — clear any token and show the answer.
    if result.interrupt is not None:
        state["token"] = result.resume_token
        state["payload"] = result.interrupt
        history.append({"role": "assistant", "content": _pause_message(result.interrupt)})
    else:
        state["token"] = None
        state["payload"] = None
        history.append({"role": "assistant", "content": str(result.output).strip()})
    return history, "", state


def build_ui() -> gr.Blocks:
    """Assemble the Gradio chat: an agent picker, the transcript, and one input box."""
    with gr.Blocks(title="AgentShip chat") as ui:
        gr.Markdown(
            "# AgentShip chat\n"
            "Pick an agent and send a request. The **deep-research** agent works for several "
            "rounds and then pauses to ask *“go deeper?”* — just reply **yes** or **no** to "
            "drive it. No scripts."
        )
        state = gr.State(
            {"agent": None, "label": None, "session_id": None, "token": None, "payload": None}
        )
        agent_label = gr.Dropdown(
            choices=list(AGENTS), value=next(iter(AGENTS)), label="Agent"
        )
        chatbot = gr.Chatbot(type="messages", height=460, label="Conversation")
        box = gr.Textbox(
            placeholder="Ask something, or reply yes/no when the agent pauses…",
            label="Message",
        )
        box.submit(respond, [box, chatbot, agent_label, state], [chatbot, box, state])
    return ui


if __name__ == "__main__":
    build_ui().launch()
