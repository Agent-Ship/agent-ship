import os
from typing import List, ClassVar
from enum import Enum
from dotenv import load_dotenv
import logging
import litellm

load_dotenv()

# Newer models (e.g. GPT-5) reject params that older models accept (e.g. temperature).
# drop_params=True tells LiteLLM to silently strip unsupported params per-model
# rather than raising UnsupportedParamsError. Set once here — applies to all engines.
litellm.drop_params = True


logger = logging.getLogger(__name__)


class LLMProviderName(Enum):
    """User-facing provider name (used in agent YAML as llm_provider_name)."""
    OPENAI = "openai"
    CLAUDE = "claude"
    GEMINI = "gemini"
    VLLM = "vllm"
    GROQ = "groq"
    OPENROUTER = "openrouter"
    DEEPSEEK = "deepseek"
    AZURE = "azure"

    def __str__(self):
        return self.value


class LLMModel(Enum):
    """User-facing model names (used in agent YAML as llm_model)."""
    # OpenAI
    GPT_5 = "gpt-5"
    GPT_5_MINI = "gpt-5-mini"
    GPT_5_NANO = "gpt-5-nano"
    GPT_4O = "gpt-4o"
    GPT_4O_MINI = "gpt-4o-mini"
    GPT_3_5_TURBO = "gpt-3.5-turbo"   # legacy, kept for backwards compat
    GPT_4_1 = "gpt-4.1"
    GPT_4_1_MINI = "gpt-4.1-mini"
    O1 = "o1"
    O1_MINI = "o1-mini"
    O4_MINI = "o4-mini"
    O3 = "o3"
    O3_MINI = "o3-mini"
    GPT_4_5 = "gpt-4.5-preview"
    # Claude
    CLAUDE_3_5_SONNET = "claude-3-5-sonnet"
    CLAUDE_3_5_HAIKU = "claude-3-5-haiku"
    CLAUDE_3_7_SONNET = "claude-3-7-sonnet"
    CLAUDE_OPUS_4 = "claude-opus-4"
    CLAUDE_SONNET_4 = "claude-sonnet-4"
    CLAUDE_HAIKU_4_5 = "claude-haiku-4-5"
    # Gemini (1.5 models shut down Sep 2025 — kept in enum for YAML parse compat only)
    GEMINI_1_5_PRO = "gemini-1.5-pro"
    GEMINI_1_5_FLASH = "gemini-1.5-flash"
    GEMINI_2_0_FLASH = "gemini-2.0-flash"
    GEMINI_2_0_FLASH_LITE = "gemini-2.0-flash-lite"
    GEMINI_2_5_PRO = "gemini-2.5-pro"
    GEMINI_2_5_FLASH = "gemini-2.5-flash"
    GEMINI_2_5_FLASH_LITE = "gemini-2.5-flash-lite"
    # Groq
    LLAMA_3_3_70B = "llama-3.3-70b-versatile"
    LLAMA_3_1_8B = "llama-3.1-8b-instant"
    LLAMA3_70B = "llama3-70b-8192"
    LLAMA3_8B = "llama3-8b-8192"
    MIXTRAL_8X7B = "mixtral-8x7b-32768"
    GEMMA2_9B = "gemma2-9b-it"
    # Qwen (via Groq)
    QWEN_2_5_72B = "qwen-2.5-72b-instruct"
    QWEN_2_5_7B = "qwen-2.5-7b-instruct-fp16"
    QWEN_2_5_CODER_32B = "qwen-2.5-coder-32b-instruct"
    # Llama 4 (via Groq)
    LLAMA_4_SCOUT = "llama-4-scout-17b-16e-instruct"
    LLAMA_4_MAVERICK = "llama-4-maverick-17b-128e-instruct"
    # DeepSeek
    DEEPSEEK_V3 = "deepseek-chat"       # DeepSeek V3
    DEEPSEEK_R1 = "deepseek-reasoner"   # DeepSeek R1

    def __str__(self):
        return self.value

    @classmethod
    def _missing_(cls, value: object):
        '''Allow arbitrary model name strings (e.g. vLLM-hosted models like
        'meta-llama/Llama-3.1-8B-Instruct', or OpenRouter routes like
        'anthropic/claude-3.5-sonnet') that aren't in the fixed enum.
        Returns a dynamic pseudo-member so the rest of the config pipeline
        can treat it uniformly.'''
        if not isinstance(value, str):
            return None
        obj = object.__new__(cls)
        obj._value_ = value
        obj._name_ = value.upper().replace("-", "_").replace("/", "__").replace(".", "_")
        return obj


