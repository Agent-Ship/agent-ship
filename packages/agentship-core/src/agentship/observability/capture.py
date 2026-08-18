"""Record/replay capture — turn a model call into replay attributes for a model span (§4.10).

To make eval deterministic (P12), every ``model`` span carries a stable ``request_hash`` and,
*only when content capture is enabled*, the request/response payload. P12 keys LLM cassettes on the
hash so an ``origin="trace"`` case replays byte-for-byte.

This module is pure and vendor-free: it computes the hash and builds an attribute dict from a
provider-shaped request/response. The LiteLLM callback (which already has both in hand) calls
:func:`replay_attributes` and stamps the result onto the current model span via
:meth:`Observer.annotate_model`. The PHI gate lives here (§4.6): the hash is always safe to record
(it is a digest, not content), but the request/response bodies are included only when
``capture_content`` is true, so prompt/response text never leaves the process in the PHI profile.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .semconv import (
    AS_REPLAY_REQUEST_HASH,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OUTPUT_MESSAGES,
)


def _canonical(value: Any) -> str:
    """Serialize a value to a stable string: sorted keys, no whitespace, non-JSON coerced to str."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str, ensure_ascii=False)


def request_hash(kwargs: dict[str, Any]) -> str:
    """Return a stable SHA-256 over the canonicalized request (model + messages + params).

    Identical requests hash equal; changing the model, any message, or a sampling parameter changes
    the hash. Only the fields that determine the model's output are included, so incidental callback
    metadata (timing, request id) does not perturb the key.
    """
    request = {
        "model": kwargs.get("model"),
        "messages": kwargs.get("messages"),
        "params": kwargs.get("optional_params") or {},
    }
    return hashlib.sha256(_canonical(request).encode()).hexdigest()


def _response_messages(response_obj: Any) -> list[dict[str, Any]]:
    """Extract the assistant message(s) from a provider response as plain dicts."""
    messages: list[dict[str, Any]] = []
    for choice in getattr(response_obj, "choices", None) or []:
        message = getattr(choice, "message", None)
        content = getattr(message, "content", None) if message is not None else None
        role = getattr(message, "role", "assistant") if message is not None else "assistant"
        messages.append({"role": role, "content": content})
    return messages


def replay_attributes(
    kwargs: dict[str, Any], response_obj: Any, *, capture_content: bool
) -> dict[str, str]:
    """Build the replay attributes to stamp onto a model span.

    Always includes ``agentship.replay.request_hash``. When ``capture_content`` is true, also
    includes the canonicalized request (``gen_ai.input.messages``) and response
    (``gen_ai.output.messages``) so P12 can rebuild a full cassette locally. When false (the PHI
    default) only the hash is emitted, so no prompt/response content is ever set as an attribute.
    """
    attrs: dict[str, str] = {AS_REPLAY_REQUEST_HASH: request_hash(kwargs)}
    if capture_content:
        attrs[GEN_AI_INPUT_MESSAGES] = _canonical(kwargs.get("messages") or [])
        attrs[GEN_AI_OUTPUT_MESSAGES] = _canonical(_response_messages(response_obj))
    return attrs
