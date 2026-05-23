"""Memory backend factory.

Single entry point that turns a YAML-level `MemoryConfig` into a live
`LongTermMemory` adapter. Dispatches on `MemoryConfig.backend`; each branch
is responsible for loading the backend's env-driven Settings and passing
them to the adapter constructor.
"""

from typing import Optional

from src.agent_framework.configs.memory import (
    Mem0PlatformSettings,
    MemoryBackend,
    MemoryConfig,
)
from src.agent_framework.memory.backends.mem0_platform import Mem0PlatformMemory
from src.agent_framework.memory.base import LongTermMemory


class MemoryFactory:
    """Constructs `LongTermMemory` adapters from agent-level config.

    The factory itself contains no validation logic — it just dispatches.
    Validation happens in the layers it composes: Pydantic validates the
    YAML schema on `MemoryConfig`; each backend's `Settings.from_env()`
    validates env vars at construction time.
    """

    @staticmethod
    def create(memory_config: MemoryConfig) -> Optional[LongTermMemory]:
        """Build the adapter the agent will use, or None if memory is off.

        Args:
            memory_config: Parsed `memory:` block from the agent's YAML.
                When `enabled=False` (the default), no backend is constructed
                and None is returned — the agent runs with no memory.

        Returns:
            A `LongTermMemory` instance for the selected backend, or None
            when memory is disabled.

        Raises:
            ValueError: When `memory.enabled=true` but `memory.backend`
                somehow has a value that wasn't caught by Pydantic's Enum
                validation (should be unreachable under normal use).
            Other exceptions from each backend's `Settings.from_env()`
            propagate — e.g. `AGENT_LTM_MEM0_PLATFORM_API_KEY` missing.
        """
        if not memory_config.enabled:
            return None

        if memory_config.backend == MemoryBackend.MEM0_PLATFORM:
            settings = Mem0PlatformSettings.from_env()
            return Mem0PlatformMemory(settings=settings)

        # Unreachable under normal use: Pydantic's Enum validation on
        # MemoryConfig.backend rejects any other value at YAML parse time.
        raise ValueError(f"Unknown memory backend: {memory_config.backend!r}")
