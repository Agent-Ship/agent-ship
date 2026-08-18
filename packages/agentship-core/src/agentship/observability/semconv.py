"""The frozen tracing contract: stable span names and attribute keys (Phase 07 DoD).

Every span name and attribute key AgentShip emits is a **public, versioned contract** — P12 (evals)
and P13 (audit) read a trace by these exact strings, so they cannot drift silently. The constants
live in ``agentship-core`` (not the exporter package) so an engine hook can import them even when
``agentship-observability`` is not installed (design §4.6). The human-readable golden copy of this
contract is ``SEMCONV.md``; these constants and that file must stay in lock-step.

Grouping:

- ``SPAN_*`` — the canonical span-tree names (§4.1). ``NODE_PREFIX``/``TOOL_PREFIX`` are prefixes; a
  concrete span is ``node.<node_name>`` / ``tool.<tool_name>``.
- ``GEN_AI_*`` — OpenTelemetry GenAI semantic-convention attribute keys (§4.2).
- ``OI_*`` — the OpenInference mirror keys Phoenix's cost panel reads.
- ``AS_*`` — AgentShip-owned attribute keys (cost, latency, tenant, replay hash, …).
"""

from __future__ import annotations

#: The frozen contract version. Bump only with a documented change to SEMCONV.md; P12/P13 pin it.
SEMCONV_VERSION = "0.1.0"

# --- Span names (§4.1) -------------------------------------------------------------------------
SPAN_AGENT = "agent"  # the root span, one per interaction
SPAN_MODEL = "model"  # one per LLM call; the LiteLLM callback stamps usage here
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
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"
#: Prompt/response content — emitted **only** when ``capture_content`` is true (PHI gate, §4.6).
GEN_AI_INPUT_MESSAGES = "gen_ai.input.messages"
GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"

# --- OpenInference mirror keys (Phoenix cost panel) -------------------------------------------
OI_TOKEN_COUNT_PROMPT = "llm.token_count.prompt"
OI_TOKEN_COUNT_COMPLETION = "llm.token_count.completion"
OI_TOKEN_COUNT_TOTAL = "llm.token_count.total"

# --- AgentShip-owned attribute keys -----------------------------------------------------------
AS_COST_USD = "agentship.cost.usd"
AS_LATENCY_MS = "agentship.latency.ms"
AS_STATUS = "agentship.status"  # "ok" | "error" on the root agent span
AS_TENANT_ID = "agentship.tenant.id"
AS_SESSION_ID = "agentship.session.id"
AS_RUN_ID = "agentship.run.id"
AS_AGENT_NAME = "agentship.agent.name"
AS_TOOL_IDEMPOTENT = "agentship.tool.idempotent"
AS_TOOL_MCP_SERVER = "agentship.tool.mcp_server"
#: Record/replay cassette key: sha256 of the canonicalized request (§4.10). P12 keys replays here.
AS_REPLAY_REQUEST_HASH = "agentship.replay.request_hash"