class ProviderAPIKey(Enum):
    """API key for the LLM provider."""
    OPENAI = os.getenv("OPENAI_API_KEY", "")
    CLAUDE = os.getenv("ANTHROPIC_API_KEY", "")
    GEMINI = os.getenv("GEMINI_API_KEY")
    VLLM = os.getenv("VLLM_API_KEY", "EMPTY")  # vLLM servers often require a dummy key
    GROQ = os.getenv("GROQ_API_KEY", "")
    OPENROUTER = os.getenv("OPENROUTER_API_KEY", "")
    DEEPSEEK = os.getenv("DEEPSEEK_API_KEY", "")
    AZURE = os.getenv("AZURE_API_KEY", "")

    def __str__(self):
        return self.value


class LLMProvider:
    """Configuration for a specific LLM provider."""

    def __init__(
        self,
        name: LLMProviderName,
        api_key: ProviderAPIKey,
        models: List[LLMModel],
        default_model: LLMModel | None,
        temperature: float = 0.7,
        litellm_prefix: str | None = None,
        model_aliases: dict[str, str] | None = None,
        api_base: str | None = None,
    ):
        self._name: LLMProviderName = name
        self._api_key: ProviderAPIKey = api_key
        self._models: List[LLMModel] = models
        self._default_model: LLMModel | None = default_model
        self._temperature = temperature
        # litellm_prefix: the prefix LiteLLM expects (e.g. "anthropic" for Claude).
        # Defaults to the user-facing name when they match.
        self._litellm_prefix: str = litellm_prefix or name.value
        # model_aliases: maps user-facing model name to the actual API model ID.
        # Lets users write "gemini-1.5-pro" while we resolve to "gemini-1.5-pro-002".
        self._model_aliases: dict[str, str] = model_aliases or {}
        # api_base: custom endpoint URL (required for vLLM and other self-hosted servers).
        self._api_base: str | None = api_base

        # Only validate default_model membership when a fixed model list is defined.
        # Providers like vLLM accept any model name, so models=[] and default_model=None.
        if self._models and default_model is not None and default_model not in self._models:
            raise ValueError(
                f"Default model '{default_model}' not found in available models: {models}"
            )

    @property
    def name(self) -> LLMProviderName:
        return self._name

    @property
    def api_key(self) -> str:
        return self._api_key.value

    @property
    def api_base(self) -> str | None:
        return self._api_base

    @property
    def default_model(self) -> LLMModel | None:
        return self._default_model

    @property
    def temperature(self) -> float:
        return self._temperature

    @property
    def models(self) -> List[LLMModel]:
        return self._models

    def get_model_string(self, model_name: str) -> str:
        """Return the LiteLLM model string (prefix/model-id), resolving aliases."""
        resolved = self._model_aliases.get(model_name, model_name)
        if resolved != model_name:
            logger.debug("Model alias resolved: %s -> %s", model_name, resolved)
        return f"{self._litellm_prefix}/{resolved}"

    def __str__(self):
        return (
            f"LLMProvider(name={self.name.value}, "
            f"litellm_prefix={self._litellm_prefix}, "
            f"default_model={self.default_model.value})"
        )


