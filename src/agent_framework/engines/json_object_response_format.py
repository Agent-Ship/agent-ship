"""LiteLLM providers that accept OpenAI-style ``response_format: {"type": "json_object"}``.

Single source of truth for LangGraph (direct LiteLLM calls) and ADK (LiteLlm wrapper
parity / docs). ADK still wires structured output primarily via ``output_schema``;
this enum documents which provider strings align with json_object in LiteLLM.
"""

from __future__ import annotations

from enum import StrEnum


class JsonObjectResponseFormatProvider(StrEnum):
    """LiteLLM backends that accept OpenAI-style json_object ``response_format``."""

    OPENAI = "openai"
    GEMINI = "gemini"
    VERTEX_AI = "vertex_ai"
    VLLM = "vllm"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    DEEPSEEK = "deepseek"


def provider_supports_json_object_response_format(provider_value: str) -> bool:
    """Return True if *provider_value* is a LiteLLM backend that supports json_object mode."""
    try:
        JsonObjectResponseFormatProvider(provider_value)
    except ValueError:
        return False
    return True
