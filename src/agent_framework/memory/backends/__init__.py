"""Memory backend adapters.

Each module here implements the `LongTermMemory` ABC for one backend.
Adapters are constructed by `MemoryFactory`; agent code never imports
them directly.
"""

from src.agent_framework.memory.backends.mem0_platform import Mem0PlatformMemory

__all__ = ["Mem0PlatformMemory"]
