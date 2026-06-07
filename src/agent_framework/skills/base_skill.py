"""Base class for all skills."""

from abc import abstractmethod
from typing import Any, Dict

from src.agent_framework.tools.base_tool import BaseTool


class BaseSkill(BaseTool):
    """Base class for user-defined and built-in skills.

    A skill is a reusable, pre-packaged capability that integrates with both
    the ADK and LangGraph engines via the existing tool adapter pipeline.
    Subclass this, implement ``run()``, and optionally define ``input_schema``
    (a Pydantic model) for structured LLM tool-call arguments.
    """

    skill_version: str = "1.0.0"

    def __init__(self, name: str, description: str, config: Dict[str, Any] = None):
        super().__init__(name=name, description=description)
        self.config: Dict[str, Any] = config or {}

    @abstractmethod
    def run(self, input: str) -> str:
        """Execute the skill. Input is a JSON string or plain text."""
