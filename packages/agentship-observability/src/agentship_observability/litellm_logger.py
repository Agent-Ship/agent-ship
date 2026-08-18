"""The one LiteLLM callback that turns a completed model call into span usage (P07 · C3).

Every model call in AgentShip goes through LiteLLM. LiteLLM fires success callbacks with the token
counts, resolved model, finish reasons, and computed cost — exactly the roll-up the ``model`` span
wants. This module registers **one** process-global :class:`CustomLogger` that, on each success,
builds a :class:`Usage` and hands it to the observer's :meth:`on_model`, which stamps it onto the
currently-active model span (§4.3).

The callback carries no per-run state: ``on_model`` targets whatever model span is current on the
OTel context, so a single registered logger serves every concurrent agent. Registration is
idempotent — calling :func:`register_litellm_logger` twice adds the callback once (§DoD "one
process-global LiteLLM callback").
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import litellm
from agentship.observability import Observer, Usage
from litellm.integrations.custom_logger import CustomLogger

_log = logging.getLogger("agentship.observability")


def _latency_ms(start_time: Any, end_time: Any) -> float:
    """Milliseconds between LiteLLM's start/end markers; 0.0 if they aren't usable datetimes."""
    if isinstance(start_time, datetime) and isinstance(end_time, datetime):
        return (end_time - start_time).total_seconds() * 1000.0
    return 0.0


def _provider_of(kwargs: dict[str, Any], model: str) -> str:
    """Best-effort LLM provider (``openai``, ``anthropic``, …) from kwargs, else the model prefix.

    LiteLLM usually sets ``custom_llm_provider``; when it doesn't, fall back to the
    ``provider/model`` prefix, and finally to ``unknown`` rather than raising inside a callback.
    """
    provider = kwargs.get("custom_llm_provider")
    if provider:
        return str(provider)
    if "/" in model:
        return model.split("/", 1)[0]
    return "unknown"


def _finish_reasons(response_obj: Any) -> list[str]:
    """Collect non-empty ``finish_reason`` values from a response's choices."""
    reasons: list[str] = []
    for choice in getattr(response_obj, "choices", None) or []:
        reason = getattr(choice, "finish_reason", None)
        if reason:
            reasons.append(str(reason))
    return reasons


def usage_from_litellm(
    kwargs: dict[str, Any], response_obj: Any, start_time: Any, end_time: Any
) -> Usage | None:
    """Build a :class:`Usage` from a LiteLLM success callback's arguments, or ``None`` if unusable.

    Reads token counts from ``response_obj.usage``, the resolved model from the response, the cost
    from ``kwargs['response_cost']`` (LiteLLM computes it), and latency from the start/end markers.
    Returns ``None`` when there is no usage block to report, so the caller can skip stamping.
    """
    usage = getattr(response_obj, "usage", None)
    if usage is None:
        return None
    requested_model = str(kwargs.get("model") or getattr(response_obj, "model", "") or "")
    response_model = getattr(response_obj, "model", None)
    cost = kwargs.get("response_cost")
    return Usage(
        model=requested_model,
        provider=_provider_of(kwargs, requested_model),
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        cost_usd=float(cost) if cost is not None else None,
        latency_ms=_latency_ms(start_time, end_time),
        finish_reasons=_finish_reasons(response_obj),
        response_model=str(response_model) if response_model else None,
    )


class AgentShipLiteLLMLogger(CustomLogger):
    """A LiteLLM callback that stamps each completed model call onto the active model span.

    Holds a reference to an :class:`Observer` only to reach :meth:`Observer.on_model`; the span it
    writes to is the one current on the trace context, not anything this logger tracks. Both the
    sync and async success hooks funnel through :meth:`_emit`, which is fail-open — a bad callback
    payload is logged and dropped, never raised into LiteLLM's request path.
    """

    def __init__(self, observer: Observer) -> None:
        """Bind the observer whose ``on_model`` receives each call's usage."""
        self._observer = observer

    def _emit(self, kwargs: Any, response_obj: Any, start_time: Any, end_time: Any) -> None:
        """Convert one success payload to :class:`Usage` and stamp it; swallow any failure."""
        try:
            usage = usage_from_litellm(kwargs or {}, response_obj, start_time, end_time)
            if usage is not None:
                self._observer.on_model(usage)
        except Exception:  # noqa: BLE001 - a callback must never break the model call
            _log.warning("observability.litellm_logger.failed (usage dropped)", exc_info=True)

    def log_success_event(self, kwargs, response_obj, start_time, end_time) -> None:
        """LiteLLM sync success hook — stamp usage onto the current model span."""
        self._emit(kwargs, response_obj, start_time, end_time)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time) -> None:
        """LiteLLM async success hook — stamp usage onto the current model span."""
        self._emit(kwargs, response_obj, start_time, end_time)


#: The one registered logger for this process; ``None`` until the first registration.
_registered: AgentShipLiteLLMLogger | None = None


def register_litellm_logger(observer: Observer) -> AgentShipLiteLLMLogger:
    """Register the process-global LiteLLM callback once, returning it (idempotent).

    On the first call, an :class:`AgentShipLiteLLMLogger` is appended to ``litellm.callbacks``.
    Later calls return the same logger without re-registering, so only one usage stamp fires per
    model call regardless of how many agents are built.
    """
    global _registered
    if _registered is not None:
        return _registered
    logger = AgentShipLiteLLMLogger(observer)
    litellm.callbacks.append(logger)
    _registered = logger
    return logger


def _reset_litellm_logger_for_tests() -> None:
    """Remove the registered callback and clear the guard. For registration tests only."""
    global _registered
    if _registered is not None and _registered in litellm.callbacks:
        litellm.callbacks.remove(_registered)
    _registered = None
