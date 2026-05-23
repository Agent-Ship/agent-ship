"""Memory configuration.

Two kinds of classes live here:

1. Agent-facing YAML schema — `MemoryConfig` and its sub-models. These are
   what an agent author writes under the `memory:` block in main_agent.yaml.
   No env reads, no secrets.

2. Per-backend env Settings — one class per supported backend. Each has a
   `from_env()` classmethod that reads its env vars explicitly and validates
   them with Pydantic. No `env_prefix` magic — the env-var name appears
   right next to the field it populates, so the mapping is readable and
   directly unit-testable.

Today only `mem0_platform` is supported. When new backends ship, add their
Settings class here (or split into sibling files if this file grows large).
"""

import os
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Agent-facing YAML schema
# ---------------------------------------------------------------------------


class MemoryBackend(str, Enum):
    """Long-term memory backends supported by the framework.

    Only entries listed here are accepted by Pydantic at YAML parse time.
    Add a new value when its adapter ships in `memory/backends/`.
    """

    MEM0_PLATFORM = "mem0_platform"


class MemoryRecallConfig(BaseModel):
    enabled: bool = True
    top_k: int = Field(default=6, gt=0)
    threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    query_field: Optional[str] = None


class MemoryWriteConfig(BaseModel):
    enabled: bool = True
    is_async: bool = Field(default=True, alias="async")


class MemoryConfig(BaseModel):
    """What an agent author writes under `memory:` in main_agent.yaml."""

    enabled: bool = False
    backend: Optional[MemoryBackend] = None
    recall: MemoryRecallConfig = Field(default_factory=MemoryRecallConfig)
    write: MemoryWriteConfig = Field(default_factory=MemoryWriteConfig)

    @model_validator(mode="after")
    def _require_backend_when_enabled(self) -> "MemoryConfig":
        if self.enabled and self.backend is None:
            raise ValueError(
                "memory.backend is required when memory.enabled=true"
            )
        return self


# ---------------------------------------------------------------------------
# Per-backend env Settings
# ---------------------------------------------------------------------------


class Mem0PlatformSettings(BaseModel):
    """Connection settings for the Mem0 Platform (hosted SaaS) backend.

    Constructed by `from_env()` when an agent selects `mem0_platform`. The
    factory calls this; the adapter receives the validated instance.

    Env vars:
    - AGENT_LTM_MEM0_PLATFORM_API_KEY  (required) — Mem0 Platform API key
    - AGENT_LTM_MEM0_PLATFORM_API_URL  (optional) — override for self-hosted Mem0
    """

    api_key: str
    api_url: Optional[str] = None

    @classmethod
    def from_env(cls) -> "Mem0PlatformSettings":
        """Build settings from the process environment.

        - `AGENT_LTM_MEM0_PLATFORM_API_KEY` is required; missing or empty
          raises with a clear message.
        - `AGENT_LTM_MEM0_PLATFORM_API_URL` is optional. An empty string
          is treated the same as unset so the Mem0 SDK falls back to its
          default hosted endpoint — passing `host=""` would break httpx
          with an "URL missing protocol" error.
        """
        api_key = os.environ.get("AGENT_LTM_MEM0_PLATFORM_API_KEY")
        if not api_key:
            raise ValueError(
                "AGENT_LTM_MEM0_PLATFORM_API_KEY is required for the "
                "mem0_platform backend. Set it in .env or the process env."
            )
        # `... or None` collapses missing/empty into None so downstream
        # code only has to deal with "None vs valid string", never "".
        api_url = os.environ.get("AGENT_LTM_MEM0_PLATFORM_API_URL") or None
        return cls(api_key=api_key, api_url=api_url)
