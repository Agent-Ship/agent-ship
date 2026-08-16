"""A generic chat + debug UI for AgentShip — pick ANY agent, send input, watch it work.

This is the one interactive front door for the whole demo. It replaces per-demo driver scripts
with a single Gradio app that can drive *any* agent in ``agents/``:

* simple agents (``assistant``, ``calculator``, ``streaming``) answer in one turn;
* the multi-agent supervisors (``triage``, ``triage panel``) classify, route, and fan out to
  sub-agents — and the **Trace** panel shows that decision path (classify → route → dispatch →
  resolve) live, so the routing is visible, not hidden behind a single chat bubble;
* the durable ``deep-research`` agent (and the ``hitl`` agent) **pause** for a human — the pause
  shows up as a chat message and your next reply resumes the checkpointed run in-conversation.

So the interaction is exactly what you'd expect — *pick an agent, send input, see it work* — and
the pause→resume plumbing lives here once, generically, on the same public ``run``/``resume`` API
any caller would use. The Trace panel is just AgentShip's own ``agentship.*`` INFO logs captured
for the turn, so it works for every agent without the UI knowing any agent's internals.

Run it (needs a real ``OPENAI_API_KEY``; ``BRAVE_API_KEY`` optional for real web results)::

    set -a; source ../agentship/.env; set +a
    make ui                       # or: python demos/chat_ui.py

Then open the printed ``http://127.0.0.1:7860`` in a browser.
"""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path

import gradio as gr
import litellm
from agentship import build_agent
from agentship.context import Caller, RunContext, RunMode

# The deep-research spec (and the triage supervisor) reference other files by repo-root-relative
# paths, so resolve the working directory to the demo root before any agent is built (mirrors the
# CLI's behaviour).
REPO_ROOT = Path(__file__).resolve().parents[1]
os.chdir(REPO_ROOT)

# Match the demo scripts' LiteLLM setup so model calls behave identically here.
litellm.disable_aiohttp_transport = True
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

#: Every standalone-runnable agent, label → YAML path (repo-relative). The flagship deep-research
#: agent is first; the multi-agent supervisors show their routing in the Trace panel. Two agents
#: are omitted because they need setup the generic UI can't do: ``mcp`` (needs an external MCP
#: server) and ``hitl`` (needs its ``save_note`` write tool registered — see test_hitl_write.py).
AGENTS: dict[str, str] = {
    "deep-research — durable, pauses to ask 'go deeper?'": "agents/deep_research.yaml",
    "quick-search — single-turn web search": "agents/quick_search.yaml",
    "triage — multi-agent supervisor (classify → route → dispatch)": "agents/triage/triage.yaml",
    "triage panel — parallel fan-out to specialists": "agents/triage/panel.yaml",
    "coordinator — classifier that labels a request quick or deep": "agents/coordinator.yaml",
    "assistant — plain single agent": "agents/assistant.yaml",
    "calculator — single agent with a tool": "agents/calculator.yaml",
    "streaming — single agent (streaming-capable)": "agents/streaming.yaml",
    "graph — supervisor scaffold": "agents/graph.yaml",
    "custom — native build_graph agent": "agents/custom/custom.yaml",
    "autonomous — deepagents-style planner": "agents/autonomous.yaml",
}

#: Words that resume a "go deeper?" style pause as approve / decline. Anything else is passed to
#: the agent verbatim as the resume value, so this UI also drives non-boolean HITL agents.
_APPROVE = {"yes", "y", "go deeper", "deeper", "approve", "continue", "more", "keep going"}
_DECLINE = {"no", "n", "stop", "done", "enough", "decline", "that's enough"}


def _new_state() -> dict:
    """A fresh per-conversation state: the built agent, its thread, and any pending pause."""
    return {"agent": None, "label": None, "session_id": None, "token": None, "payload": None}


