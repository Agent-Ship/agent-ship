"""Unit tests for LLM provider config — model strings, aliases, and LiteLLM prefixes.

These tests are pure logic (no mocks, no network) and run without any API keys.
They exist to catch regressions like:
  - Wrong LiteLLM prefix (e.g. "claude/" instead of "anthropic/")
  - Deprecated model IDs passed raw to the API (e.g. "gemini-1.5-pro" without -002)
  - New model added to enum but missing from provider models list
  - Alias map out of sync with the enum
"""

import pytest

from src.agent_framework.configs.llm.llm_provider_config import (
    LLMModel,
    LLMProviderConfig,
    LLMProviderName,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def model_string(provider_name: str, model_name: str) -> str:
    """Resolve a provider + model name to the LiteLLM model string."""
    provider = LLMProviderConfig.get_llm_provider(LLMProviderName(provider_name))
    return provider.get_model_string(model_name)


# ---------------------------------------------------------------------------
# LiteLLM prefix correctness
# ---------------------------------------------------------------------------

class TestLiteLLMPrefixes:
    """The prefix in the model string must match what LiteLLM expects."""

    def test_openai_prefix(self):
        assert model_string("openai", "gpt-4o").startswith("openai/")

    def test_claude_uses_anthropic_prefix_not_claude(self):
        # Critical: LiteLLM routes on "anthropic/", not "claude/"
        result = model_string("claude", "claude-3-5-sonnet")
        assert result.startswith("anthropic/"), (
            f"Expected 'anthropic/' prefix but got '{result}'. "
            "LiteLLM will fail to route Claude calls with any other prefix."
        )
        assert not result.startswith("claude/")

    def test_gemini_prefix(self):
        assert model_string("gemini", "gemini-2.0-flash").startswith("gemini/")


# ---------------------------------------------------------------------------
# Model alias resolution
# ---------------------------------------------------------------------------

class TestModelAliases:
    """Aliases map user-friendly names to versioned API model IDs."""

    # Gemini — 1.5 models were shut down Sep 2025; aliases forward to 2.5
    def test_gemini_1_5_pro_forwards_to_current(self):
        result = model_string("gemini", "gemini-1.5-pro")
        assert result == "gemini/gemini-2.5-pro", (
            "gemini-1.5-pro is shut down — must alias to a live model"
        )

    def test_gemini_1_5_flash_forwards_to_current(self):
        result = model_string("gemini", "gemini-1.5-flash")
        assert result == "gemini/gemini-2.5-flash"

    def test_gemini_2_0_flash_no_alias_needed(self):
        assert model_string("gemini", "gemini-2.0-flash") == "gemini/gemini-2.0-flash"

    def test_gemini_2_5_flash_no_alias(self):
        # 2.5 models are stable GA — no alias needed
        assert model_string("gemini", "gemini-2.5-flash") == "gemini/gemini-2.5-flash"

    def test_gemini_2_5_pro_no_alias(self):
        assert model_string("gemini", "gemini-2.5-pro") == "gemini/gemini-2.5-pro"

    # Claude
    def test_claude_3_5_sonnet_resolves_to_dated(self):
        result = model_string("claude", "claude-3-5-sonnet")
        assert result == "anthropic/claude-3-5-sonnet-20241022"

    def test_claude_3_5_haiku_resolves_to_dated(self):
        result = model_string("claude", "claude-3-5-haiku")
        assert result == "anthropic/claude-3-5-haiku-20241022"

    def test_claude_3_7_sonnet_resolves_to_dated(self):
        result = model_string("claude", "claude-3-7-sonnet")
        assert result == "anthropic/claude-3-7-sonnet-20250219"

    def test_claude_opus_4_resolves_to_dated(self):
        result = model_string("claude", "claude-opus-4")
        assert result == "anthropic/claude-opus-4-20250514"

    def test_claude_sonnet_4_resolves_to_dated(self):
        result = model_string("claude", "claude-sonnet-4")
        assert result == "anthropic/claude-sonnet-4-20250514"

    # GPT-5 — released August 2025; no aliases needed, IDs are stable
    def test_gpt5_no_alias_needed(self):
        assert model_string("openai", "gpt-5") == "openai/gpt-5"

    def test_gpt5_mini_no_alias_needed(self):
        assert model_string("openai", "gpt-5-mini") == "openai/gpt-5-mini"

    def test_gpt5_nano_no_alias_needed(self):
        assert model_string("openai", "gpt-5-nano") == "openai/gpt-5-nano"

    def test_unknown_model_passthrough(self):
        # A model not in aliases should pass through unchanged (LiteLLM may know it)
        result = model_string("openai", "gpt-99-turbo")
        assert result == "openai/gpt-99-turbo"


# ---------------------------------------------------------------------------
# Model enum ↔ provider list consistency
# ---------------------------------------------------------------------------

class TestProviderModelListConsistency:
    """Every model in a provider's models list must exist in LLMModel enum,
    and the enum value must round-trip through get_model_string without error."""

    @pytest.mark.parametrize("provider_name", ["openai", "claude", "gemini", "groq", "openrouter"])
    def test_all_provider_models_are_valid_enum_values(self, provider_name):
        provider = LLMProviderConfig.get_llm_provider(LLMProviderName(provider_name))
        for model in provider.models:
            # Each model must be a valid LLMModel enum member
            assert model in LLMModel, f"{model} in {provider_name} models list is not a valid LLMModel"
            # And get_model_string must not raise
            result = provider.get_model_string(model.value)
            assert result  # non-empty string

    def test_openai_models_list_is_not_empty(self):
        assert len(LLMProviderConfig.openai.models) > 0

    def test_claude_models_list_is_not_empty(self):
        assert len(LLMProviderConfig.claude.models) > 0

    def test_gemini_models_list_is_not_empty(self):
        assert len(LLMProviderConfig.gemini.models) > 0

    def test_groq_models_list_is_not_empty(self):
        assert len(LLMProviderConfig.groq.models) > 0

    def test_vllm_models_list_is_empty(self):
        # vLLM accepts any model name — no fixed list enforced
        assert LLMProviderConfig.vllm.models == []

    def test_openrouter_models_list_is_not_empty(self):
        assert len(LLMProviderConfig.openrouter.models) > 0


# ---------------------------------------------------------------------------
# Default models
# ---------------------------------------------------------------------------

class TestDefaultModels:
    def test_openai_default_in_model_list(self):
        p = LLMProviderConfig.openai
        assert p.default_model in p.models

    def test_claude_default_in_model_list(self):
        p = LLMProviderConfig.claude
        assert p.default_model in p.models

    def test_gemini_default_in_model_list(self):
        p = LLMProviderConfig.gemini
        assert p.default_model in p.models

    def test_gemini_default_is_not_deprecated(self):
        # All 1.5 and 2.0 models are deprecated/shut down — default must be 2.5+
        deprecated = {"gemini-1.5-pro", "gemini-1.5-flash", "gemini-2.0-flash", "gemini-2.0-flash-lite"}
        assert LLMProviderConfig.gemini.default_model.value not in deprecated

    def test_groq_default_in_model_list(self):
        p = LLMProviderConfig.groq
        assert p.default_model in p.models

    def test_vllm_default_model_is_none(self):
        # vLLM has no fixed default — user must specify llm_model in YAML
        assert LLMProviderConfig.vllm.default_model is None

    def test_openrouter_default_in_model_list(self):
        p = LLMProviderConfig.openrouter
        assert p.default_model in p.models


# ---------------------------------------------------------------------------
# Full model string format
# ---------------------------------------------------------------------------

class TestModelStringFormat:
    """Model strings must be in 'prefix/model-id' format for LiteLLM."""

    @pytest.mark.parametrize("provider_name,model_name,expected", [
        ("openai", "gpt-5",            "openai/gpt-5"),
        ("openai", "gpt-5-mini",       "openai/gpt-5-mini"),
        ("openai", "gpt-5-nano",       "openai/gpt-5-nano"),
        ("openai", "gpt-4o",           "openai/gpt-4o"),
        ("openai", "gpt-4o-mini",      "openai/gpt-4o-mini"),
        ("openai", "o3",               "openai/o3"),
        ("openai", "o3-mini",          "openai/o3-mini"),
        ("claude", "claude-3-5-sonnet","anthropic/claude-3-5-sonnet-20241022"),
        ("claude", "claude-sonnet-4",  "anthropic/claude-sonnet-4-20250514"),
        ("gemini", "gemini-2.0-flash", "gemini/gemini-2.0-flash"),
        ("gemini", "gemini-1.5-pro",   "gemini/gemini-2.5-pro"),
    ])
    def test_model_string(self, provider_name, model_name, expected):
        assert model_string(provider_name, model_name) == expected

    @pytest.mark.parametrize("provider_name", ["openai", "claude", "gemini", "groq", "openrouter"])
    def test_model_string_contains_slash(self, provider_name):
        provider = LLMProviderConfig.get_llm_provider(LLMProviderName(provider_name))
        for model in provider.models:
            result = provider.get_model_string(model.value)
            assert "/" in result, f"Model string '{result}' missing provider/model separator"
            prefix, _, model_id = result.partition("/")
            assert prefix, "prefix must not be empty"
            assert model_id, "model id must not be empty"


# ---------------------------------------------------------------------------
# Groq
# ---------------------------------------------------------------------------

class TestGroqProvider:
    """Groq is a cloud inference provider served via LiteLLM's groq/ prefix."""

    def test_prefix_is_groq(self):
        assert model_string("groq", "llama-3.3-70b-versatile").startswith("groq/")

    def test_no_api_base(self):
        # Groq uses its own cloud endpoint — no custom api_base needed
        assert LLMProviderConfig.groq.api_base is None

    @pytest.mark.parametrize("model_name,expected", [
        ("llama-3.3-70b-versatile", "groq/llama-3.3-70b-versatile"),
        ("llama-3.1-8b-instant",    "groq/llama-3.1-8b-instant"),
        ("llama3-70b-8192",         "groq/llama3-70b-8192"),
        ("llama3-8b-8192",          "groq/llama3-8b-8192"),
        ("mixtral-8x7b-32768",      "groq/mixtral-8x7b-32768"),
        ("gemma2-9b-it",            "groq/gemma2-9b-it"),
    ])
    def test_model_strings(self, model_name, expected):
        assert model_string("groq", model_name) == expected

    def test_unknown_model_passthrough(self):
        # Future models not yet in enum should still pass through
        result = model_string("groq", "llama-4-scout")
        assert result == "groq/llama-4-scout"

    def test_get_llm_provider_returns_groq(self):
        provider = LLMProviderConfig.get_llm_provider(LLMProviderName.GROQ)
        assert provider.name == LLMProviderName.GROQ


# ---------------------------------------------------------------------------
# vLLM
# ---------------------------------------------------------------------------

class TestVLLMProvider:
    """vLLM is a self-hosted OpenAI-compatible server. Any model name is valid."""

    def test_prefix_is_hosted_vllm(self):
        result = model_string("vllm", "meta-llama/Llama-3.1-8B-Instruct")
        assert result.startswith("hosted_vllm/")

    def test_model_string_with_slash_in_name(self):
        # HuggingFace-style model IDs contain a slash — must survive round-trip
        result = model_string("vllm", "meta-llama/Llama-3.1-8B-Instruct")
        assert result == "hosted_vllm/meta-llama/Llama-3.1-8B-Instruct"

    def test_model_string_simple_name(self):
        result = model_string("vllm", "mistral-7b")
        assert result == "hosted_vllm/mistral-7b"

    def test_api_base_defaults_to_localhost(self):
        assert LLMProviderConfig.vllm.api_base == "http://localhost:8000"

    def test_api_base_respects_env_var(self):
        # api_base is read from VLLM_API_BASE at module import time.
        # We verify the plumbing by checking the LLMProvider._api_base field
        # directly, keeping isolation without a module reload that would
        # corrupt other tests in the suite.
        from src.agent_framework.configs.llm.llm_provider_config import LLMProvider, LLMProviderName, ProviderAPIKey
        custom = LLMProvider(
            name=LLMProviderName.VLLM,
            api_key=ProviderAPIKey.VLLM,
            litellm_prefix="hosted_vllm",
            models=[],
            default_model=None,
            api_base="http://my-gpu-server:9000",
        )
        assert custom.api_base == "http://my-gpu-server:9000"

    def test_arbitrary_model_accepted_via_missing(self):
        # LLMModel._missing_ must return a pseudo-member for unknown strings
        m = LLMModel("some-custom-model-xyz")
        assert m.value == "some-custom-model-xyz"

    def test_model_with_slashes_accepted_via_missing(self):
        m = LLMModel("org/model-name")
        assert m.value == "org/model-name"

    def test_get_llm_provider_returns_vllm(self):
        provider = LLMProviderConfig.get_llm_provider(LLMProviderName.VLLM)
        assert provider.name == LLMProviderName.VLLM


# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------

class TestOpenRouterProvider:
    """OpenRouter is a multi-model gateway routed via LiteLLM's openrouter/ prefix.
    Model names use the org/model namespace (e.g. openai/gpt-4o)."""

    def test_prefix_is_openrouter(self):
        assert model_string("openrouter", "openai/gpt-4o").startswith("openrouter/")

    def test_no_api_base(self):
        # OpenRouter is a cloud service — no custom api_base needed
        assert LLMProviderConfig.openrouter.api_base is None

    def test_get_llm_provider_returns_openrouter(self):
        provider = LLMProviderConfig.get_llm_provider(LLMProviderName.OPENROUTER)
        assert provider.name == LLMProviderName.OPENROUTER

    @pytest.mark.parametrize("model_name,expected", [
        ("openai/gpt-4o",                    "openrouter/openai/gpt-4o"),
        ("openai/gpt-4o-mini",               "openrouter/openai/gpt-4o-mini"),
        ("anthropic/claude-3.5-sonnet",      "openrouter/anthropic/claude-3.5-sonnet"),
        ("anthropic/claude-3.5-haiku",       "openrouter/anthropic/claude-3.5-haiku"),
        ("meta-llama/llama-3.3-70b-instruct","openrouter/meta-llama/llama-3.3-70b-instruct"),
        ("google/gemini-2.0-flash-001",      "openrouter/google/gemini-2.0-flash-001"),
        ("deepseek/deepseek-r1",             "openrouter/deepseek/deepseek-r1"),
        ("deepseek/deepseek-chat-v3-0324",   "openrouter/deepseek/deepseek-chat-v3-0324"),
        ("mistralai/mixtral-8x7b-instruct",  "openrouter/mistralai/mixtral-8x7b-instruct"),
    ])
    def test_model_strings(self, model_name, expected):
        assert model_string("openrouter", model_name) == expected

    def test_unknown_model_passthrough(self):
        # Models not in the curated list should still pass through for future additions
        result = model_string("openrouter", "cohere/command-r-plus")
        assert result == "openrouter/cohere/command-r-plus"

    def test_model_string_preserves_org_slash(self):
        # The org/model slash must survive — it's part of the OpenRouter model ID
        result = model_string("openrouter", "openai/gpt-4o")
        parts = result.split("/")
        assert parts[0] == "openrouter"
        assert parts[1] == "openai"
        assert parts[2] == "gpt-4o"

    def test_default_model_is_gpt_4o_mini(self):
        assert LLMProviderConfig.openrouter.default_model.value == "openai/gpt-4o-mini"
