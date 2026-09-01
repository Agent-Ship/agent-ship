"""Call a running AgentShip service from Python — the client the demo's tests drive.

This is the "how do I talk to the service" example: list agents, take a turn (streamed or
not), and continue a run that paused for a human. It speaks only the public ``/v1`` surface
over HTTP, exactly as any client would, so nothing here depends on the framework being
importable in the same process.

The browser UI is **AgentShip Studio**, served by the service itself at ``/studio`` — run
``make ui`` to open it. This module is the programmatic equivalent, and the thing the demo's
service tests exercise.

    from service_client import fetch_agent_names, invoke_turn, resume_turn, stream_frames

Point it at another deployment with ``AGENTSHIP_BASE_URL`` / ``AGENTSHIP_API_KEY``.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx

#: Defaults match ``docker-compose.yml``: the demo service listens on 7005 and ships a ``dev``
#: API key with every scope. Both are read per call so the environment can change without a
#: restart (and so tests can point the UI at a stub).
DEFAULT_BASE_URL = "http://localhost:7005"
DEFAULT_API_KEY = "dev"

#: No read timeout — a research agent can think for minutes — but fail fast when nothing is
#: listening, so "the service is down" surfaces in seconds rather than hanging the browser.
TIMEOUT = httpx.Timeout(None, connect=5.0)

#: Words that resume a pause as approve / decline. Anything else is passed to the paused
#: ``interrupt()`` verbatim, so this one UI can also drive free-form (non yes/no) pauses.
APPROVE = {"yes", "y", "approve", "ok", "okay", "go", "continue", "sure", "do it"}
DECLINE = {"no", "n", "stop", "reject", "decline", "cancel", "don't", "dont"}


class ServiceError(Exception):
    """A ``/v1`` call that failed, carrying a message already worded for the chat transcript.

    One exception type for both "nothing is listening" and "the service said no", because the
    UI does the same thing with either: show it to the human and end the turn.
    """


def base_url() -> str:
    """The service root to call, from ``AGENTSHIP_BASE_URL`` (default: the compose port 7005)."""
    return (os.environ.get("AGENTSHIP_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def api_key() -> str:
    """The key sent with every request, from ``AGENTSHIP_API_KEY`` (default: the ``dev`` key)."""
    return os.environ.get("AGENTSHIP_API_KEY") or DEFAULT_API_KEY


def request_headers() -> dict[str, str]:
    """Auth + content headers for a ``/v1`` call (the service also accepts ``X-API-Key``)."""
    return {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}


def unreachable_message(exc: Exception) -> str:
    """Word a connection failure so the human knows the service, not the UI, is the problem."""
    return (
        f"⚠️ Cannot reach the service at {base_url()} — is `make docker-up` running?\n\n"
        f"({type(exc).__name__}: {exc})"
    )


def problem_message(status: int, body: str) -> str:
    """Word an error response, preferring the service's ``problem+json`` ``detail`` and ``code``.

    Showing the envelope verbatim is the point: a 401 with ``code: invalid_api_key`` is the
    service's real behaviour, and hiding it behind a generic message would hide the feature.
    """
    try:
        problem = json.loads(body)
    except ValueError:
        problem = {}
    if not isinstance(problem, dict) or "title" not in problem:
        return f"⚠️ HTTP {status} from the service:\n\n```\n{body[:2000]}\n```"
    lines = [f"⚠️ **{problem.get('title')}** (HTTP {problem.get('status', status)})"]
    if problem.get("detail"):
        lines.append(str(problem["detail"]))
    ids = [f"`{key}: {problem[key]}`" for key in ("code", "trace_id") if problem.get(key)]
    if ids:
        lines.append(" · ".join(ids))
    return "\n\n".join(lines)


def raise_for_problem(response: httpx.Response, body: str) -> None:
    """Turn a non-2xx response into a :class:`ServiceError` carrying its problem+json text."""
    if response.status_code >= 400:
        raise ServiceError(problem_message(response.status_code, body))


def fetch_agent_names() -> tuple[list[str], str | None]:
    """Ask ``GET /v1/agents`` which agents the service serves.

    Returns ``(names, error)`` rather than raising because this runs while the UI is being
    assembled: a dead service must produce an empty picker and a message in the chat, never a
    crash and never a hardcoded fallback list.
    """
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            response = client.get(f"{base_url()}/v1/agents", headers=request_headers())
            body = response.text
    except httpx.RequestError as exc:
        return [], unreachable_message(exc)
    if response.status_code >= 400:
        return [], problem_message(response.status_code, body)
    try:
        cards = json.loads(body)
        return [card["name"] for card in cards], None
    except (ValueError, KeyError, TypeError) as exc:
        return [], f"⚠️ {base_url()}/v1/agents returned something unexpected: {exc}"


async def post_json(path: str, body: dict) -> dict:
    """POST a JSON body to ``path`` on the service and return the decoded reply.

    Shared by ``:invoke`` and ``:resume`` — both take JSON in and return an ``InvokeResponse``.
    """
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                f"{base_url()}{path}", headers=request_headers(), json=body
            )
            text = response.text
    except httpx.RequestError as exc:
        raise ServiceError(unreachable_message(exc)) from exc
    raise_for_problem(response, text)
    return json.loads(text)


async def invoke_turn(agent: str, text: str, session_id: str) -> dict:
    """Run one non-streaming turn: ``POST /v1/agents/{agent}:invoke``.

    This is also the only endpoint that reports a human-in-the-loop pause, because ``:stream``
    has no frame for a ``resume_token`` — see :data:`PAUSE_NOTE`.
    """
    return await post_json(
        f"/v1/agents/{agent}:invoke", {"input": text, "session_id": session_id}
    )


async def resume_turn(agent: str, resume_token: dict, session_id: str, value: Any) -> dict:
    """Continue a paused run: ``POST /v1/agents/{agent}:resume`` with the token and the decision.

    ``session_id`` is required here (unlike ``:invoke``) because a resume replays one specific
    checkpoint thread — sending a fresh id would silently resume nothing.
    """
    return await post_json(
        f"/v1/agents/{agent}:resume",
        {"resume_token": resume_token, "session_id": session_id, "resume_value": value},
    )


async def stream_frames(agent: str, text: str, session_id: str):
    """Yield each SSE frame of ``POST /v1/agents/{agent}:stream`` as a ``{type, seq, data}`` dict.

    Only the ``data:`` lines are decoded; the ``event:`` line repeats the type already inside the
    JSON payload, and sse-starlette's keepalive comments carry nothing.
    """
    body = {"input": text, "session_id": session_id}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            async with client.stream(
                "POST", f"{base_url()}/v1/agents/{agent}:stream",
                headers=request_headers(), json=body,
            ) as response:
                if response.status_code >= 400:
                    raise_for_problem(response, (await response.aread()).decode())
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        yield json.loads(line[len("data:"):].strip())
    except httpx.RequestError as exc:
        raise ServiceError(unreachable_message(exc)) from exc


def resume_value_from_reply(reply: str) -> Any:
    """Turn a human's chat reply into the value the paused ``interrupt()`` should receive.

    A yes/no word becomes ``{"approved": bool}`` — the shape a confirm-before-write pause
    expects — and anything else is passed through verbatim, so this UI drives free-form pauses
    too. The mapping is keyed off the reply rather than the pause's payload because the wire's
    ``InvokeResponse`` does not carry the payload (see :data:`PAUSE_NOTE`).
    """
    text = reply.strip().lower()
    if text in APPROVE:
        return {"approved": True}
    if text in DECLINE:
        return {"approved": False}
    return reply.strip()


#: Why the pause is rendered generically. ``InvokeResponse`` carries ``resume_token`` but not the
#: ``interrupt`` payload the engine produced, so the UI knows a run paused but not what it asked.
PAUSE_NOTE = (
    "The service's `InvokeResponse` returns the `resume_token` but not the pause's payload, "
    "so this UI can tell you *that* the run paused, not what it wants to do."
)


def is_paused(reply: dict) -> bool:
    """True when an ``InvokeResponse`` describes a run that stopped for a human.

    Reads the service's ``paused`` field rather than re-deriving it. Every durable run
    carries a ``resume_token`` — it is the crash-resume handle — so the token alone is not
    the signal, and each client inferring that rule independently is how Studio came to
    announce a pause on a turn that had answered.
    """
    return bool(reply.get("paused"))


def pause_message() -> str:
    """Render a pause as an assistant message inviting the reply that will resume the run."""
    return (
        "⏸️ **The run paused for you.** Reply **yes** to approve or **no** to reject "
        "(anything else is passed to the run verbatim).\n\n"
        f"_{PAUSE_NOTE}_"
    )


def new_state() -> dict:
    """Fresh per-conversation state: the chosen agent, its session, and any pending pause."""
    return {"agent": None, "session_id": None, "resume_token": None}


def trace_text(lines: list[str]) -> str:
    """Join the turn's wire notes for the Trace panel, or say plainly that nothing arrived."""
    return "\n".join(lines) if lines else "(nothing on the wire yet)"


