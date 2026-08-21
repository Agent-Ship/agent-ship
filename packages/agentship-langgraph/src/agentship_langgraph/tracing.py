"""Translate LangChain/LangGraph callback events into AgentShip observer spans (Phase 07).

The runtime opens the root ``agent`` span; everything *inside* a turn — the graph's nodes, each
model call, every tool and MCP invocation — happens inside LangGraph/LangChain, where our kernel
never sees it. The one supported way to observe that work is LangChain's callback protocol (the
same seam LangSmith and OpenInference hook), so this handler subscribes to it and maps each event
onto the frozen semconv:

* ``on_chat_model_start`` / ``on_llm_start`` → open a ``model`` span (provider, model, temperature,
  max tokens, a replay hash, and — only when content capture is enabled — the redacted prompt).
* ``on_llm_end`` → stamp tokens (from ``usage_metadata``), cost (via LiteLLM's price table),
  finish reasons, latency and the response model, then close the span. **This is where tokens and
  cost finally land** — the gap that made P07 traces empty.
* ``on_tool_start`` / ``on_tool_end`` → a ``tool.<name>`` span (args/result redacted-gated, and the
  originating MCP server when the tool came from one).
* ``on_chain_start`` for a LangGraph node → a ``node.<name>`` span.

Spans are held by ``run_id`` and parented by ``parent_run_id``, so the model→tool tree is rebuilt
from the callback stream even though the events fire as unrelated calls; each span is opened via
:meth:`Observer.start_span` and closed on its matching end/error event. Everything is fail-open — a
tracing fault logs and is swallowed so a run never breaks because of observability (§4.7).
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID

from agentship.observability import (
    SpanKind,
    Usage,
    redact_pii,
    request_hash,
    semconv,
    usage_attributes,
)
from langchain_core.callbacks import AsyncCallbackHandler

if TYPE_CHECKING:
    from agentship.observability import Observer, Span
    from langchain_core.messages import BaseMessage
    from langchain_core.outputs import LLMResult

_log = logging.getLogger("agentship.observability")


def _provider_of(model_id: str) -> str:
    """Return the provider slug from a LiteLLM model id (``openai/gpt-4o-mini`` → ``openai``)."""
    return model_id.split("/", 1)[0] if "/" in model_id else ""


def _model_id(serialized: dict[str, Any] | None, kwargs: dict[str, Any]) -> str:
    """Resolve the requested model id from the callback's invocation params or serialized model."""
    params = kwargs.get("invocation_params") or {}
    model = params.get("model") or params.get("model_name")
    if not model and serialized:
        model = (serialized.get("kwargs") or {}).get("model")
    return str(model or "")


def _flatten_messages(messages: list[list[BaseMessage]]) -> list[dict[str, str]]:
    """Flatten LangChain's per-prompt message lists to ``[{role, content}]`` for hashing/capture."""
    flat: list[dict[str, str]] = []
    for prompt in messages:
        for message in prompt:
            flat.append({"role": message.type, "content": str(message.content)})
    return flat


def _json(value: Any) -> str:
    """Stable, compact JSON for a captured payload (non-JSON values coerced to ``str``)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def _cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float | None:
    """Price a call from LiteLLM's cost table; ``None`` when the model is unknown or unpriced."""
    if not model_id or not (input_tokens or output_tokens):
        return None
    try:
        from litellm import cost_per_token

        prompt_cost, completion_cost = cost_per_token(
            model=model_id, prompt_tokens=input_tokens, completion_tokens=output_tokens
        )
        return float(prompt_cost) + float(completion_cost)
    except Exception:  # noqa: BLE001 - a missing price must not break the trace
        return None


def _usage_from_result(response: LLMResult, model_id: str, latency_ms: float) -> Usage:
    """Build a vendor-neutral :class:`Usage` from a LangChain ``LLMResult``.

    Reads token counts from the message's ``usage_metadata`` (the modern chat-model field), falling
    back to ``llm_output['token_usage']`` for models that only report there; derives cost from the
    token counts via LiteLLM's price table. Missing counts default to zero rather than raising.
    """
    generations = response.generations or []
    first = generations[0][0] if generations and generations[0] else None
    message = getattr(first, "message", None)

    usage_metadata = getattr(message, "usage_metadata", None) or {}
    input_tokens = int(usage_metadata.get("input_tokens") or 0)
    output_tokens = int(usage_metadata.get("output_tokens") or 0)
    if not (input_tokens or output_tokens):
        token_usage = (response.llm_output or {}).get("token_usage") or (
            response.llm_output or {}
        ).get("usage") or {}
        input_tokens = int(token_usage.get("prompt_tokens") or 0)
        output_tokens = int(token_usage.get("completion_tokens") or 0)

    finish_reasons: list[str] = []
    info = getattr(first, "generation_info", None) or {}
    if reason := info.get("finish_reason"):
        finish_reasons = [str(reason)]

    response_model = (response.llm_output or {}).get("model_name") or None
    return Usage(
        model=model_id,
        provider=_provider_of(model_id),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=_cost_usd(model_id, input_tokens, output_tokens),
        latency_ms=latency_ms,
        finish_reasons=finish_reasons,
        response_model=str(response_model) if response_model else None,
    )


