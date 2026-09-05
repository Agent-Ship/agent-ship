"""The frozen tracing contract: stable span names and attribute keys (Phase 07 DoD).

Every span name and attribute key AgentShip emits is a **public, versioned contract** — P12 (evals)
and P13 (audit) read a trace by these exact strings, so they cannot drift silently. The constants
live in ``agentship-core`` (not the exporter package) so an engine hook can import them even when
``agentship-observability`` is not installed (design §4.6). The human-readable golden copy of this
contract is ``SEMCONV.md``; these constants and that file must stay in lock-step.

Grouping:

- ``SPAN_*`` — the canonical span-tree names (§4.1). ``NODE_PREFIX``/``TOOL_PREFIX`` are prefixes; a
  concrete span is ``node.<node_name>`` / ``tool.<tool_name>``.
- ``GEN_AI_*`` — OpenTelemetry GenAI semantic-convention attribute keys (§4.2). We **adopt**
  these standard keys, we do not invent them; they are restated here as plain strings only
  because the vendor-free kernel cannot import ``opentelemetry.semconv``.
- ``OI_*`` — the OpenInference mirror keys Phoenix's cost panel reads (adopted, not invented).
- ``AS_*`` — AgentShip-owned attribute keys (cost, latency, tenant, replay hash, …).

The ``GEN_AI_*`` / ``OI_*`` values are held to their upstream standards by a drift guard,
``agentship-observability/tests/test_semconv_upstream.py``: it imports the real OpenTelemetry
GenAI and OpenInference constants and fails CI if any value here diverges. Upstream is the
single source of truth; this file only restates it where OTel is not on the import path.
"""

from __future__ import annotations

#: The frozen contract version. Bump only with a documented change to SEMCONV.md; P12/P13 pin it.
SEMCONV_VERSION = "0.1.0"

# --- Span names (§4.1) -------------------------------------------------------------------------
SPAN_AGENT = "agent"  # the root span's PREFIX; see agent_span() for the name actually emitted


def agent_span(agent_name: str) -> str:
    """The root span's name: ``agent <name>``, e.g. ``agent research-team``.

    Named after the agent because the span name is what a trace UI lists. Every root span used
    to be called just "agent", so a supervisor and its three specialists appeared as four
    identical rows in Opik and the one detail telling them apart lived in an attribute the UI
    does not surface. The ``agent `` prefix keeps them greppable and sorted together, and
    matches OTel's GenAI convention of naming an agent span after its agent.
    """
    return f"{SPAN_AGENT} {agent_name}" if agent_name else SPAN_AGENT


SPAN_MODEL = "model"  # the model span's PREFIX; see model_span() for the name actually emitted


#: What a model span's name starts with once it carries the model id.
MODEL_SPAN_PREFIX = "chat "


def is_model_span(name: str) -> bool:
    """Whether ``name`` is a model span, under either form it can take.

    A model span is normally ``chat <model>``, but falls back to the bare ``model`` when the
    model id is unknown — which a fake or offline model produces. Both are model spans, so
    every place that looks for them goes through here rather than repeating the two-way check
    and getting it half right.
    """
    return name == SPAN_MODEL or name.startswith(MODEL_SPAN_PREFIX)


def model_span(model_id: str | None) -> str:
    """A model span's name: ``chat <model>``, e.g. ``chat openai/gpt-4o-mini``.

    Every LLM call used to produce a span called just "model", so a supervisor's trace showed
    several identical rows with no way to see which ran on the cheap classifier and which on
    the expensive specialist without opening each one. OTel's GenAI convention names a model
    span ``{operation} {model}`` for exactly this. Falls back to the bare name when the model
    is unknown, rather than emitting a dangling "chat ".
    """
    return f"{MODEL_SPAN_PREFIX}{model_id}" if model_id else SPAN_MODEL


