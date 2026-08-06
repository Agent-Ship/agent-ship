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

import litellm
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_litellm import ChatLiteLLM

from .errors import ModelError, SpecError

# On a failed call LiteLLM prints its own "Give Feedback / Get Help" + "LiteLLM.Info"
# banner straight to stderr (not via the logging level), which buries AgentShip's
# single clean ``Error: …`` line. Turn that banner off at import so a failed run
# shows only our actionable message; ``map_model_error`` carries the real cause.
litellm.suppress_debug_info = True

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


#: Provider prefix (the part before the first ``/`` in a LiteLLM model id) → the
#: environment variable that carries its API credential. Used to name the exact
#: variable to set in a credential :class:`~agentship.errors.ModelError`.
_PROVIDER_ENV_VAR = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
    "together_ai": "TOGETHER_AI_API_KEY",
}

#: Substrings that mark a provider exception as a missing/invalid-credential
#: failure (matched case-insensitively against the exception message). LiteLLM
#: surfaces the missing-key case as a generic error whose *message* — not its type
#: — is the reliable signal, so we match on text.
_CREDENTIAL_MARKERS = (
    "missing credentials",
    "api_key",
    "api key",
    "authentication",
    "invalid_api_key",
    "incorrect api key",
    "no api key",
)


def _provider_env_var(model: str) -> str | None:
    """Return the API-key env var for ``model``'s provider prefix, or ``None``.

    ``model`` is a LiteLLM id like ``"openai/gpt-4o-mini"``; the provider is the
    part before the first ``/``. Returns the known credential env var (e.g.
    ``"OPENAI_API_KEY"``) or ``None`` when the provider is unknown/unprefixed.
    """
    provider = model.split("/", 1)[0].strip().lower() if model else ""
    return _PROVIDER_ENV_VAR.get(provider)


def _is_credential_error(exc: BaseException) -> bool:
    """Whether ``exc`` looks like a missing/invalid-credential provider failure.

    Matches known credential markers (case-insensitively) against the exception
    message. LiteLLM reports the missing-key case with a non-auth exception type
    but a message that names the credential, so the message is the reliable signal.
    """
    text = str(exc).lower()
    return any(marker in text for marker in _CREDENTIAL_MARKERS)


def map_model_error(model: str, exc: Exception) -> ModelError:
    """Turn a provider/LiteLLM exception into an actionable :class:`ModelError`.

    For a credential failure the message names the exact env var to set (derived
    from ``model``'s provider prefix); other provider errors are wrapped with their
    concise cause. The returned error chains ``exc`` as its cause, so callers should
    ``raise map_model_error(...) from exc`` and the original traceback stays
    reachable under ``--debug``.
    """
    if _is_credential_error(exc):
        env_var = _provider_env_var(model)
        if env_var is not None:
            message = (
                f"No API credentials for model {model!r}. Set {env_var} — export it, "
                f"or put it in a .env in this directory (see .env.example) — and retry."
            )
        else:
            message = (
                f"No API credentials for model {model!r}. Set the provider's API key "
                f"environment variable — export it, or put it in a .env in this "
                f"directory (see .env.example) — and retry."
            )
        return ModelError(message)
    return ModelError(f"Model call failed for {model!r}: {exc}")