def note_reply(lines: list[str], reply: dict) -> None:
    """Append the ids an ``InvokeResponse`` carried to the turn's trace notes."""
    lines.append(f"session_id : {reply.get('session_id')}")
    lines.append(f"trace_id   : {reply.get('trace_id')}")
    lines.append(f"resume_token: {json.dumps(reply.get('resume_token'))}")


async def respond(message: str, history: list, agent: str, live_tokens: bool, state: dict):
    """Handle one chat turn against the service, yielding ``(history, box, state, trace)``.

    An async generator so streamed tokens reach the browser as they arrive. Three paths: a
    pending ``resume_token`` sends the reply to ``:resume``; otherwise the turn goes to
    ``:stream`` (live tokens) or ``:invoke``. The ``session_id`` is kept for the whole
    conversation so durable agents remember, and reminted when the agent changes.
    """
    history = history + [{"role": "user", "content": message}]
    if not agent:
        history.append({"role": "assistant", "content": "⚠️ No agent selected — is the service up?"})
        yield history, "", state, trace_text([])
        return

    if state.get("agent") != agent or not state.get("session_id"):
        state["agent"] = agent
        state["session_id"] = uuid.uuid4().hex
        state["resume_token"] = None
    session_id = state["session_id"]
    lines = [f"POST {base_url()}/v1/agents/{agent}"]

    try:
        if state.get("resume_token") is not None:
            lines[0] += ":resume"
            reply = await resume_turn(
                agent, state["resume_token"], session_id, resume_value_from_reply(message)
            )
        elif live_tokens:
            lines[0] += ":stream"
            history.append({"role": "assistant", "content": ""})
            async for frame in stream_frames(agent, message, session_id):
                lines.append(f"seq {frame.get('seq'):>3}  {frame.get('type')}")
                text = frame.get("data", {}).get("content")
                if text:
                    history[-1]["content"] += str(text)
                if frame.get("type") == "error":
                    history[-1]["content"] += f"\n\n⚠️ {frame.get('data', {}).get('detail')}"
                yield history, "", state, trace_text(lines)
            lines.append(f"session_id : {session_id}")
            yield history, "", state, trace_text(lines)
            return
        else:
            lines[0] += ":invoke"
            reply = await invoke_turn(agent, message, session_id)
    except ServiceError as exc:
        state["resume_token"] = None
        history.append({"role": "assistant", "content": str(exc)})
        yield history, "", state, trace_text(lines)
        return

    note_reply(lines, reply)
    if is_paused(reply):
        state["resume_token"] = reply["resume_token"]
        history.append({"role": "assistant", "content": pause_message()})
    else:
        state["resume_token"] = None
        history.append({"role": "assistant", "content": str(reply.get("output", "")).strip()})
    yield history, "", state, trace_text(lines)


def start_new_chat(agent: str):
    """Clear the transcript, session and trace so the next turn opens a fresh conversation."""
    return [], new_state(), trace_text([])


def reload_agents():
    """Re-ask the service for its catalog, so a UI opened before the service came up recovers."""
    names, error = fetch_agent_names()
    chat = [{"role": "assistant", "content": error}] if error else []
    return gr.Dropdown(choices=names, value=names[0] if names else None), chat, new_state()