SPAN_GUARDRAIL_INPUT = "guardrail.input"
SPAN_GUARDRAIL_OUTPUT = "guardrail.output"
SPAN_MEMORY_RECALL = "memory.recall"
SPAN_MEMORY_WRITE = "memory.write"
SPAN_OUTPUT_VALIDATE = "output.validate"
#: ``node.<name>`` — a LangGraph node / ADK step (repeats per node).
NODE_PREFIX = "node."
#: ``tool.<name>`` — one span per tool invocation.
TOOL_PREFIX = "tool."


def node_span(name: str) -> str:
    """Return the canonical span name for a graph node, e.g. ``node.classify``."""
    return f"{NODE_PREFIX}{name}"


def tool_span(name: str) -> str:
    """Return the canonical span name for a tool invocation, e.g. ``tool.search``."""
    return f"{TOOL_PREFIX}{name}"


# --- GenAI semantic-convention attribute keys (§4.2) ------------------------------------------
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"  # "chat" on model spans, "execute_tool" on tools
GEN_AI_SYSTEM = "gen_ai.system"  # provider, e.g. "openai"/"anthropic"/"vertex_ai"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
#: Of the output tokens, how many the model spent thinking. A subset of the output count,
#: NOT an addition to it — providers bill reasoning as output, and adding it again would
#: double-count the spend. Recorded whether or not content capture is on: it is a number,
#: it leaks nothing, and it is the only thing that explains why a reasoning model cost
#: several times a normal one for the same visible answer.
GEN_AI_USAGE_REASONING_TOKENS = "gen_ai.usage.reasoning_tokens"
#: The model's thinking text. Ours (``agentship.``) rather than a ``gen_ai.`` key because
#: OTel's GenAI conventions have not settled on one; when they do, this moves and the drift
#: guard will say so. Chain-of-thought is content, so it is gated by ``capture_content``.
AS_REASONING = "agentship.reasoning"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
#: Prompt/response content — emitted **only** when ``capture_content`` is true (PHI gate, §4.6).
GEN_AI_INPUT_MESSAGES = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"

# --- OpenInference mirror keys (Phoenix cost panel) -------------------------------------------
#: The OpenInference content pair. Opik and Phoenix render a span's input/output panels from
#: these, not from ``gen_ai.*.messages`` — so content captured only under the GenAI keys left
#: those panels blank. Mirrored for the same reason the token counts below are: adopted from
#: upstream, not invented. Both are gated by ``capture_content`` (the PHI gate).
OI_INPUT_VALUE = "input.value"
OI_OUTPUT_VALUE = "output.value"

OI_TOKEN_COUNT_PROMPT = "llm.token_count.prompt"
OI_TOKEN_COUNT_COMPLETION = "llm.token_count.completion"
OI_TOKEN_COUNT_TOTAL = "llm.token_count.total"

# --- AgentShip-owned attribute keys -----------------------------------------------------------
AS_COST_USD = "agentship.cost.usd"
AS_LATENCY_MS = "agentship.latency.ms"
AS_STATUS = "agentship.status"  # "ok" | "error" on the root agent span
AS_RUN_MODE = "agentship.run.mode"  # "invoke" | "stream" on the root agent span
AS_TENANT_ID = "agentship.tenant.id"
AS_SESSION_ID = "agentship.session.id"

#: The conversation this turn belongs to, under the bare key backends read. Opik groups traces
#: into threads by an attribute named exactly ``thread_id``; without it a ten-turn conversation
#: shows up as ten unrelated traces. Deliberately un-namespaced for that reason — our own
#: ``agentship.session.id`` carries the same value for anything querying our namespace.
THREAD_ID = "thread_id"
AS_RUN_ID = "agentship.run.id"
AS_AGENT_NAME = "agentship.agent.name"
#: Caller id on the root span — always the salted hash, never the raw id (PHI gate, §4.6).
AS_USER_ID = "agentship.user.id"
AS_TOOL_IDEMPOTENT = "agentship.tool.idempotent"
AS_TOOL_MCP_SERVER = "agentship.tool.mcp_server"
#: Record/replay cassette key: sha256 of the canonicalized request (§4.10). P12 keys replays here.
AS_REPLAY_REQUEST_HASH = "agentship.replay.request_hash"