class _TraceCollector(logging.Handler):
    """A logging handler that buffers AgentShip's ``agentship.*`` INFO logs for one turn.

    Attaching this to the ``agentship`` logger for the duration of a run captures the decision
    trace every agent already emits — the supervisor's classify/route/dispatch/resolve lines, tool
    calls, and the deep-research rounds — so the Trace panel is generic and needs no per-agent
    wiring. Each record is rendered as ``<logger tail>: <message>`` (e.g. ``supervisor: dispatch
    …``).
    """

    def __init__(self) -> None:
        """Buffer at INFO level; the lines are drained into the Trace panel after the turn."""
        super().__init__(level=logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Append one formatted log line (never raising — logging must not break the turn)."""
        try:
            name = record.name
            if name.startswith("agentship."):
                name = name[len("agentship."):]
            self.lines.append(f"{name}: {record.getMessage()}")
        except Exception:  # noqa: BLE001  # defensive: a bad log record must never crash a turn
            self.lines.append("(unformattable log record)")


def _capture_trace() -> _TraceCollector:
    """Install a fresh trace collector on the ``agentship`` logger and ensure INFO is emitted.

    Returns the collector; the caller must pass it to :func:`_release_trace` in a ``finally`` so the
    handler is always removed and the logger's level restored, even if the run raises.
    """
    collector = _TraceCollector()
    root = logging.getLogger("agentship")
    collector._prev_level = root.level  # type: ignore[attr-defined]  # stash for restore
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    root.addHandler(collector)
    return collector


def _release_trace(collector: _TraceCollector) -> str:
    """Remove the collector, restore the logger level, and return the captured trace text."""
    root = logging.getLogger("agentship")
    root.removeHandler(collector)
    root.setLevel(collector._prev_level)  # type: ignore[attr-defined]
    return "\n".join(collector.lines) if collector.lines else "(no trace emitted this turn)"


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
    reply_hint = "Reply **yes** to go deeper / approve, or **no** to stop and get the result."
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
    """Handle one chat turn; return ``(history, cleared_box, state, trace)`` for the UI outputs.

    Either starts a fresh run or resumes a paused one, capturing AgentShip's INFO logs for the
    turn into the Trace panel. ``state`` carries the built agent, its conversation ``session_id``,
    and any pending resume token/payload between turns: when a token is pending, this turn's
    ``message`` resumes the paused run; otherwise it (re)builds the selected agent on a new thread
    and runs it. Build/run errors are shown in the chat rather than crashing the app, so selecting
    an agent that needs external setup fails gracefully.
    """
    if not (os.environ.get("OPENAI_API_KEY") or "").strip():
        warning = "⚠️ Set OPENAI_API_KEY (source ../agentship/.env) and reload."
        history = history + [
            {"role": "user", "content": message},
            {"role": "assistant", "content": warning},
        ]
        return history, "", state, "(no API key set)"

    history = history + [{"role": "user", "content": message}]
    collector = _capture_trace()
    try:
        if state.get("token") is not None:
            # Resume a paused run: this reply is the human's decision, fed back into interrupt().
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
    except Exception as exc:  # noqa: BLE001  # a UI must show any agent failure, not crash
        # Never crash the app — surface the failure in the chat and clear any stale pause.
        state["token"] = None
        state["payload"] = None
        history.append({"role": "assistant", "content": f"⚠️ {type(exc).__name__}: {exc}"})
        return history, "", state, _release_trace(collector)

    trace = _release_trace(collector)
    # A pending interrupt means the agent paused; keep the token for the next reply. Otherwise the
    # run finished — clear any token and show the answer.
    if result.interrupt is not None:
        state["token"] = result.resume_token
        state["payload"] = result.interrupt
        history.append({"role": "assistant", "content": _pause_message(result.interrupt)})
    else:
        state["token"] = None
        state["payload"] = None
        history.append({"role": "assistant", "content": str(result.output).strip()})
    return history, "", state, trace


def _reset(agent_label: str):
    """Start a brand-new conversation with the selected agent (clears chat, state, and trace)."""
    return [], _new_state(), "(trace appears here after you send a message)"


def build_ui() -> gr.Blocks:
    """Assemble the chat + debug UI: agent picker, transcript, input box, and a trace panel."""
    with gr.Blocks(title="AgentShip chat") as ui:
        gr.Markdown(
            "# AgentShip — chat & debug\n"
            "Pick an agent, send input, and watch it work. Multi-agent supervisors show their "
            "**classify → route → dispatch → resolve** path in the Trace panel; the "
            "**deep-research** and **hitl** agents pause for you — just reply **yes**/**no** to "
            "resume. No scripts."
        )
        state = gr.State(_new_state())
        with gr.Row():
            agent_label = gr.Dropdown(
                choices=list(AGENTS), value=next(iter(AGENTS)), label="Agent", scale=4
            )
            reset_btn = gr.Button("New chat", scale=1)
        chatbot = gr.Chatbot(type="messages", height=460, label="Conversation")
        box = gr.Textbox(
            placeholder="Send input, or reply yes/no when the agent pauses…",
            label="Message",
        )
        with gr.Accordion("Trace (AgentShip's decision log for the last turn)", open=False):
            trace = gr.Code(value="(trace appears here after you send a message)", label="")

        box.submit(respond, [box, chatbot, agent_label, state], [chatbot, box, state, trace])
        # Switching agents or clicking "New chat" starts a fresh conversation on that agent.
        reset_btn.click(_reset, [agent_label], [chatbot, state, trace])
        agent_label.change(_reset, [agent_label], [chatbot, state, trace])
    return ui


if __name__ == "__main__":
    build_ui().launch()
