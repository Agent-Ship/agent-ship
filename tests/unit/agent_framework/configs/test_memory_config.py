"""Unit tests for memory configuration.

What's covered:
  - MemoryConfig YAML schema: defaults, backend-required-when-enabled validator,
    Enum rejection of unknown backend names.
  - Mem0PlatformSettings.from_env(): explicit env-to-field mapping, required-key
    enforcement, optional-url passthrough.

No network, no SDK imports.
"""

import pytest
from pydantic import ValidationError

from src.agent_framework.configs.memory import (
    Mem0PlatformSettings,
    MemoryBackend,
    MemoryConfig,
)


# ---------------------------------------------------------------------------
# MemoryConfig
# ---------------------------------------------------------------------------


class TestMemoryConfig:
    def test_default_is_disabled_no_backend(self):
        cfg = MemoryConfig()
        assert cfg.enabled is False
        assert cfg.backend is None

    def test_enabled_without_backend_is_rejected(self):
        with pytest.raises(ValidationError, match="memory.backend is required"):
            MemoryConfig(enabled=True)

    def test_enabled_with_supported_backend(self):
        cfg = MemoryConfig(enabled=True, backend="mem0_platform")
        assert cfg.backend == MemoryBackend.MEM0_PLATFORM

    def test_unsupported_backend_is_rejected_at_parse_time(self):
        """Pydantic's Enum validation rejects unknown values up front."""
        with pytest.raises(ValidationError):
            MemoryConfig(enabled=True, backend="not_a_backend")

    def test_disabled_with_no_backend_is_valid(self):
        # No validation fires on the backend field when memory is disabled.
        cfg = MemoryConfig(enabled=False)
        assert cfg.enabled is False

    def test_recall_defaults(self):
        cfg = MemoryConfig()
        assert cfg.recall.enabled is True
        assert cfg.recall.top_k == 6
        assert cfg.recall.threshold == 0.7

    def test_recall_validates_top_k_positive(self):
        with pytest.raises(ValidationError):
            MemoryConfig(recall={"top_k": 0})

    def test_recall_validates_threshold_range(self):
        with pytest.raises(ValidationError):
            MemoryConfig(recall={"threshold": 1.5})

    def test_write_async_alias(self):
        # YAML uses `async:` (a Python keyword), aliased to is_async internally.
        cfg = MemoryConfig(write={"async": False})
        assert cfg.write.is_async is False


# ---------------------------------------------------------------------------
# Mem0PlatformSettings.from_env
# ---------------------------------------------------------------------------


class TestMem0PlatformSettingsFromEnv:
    def test_reads_required_key(self, monkeypatch):
        monkeypatch.setenv("AGENT_LTM_MEM0_PLATFORM_API_KEY", "sk-test")
        monkeypatch.delenv("AGENT_LTM_MEM0_PLATFORM_API_URL", raising=False)

        settings = Mem0PlatformSettings.from_env()
        assert settings.api_key == "sk-test"
        assert settings.api_url is None

    def test_reads_optional_url(self, monkeypatch):
        monkeypatch.setenv("AGENT_LTM_MEM0_PLATFORM_API_KEY", "sk-test")
        monkeypatch.setenv(
            "AGENT_LTM_MEM0_PLATFORM_API_URL", "https://mem0.example.com"
        )

        settings = Mem0PlatformSettings.from_env()
        assert settings.api_url == "https://mem0.example.com"

    def test_raises_when_required_key_missing(self, monkeypatch):
        monkeypatch.delenv("AGENT_LTM_MEM0_PLATFORM_API_KEY", raising=False)

        with pytest.raises(ValueError, match="AGENT_LTM_MEM0_PLATFORM_API_KEY"):
            Mem0PlatformSettings.from_env()

    def test_raises_when_required_key_is_empty_string(self, monkeypatch):
        monkeypatch.setenv("AGENT_LTM_MEM0_PLATFORM_API_KEY", "")

        with pytest.raises(ValueError, match="AGENT_LTM_MEM0_PLATFORM_API_KEY"):
            Mem0PlatformSettings.from_env()
