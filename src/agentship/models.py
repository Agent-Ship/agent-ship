"""The model seam: :func:`resolve_model` over LiteLLM's ``ChatLiteLLM``.

A single plain function turns a spec's ``model`` string (e.g.
``"openai/gpt-4o-mini"``) plus optional generation params into a LangChain
``BaseChatModel`` the engine can invoke. LiteLLM gives us one uniform interface
across providers, so the kernel never grows provider-specific branches.

This is deliberately a function, **not** a registry (architecture: no speculative
generality) — a ``ModelSource`` seam is added only when a second real
implementation exists. The engine resolves its chat model through here, and
offline tests monkeypatch this function to inject a fake model (no network).
"""

from __future__ import annotations

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_litellm import ChatLiteLLM

from .errors import SpecError

#: Maps ``resolve_model`` keyword names to the ``ChatLiteLLM`` constructor field.
#: ``timeout`` is exposed as a plain name but LiteLLM calls it ``request_timeout``.
_PARAM_TO_FIELD = {
    "temperature": "temperature",
    "max_tokens": "max_tokens",
    "api_base": "api_base",
    "timeout": "request_timeout",
}


def resolve_model(model: str, **params: Any) -> BaseChatModel:
    """Build a ``ChatLiteLLM`` chat model for ``model`` with optional params.

    ``model`` is a LiteLLM model id such as ``"openai/gpt-4o-mini"``. Recognised
    keyword params — ``temperature``, ``max_tokens``, ``api_base`` and ``timeout``
    (threaded as LiteLLM's ``request_timeout``) — are forwarded when supplied; any
    passed as ``None`` are dropped so the model keeps its own default. Unknown
    keyword params are forwarded as-is to ``ChatLiteLLM``.

    Raises :class:`~agentship.errors.SpecError` when ``model`` is empty or blank,
    so a misconfigured spec fails with an actionable message before any provider
    call is attempted.
    """
    if not model or not model.strip():
        raise SpecError(
            "model must be a non-empty LiteLLM model id (e.g. 'openai/gpt-4o-mini'); "
            "got an empty value"
        )

    kwargs: dict[str, Any] = {}
    for name, value in params.items():
        if value is None:
            continue
        field = _PARAM_TO_FIELD.get(name, name)
        kwargs[field] = value

    return ChatLiteLLM(model=model, **kwargs)