def _output_text(response: LLMResult) -> str:
    """Concatenate the assistant text from an ``LLMResult`` for content capture."""
    parts: list[str] = []
    for generation in response.generations or []:
        for item in generation:
            text = getattr(item, "text", None)
            if text:
                parts.append(str(text))
    return "".join(parts)


class ObservabilityCallback(AsyncCallbackHandler):
    """A LangChain callback that mirrors model/tool/node events into observer spans.

    One instance is created per run and added to the graph's ``callbacks`` next to the tool logger.
    It holds every open span by its ``run_id`` and parents new spans by ``parent_run_id``, so the
    tree it builds nests under whatever span is active when the run starts — the runtime's root
    ``agent`` span. ``capture_content`` mirrors the observer's PHI gate: prompts, tool args and
    results are captured only when it is on, and always redacted first.
    """

    #: Async callbacks run in the same task as the awaited ``ainvoke``/``astream``, so the ambient
    #: span (contextvar) is preserved and the first span nests under the root agent span.
    run_inline = True

    def __init__(
        self,
        observer: Observer,
        *,
        capture_content: bool = False,
        mcp_servers: dict[str, str] | None = None,
    ) -> None:
        """Bind the observer, the content gate, and an optional tool-name → MCP-server map."""
        self._observer = observer
        self._capture_content = capture_content
        self._mcp_servers = mcp_servers or {}
        self._spans: dict[UUID, Span] = {}
        self._starts: dict[UUID, float] = {}
        self._model_ids: dict[UUID, str] = {}

    # -- span bookkeeping ----------------------------------------------------------------------

    def _open(
        self,
        run_id: UUID,
        parent_run_id: UUID | None,
        name: str,
        kind: SpanKind,
        attrs: dict[str, Any],
    ) -> None:
        """Open a span for ``run_id`` under its parent span (or the ambient root when unmapped)."""
        try:
            parent = self._spans.get(parent_run_id) if parent_run_id else None
            self._spans[run_id] = self._observer.start_span(name, kind, attrs, parent=parent)
            self._starts[run_id] = time.monotonic()
        except Exception:  # noqa: BLE001 - tracing must never break the run
            _log.warning("observability.callback.open failed name=%s", name, exc_info=True)

    def _latency_ms(self, run_id: UUID) -> float:
        """Milliseconds since the span for ``run_id`` opened (0.0 if its start was not recorded)."""
        start = self._starts.get(run_id)
        return (time.monotonic() - start) * 1000.0 if start is not None else 0.0

    def _close(self, run_id: UUID) -> None:
        """End and forget the span for ``run_id``."""
        self._starts.pop(run_id, None)
        self._model_ids.pop(run_id, None)
        span = self._spans.pop(run_id, None)
        if span is not None:
            span.end()

    def _error_close(self, run_id: UUID, error: BaseException | Any) -> None:
        """Mark the ``run_id`` span errored (recording ``error`` if an exception) and close it."""
        span = self._spans.get(run_id)
        if span is not None:
            span.set_error(error if isinstance(error, BaseException) else None)
        self._close(run_id)

    # -- model spans ---------------------------------------------------------------------------

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any] | None,
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Open a ``model`` span for a chat-model call, stamping request identity + replay hash."""
        model_id = _model_id(serialized, kwargs)
        flat = _flatten_messages(messages)
        params = kwargs.get("invocation_params") or {}
        attrs: dict[str, Any] = {
            semconv.GEN_AI_OPERATION_NAME: "chat",
            semconv.GEN_AI_SYSTEM: _provider_of(model_id),
            semconv.GEN_AI_REQUEST_MODEL: model_id,
            semconv.AS_REPLAY_REQUEST_HASH: request_hash(
                {"model": model_id, "messages": flat, "optional_params": params}
            ),
        }
        if (temperature := params.get("temperature")) is not None:
            attrs[semconv.GEN_AI_REQUEST_TEMPERATURE] = temperature
        if (max_tokens := params.get("max_tokens")) is not None:
            attrs[semconv.GEN_AI_REQUEST_MAX_TOKENS] = max_tokens
        if self._capture_content:
            attrs[semconv.GEN_AI_INPUT_MESSAGES] = redact_pii(_json(flat))
        self._open(run_id, parent_run_id, semconv.SPAN_MODEL, SpanKind.LLM, attrs)
        self._model_ids[run_id] = model_id

    async def on_llm_start(
        self,
        serialized: dict[str, Any] | None,
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Open a ``model`` span for a text-completion (non-chat) call."""
        model_id = _model_id(serialized, kwargs)
        attrs: dict[str, Any] = {
            semconv.GEN_AI_OPERATION_NAME: "chat",
            semconv.GEN_AI_SYSTEM: _provider_of(model_id),
            semconv.GEN_AI_REQUEST_MODEL: model_id,
            semconv.AS_REPLAY_REQUEST_HASH: request_hash(
                {"model": model_id, "messages": list(prompts), "optional_params": {}}
            ),
        }
        if self._capture_content:
            attrs[semconv.GEN_AI_INPUT_MESSAGES] = redact_pii(_json(list(prompts)))
        self._open(run_id, parent_run_id, semconv.SPAN_MODEL, SpanKind.LLM, attrs)
        self._model_ids[run_id] = model_id

    async def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        """Stamp tokens/cost/latency/finish-reasons onto the model span and close it."""
        span = self._spans.get(run_id)
        if span is None:
            return
        model_id = self._model_ids.get(run_id, "")
        usage = _usage_from_result(response, model_id, self._latency_ms(run_id))
        span.set_attributes(usage_attributes(usage))
        if self._capture_content and (text := _output_text(response)):
            span.set_attributes({semconv.GEN_AI_OUTPUT_MESSAGES: redact_pii(text)})
        self._close(run_id)

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        """Mark the model span errored and close it."""
        self._error_close(run_id, error)

    # -- tool spans ----------------------------------------------------------------------------

    async def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Open a ``tool.<name>`` span, tagging the MCP server when the tool came from one."""
        name = (serialized or {}).get("name") or "tool"
        attrs: dict[str, Any] = {
            semconv.GEN_AI_OPERATION_NAME: "execute_tool",
            semconv.GEN_AI_TOOL_NAME: name,
        }
        if server := self._mcp_servers.get(name):
            attrs[semconv.AS_TOOL_MCP_SERVER] = server
        if self._capture_content:
            payload = _json(inputs) if inputs else str(input_str)
            attrs[semconv.GEN_AI_INPUT_MESSAGES] = redact_pii(payload)
        self._open(run_id, parent_run_id, semconv.tool_span(name), SpanKind.TOOL, attrs)

    async def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        """Capture the (redacted) tool result when content capture is on, then close the span."""
        span = self._spans.get(run_id)
        if span is not None and self._capture_content:
            span.set_attributes({semconv.GEN_AI_OUTPUT_MESSAGES: redact_pii(str(output))})
        self._close(run_id)

    async def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        """Mark the tool span errored and close it."""
        self._error_close(run_id, error)

    # -- node spans ----------------------------------------------------------------------------

    async def on_chain_start(
        self,
        serialized: dict[str, Any] | None,
        inputs: dict[str, Any] | Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        metadata: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """Open a ``node.<name>`` span for a LangGraph node's own runnable (not nested chains).

        LangGraph tags every runnable inside a node with ``metadata['langgraph_node']``; the node
        *boundary* is the one whose own name equals that node name. Gating on that keeps one span
        per node instead of one per nested chain, and skips the graph's outer runnable (no tag).
        """
        node = (metadata or {}).get("langgraph_node")
        name = (serialized or {}).get("name") or kwargs.get("name")
        if not node or name != node:
            return
        self._open(run_id, parent_run_id, semconv.node_span(str(node)), SpanKind.INTERNAL, {})

    async def on_chain_end(self, outputs: Any, *, run_id: UUID, **kwargs: Any) -> None:
        """Close the node span for ``run_id`` if one was opened."""
        if run_id in self._spans:
            self._close(run_id)

    async def on_chain_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        """Mark the node span errored and close it, if one was opened."""
        if run_id in self._spans:
            self._error_close(run_id, error)
