"""Configuration system for AI-Ecosystem.

Holds Pydantic config classes that the framework loads at startup.

Structure:
- agent_config.py : Agent behavior and capabilities (YAML schema).
- loader.py       : Utility loaders for agent configuration.
- memory/         : Memory configuration — YAML schema + per-backend env Settings.
- llm/            : LLM provider configurations and enums.
- opik_config.py  : Observability (Opik) settings.
"""

from .agent_config import AgentConfig, ExecutionEngine, StreamingMode
from .memory import MemoryBackend, MemoryConfig
from .loader import load_agent_config

__all__ = [
    "AgentConfig",
    "ExecutionEngine",
    "StreamingMode",
    "MemoryConfig",
    "MemoryBackend",
    "load_agent_config",
]