class LLMProviderConfig:
    """Configuration manager for all LLM providers and their models."""

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai = LLMProvider(
        name=LLMProviderName.OPENAI,
        api_key=ProviderAPIKey.OPENAI,
        models=[
            LLMModel.GPT_5,
            LLMModel.GPT_5_MINI,
            LLMModel.GPT_5_NANO,
            LLMModel.GPT_4_1,
            LLMModel.GPT_4_1_MINI,
            LLMModel.GPT_4O,
            LLMModel.GPT_4O_MINI,
            LLMModel.GPT_4_5,
            LLMModel.O4_MINI,
            LLMModel.O3,
            LLMModel.O3_MINI,
            LLMModel.O1,
            LLMModel.O1_MINI,
            LLMModel.GPT_3_5_TURBO,
        ],
        default_model=LLMModel.GPT_4O_MINI,
    )

    # ── Anthropic / Claude ────────────────────────────────────────────────────
    # YAML uses llm_provider_name: claude  (user-friendly)
    # LiteLLM requires the "anthropic/" prefix in model strings
    claude = LLMProvider(
        name=LLMProviderName.CLAUDE,
        api_key=ProviderAPIKey.CLAUDE,
        litellm_prefix="anthropic",
        models=[
            LLMModel.CLAUDE_SONNET_4,
            LLMModel.CLAUDE_OPUS_4,
            LLMModel.CLAUDE_HAIKU_4_5,
            LLMModel.CLAUDE_3_7_SONNET,
            LLMModel.CLAUDE_3_5_SONNET,
            LLMModel.CLAUDE_3_5_HAIKU,
        ],
        default_model=LLMModel.CLAUDE_SONNET_4,
        model_aliases={
            # User-friendly names → versioned API IDs
            "claude-3-5-sonnet": "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku": "claude-3-5-haiku-20241022",
            "claude-3-7-sonnet": "claude-3-7-sonnet-20250219",
            "claude-opus-4": "claude-opus-4-20250514",
            "claude-sonnet-4": "claude-sonnet-4-20250514",
            "claude-haiku-4-5": "claude-haiku-4-5-20251001",
        },
    )

    # ── Gemini ────────────────────────────────────────────────────────────────
    # Availability (Google AI API with GEMINI_API_KEY):
    #   gemini-1.5-*         → shut down Sep 24–29 2025, do NOT use
    #   gemini-2.0-flash*    → deprecated, shutdown Jun 1 2026
    #   gemini-2.5-*         → stable GA, recommended
    #
    # model_aliases: if a user writes a deprecated name we forward to the
    # nearest current equivalent so they get a working call + a debug log.
    gemini = LLMProvider(
        name=LLMProviderName.GEMINI,
        api_key=ProviderAPIKey.GEMINI,
        models=[
            LLMModel.GEMINI_2_5_FLASH,
            LLMModel.GEMINI_2_5_FLASH_LITE,
            LLMModel.GEMINI_2_5_PRO,
            LLMModel.GEMINI_2_0_FLASH,
            LLMModel.GEMINI_2_0_FLASH_LITE,
            # 1.5 excluded — shut down; enum values kept above for YAML compat
        ],
        default_model=LLMModel.GEMINI_2_5_FLASH,
        model_aliases={
            # Dead 1.5 models → nearest current equivalent
            "gemini-1.5-pro":   "gemini-2.5-pro",
            "gemini-1.5-flash":  "gemini-2.5-flash",
        },
    )

    # ── Groq ──────────────────────────────────────────────────────────────────
    groq = LLMProvider(
        name=LLMProviderName.GROQ,
        api_key=ProviderAPIKey.GROQ,
        litellm_prefix="groq",
        models=[
            LLMModel.LLAMA_3_3_70B,
            LLMModel.LLAMA_3_1_8B,
            LLMModel.LLAMA3_70B,
            LLMModel.LLAMA3_8B,
            LLMModel.MIXTRAL_8X7B,
            LLMModel.GEMMA2_9B,
            LLMModel.QWEN_2_5_72B,
            LLMModel.QWEN_2_5_7B,
            LLMModel.QWEN_2_5_CODER_32B,
            LLMModel.LLAMA_4_SCOUT,
            LLMModel.LLAMA_4_MAVERICK,
        ],
        default_model=LLMModel.LLAMA_3_3_70B,
    )

    # ── DeepSeek ──────────────────────────────────────────────────────────────
    # DeepSeek V3 (deepseek-chat) and R1 (deepseek-reasoner) are among the most
    # widely-used open-weight models in 2025. LiteLLM routes via the "deepseek/"
    # prefix to DeepSeek's own API (api.deepseek.com).
    #
    # Required env var:
    #   DEEPSEEK_API_KEY  - from https://platform.deepseek.com/api_keys
    deepseek = LLMProvider(
        name=LLMProviderName.DEEPSEEK,
        api_key=ProviderAPIKey.DEEPSEEK,
        litellm_prefix="deepseek",
        models=[
            LLMModel.DEEPSEEK_V3,
            LLMModel.DEEPSEEK_R1,
        ],
        default_model=LLMModel.DEEPSEEK_V3,
    )

    # ── vLLM ──────────────────────────────────────────────────────────────────
    # vLLM exposes an OpenAI-compatible REST API. LiteLLM routes to it via the
    # "hosted_vllm/" prefix. The model name is whatever the vLLM server has
    # loaded (e.g. "meta-llama/Llama-3.1-8B-Instruct"), so no fixed model list
    # is enforced here — any string is accepted via LLMModel._missing_.
    #
    # Required env vars:
    #   VLLM_API_BASE  - vLLM server URL, e.g. http://localhost:8000 (default)
    #   VLLM_API_KEY   - API key if the server requires auth (default: "EMPTY")
    vllm = LLMProvider(
        name=LLMProviderName.VLLM,
        api_key=ProviderAPIKey.VLLM,
        litellm_prefix="hosted_vllm",
        models=[],          # open — any model name is valid
        default_model=None,  # no fixed default; user must specify llm_model in YAML
        api_base=os.getenv("VLLM_API_BASE", "http://localhost:8000"),
    )

    # ── OpenRouter ────────────────────────────────────────────────────────────
    # Routes many third-party models through one API. Model id is the OpenRouter
    # route (e.g. anthropic/claude-3.5-sonnet). LiteLLM: openrouter/<route>.
    # https://openrouter.ai/docs — env: OPENROUTER_API_KEY
    openrouter = LLMProvider(
        name=LLMProviderName.OPENROUTER,
        api_key=ProviderAPIKey.OPENROUTER,
        litellm_prefix="openrouter",
        models=[],  # large catalog — use llm_model as route string (LLMModel._missing_)
        default_model=None,
    )

    # ── Azure OpenAI ──────────────────────────────────────────────────────────
    # Azure hosts the same OpenAI models but behind your own Azure endpoint.
    # The llm_model value is your Azure *deployment name* (set when you deploy
    # a model in Azure AI Studio), so the model list is open — LLMModel._missing_
    # handles any string.  LiteLLM model string: azure/<deployment-name>.
    #
    # Required env vars:
    #   AZURE_API_KEY      - from Azure portal (API key)
    #   AZURE_API_BASE     - endpoint URL, e.g. https://my-resource.openai.azure.com/
    #   AZURE_API_VERSION  - API version, e.g. 2024-08-01-preview (read by LiteLLM automatically)
    azure = LLMProvider(
        name=LLMProviderName.AZURE,
        api_key=ProviderAPIKey.AZURE,
        litellm_prefix="azure",
        models=[],          # open — deployment names are user-defined in Azure portal
        default_model=None,
        api_base=os.getenv("AZURE_API_BASE", ""),
    )

    # Enum member → provider instance (O(1) lookup; extend when adding a provider)
    _PROVIDERS: ClassVar[dict[LLMProviderName, LLMProvider]] = {
        LLMProviderName.OPENAI: openai,
        LLMProviderName.CLAUDE: claude,
        LLMProviderName.GEMINI: gemini,
        LLMProviderName.GROQ: groq,
        LLMProviderName.DEEPSEEK: deepseek,
        LLMProviderName.VLLM: vllm,
        LLMProviderName.OPENROUTER: openrouter,
        LLMProviderName.AZURE: azure,
    }

    @staticmethod
    def get_llm_provider(llm_provider_name: LLMProviderName) -> LLMProvider:
        """Get provider configuration by name."""
        try:
            return LLMProviderConfig._PROVIDERS[llm_provider_name]
        except KeyError:
            raise ValueError(f"Unsupported provider: {llm_provider_name}") from None


if __name__ == "__main__":
    print(LLMProviderConfig.get_llm_provider(LLMProviderName.OPENAI))
    print(LLMProviderConfig.get_llm_provider(LLMProviderName.CLAUDE))
    print(LLMProviderConfig.get_llm_provider(LLMProviderName.GEMINI))
