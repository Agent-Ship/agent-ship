"""Confirm the memory-recall block actually lands in the prompt sent to the LLM.

Two engines, two injection paths:

- **ADK**: prepends the recall block to the user-turn message text. The
  system instruction is baked at agent-construction time, so per-call
  injection has to happen on the user-turn side. Tested via the engine's
  static `_prepend_memory_recall` helper — that's the only behaviour
  request_context drives in the ADK path.

- **LangGraph**: appends the recall block to the base instruction inside
  `_build_system_prompt(request_context)`. The resulting system message
  is the first entry in the list sent to LiteLLM. We exercise the prompt
  builder directly with a minimal engine instance (no real LiteLLM, no
  real ADK SDK).
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest

from src.agent_framework.engines.adk.engine import AdkEngine
from src.agent_framework.middleware.memory_middleware import (
    MEMORY_RECALL_KEY,
    MEMORY_RECALL_PREAMBLE,
)


# ---------------------------------------------------------------------------
# ADK
# ---------------------------------------------------------------------------


class TestAdkPrependMemoryRecall:
    """`AdkEngine._prepend_memory_recall` is the single injection point.

    Tests target the static method directly so no ADK SDK / session store
    construction is needed.
    """

    def test_returns_input_unchanged_when_request_context_is_none(self):
        result = AdkEngine._prepend_memory_recall("user-input", None)
        assert result == "user-input"

    def test_returns_input_unchanged_when_request_context_empty(self):
        result = AdkEngine._prepend_memory_recall("user-input", {})
        assert result == "user-input"

    def test_returns_input_unchanged_when_recall_block_missing(self):
        ctx = {"agent_name": "demo", "some_other_key": "value"}
        result = AdkEngine._prepend_memory_recall("user-input", ctx)
        assert result == "user-input"

    def test_prepends_block_with_separator_when_present(self):
        ctx = {MEMORY_RECALL_KEY: "<recalled facts>"}
        result = AdkEngine._prepend_memory_recall('{"text":"hi"}', ctx)

        # The block comes first, then a separating blank line, then the
        # original user-turn content.
        assert result.startswith("<recalled facts>")
        assert result.endswith('{"text":"hi"}')
        # No content gets lost between them.
        assert "<recalled facts>\n\n" in result

    def test_block_carrying_real_preamble_lands_intact(self):
        block = f"{MEMORY_RECALL_PREAMBLE}\n\n- User lives in Bangalore"
        ctx = {MEMORY_RECALL_KEY: block}
        result = AdkEngine._prepend_memory_recall("hi", ctx)
        # Preamble's first sentence is what tells the LLM how to treat
        # the block — that text must be intact in the final output.
        assert "notes about this user from previous conversations" in result


# ---------------------------------------------------------------------------
# LangGraph
# ---------------------------------------------------------------------------


def _make_minimal_langgraph_engine_for_prompt_test():
    """Build a `LangGraphEngine` shell sufficient for `_build_system_prompt`.

    Importing `LangGraphEngine` pulls heavy deps (litellm, langgraph,
    session store). We use `__new__` to skip the constructor and stub
    only the attributes `_build_system_prompt` reads:
      - `agent_config.instruction_template`
      - `self._tools`
      - `self.output_schema`
    """
    from src.agent_framework.engines.langgraph.engine import LangGraphEngine

    engine = LangGraphEngine.__new__(LangGraphEngine)
    engine.agent_config = MagicMock()
    engine.agent_config.instruction_template = "You are a helpful assistant."
    engine._tools = []
    # output_schema must have a model_json_schema() method for
    # build_schema_prompt — use a real Pydantic model.
    from pydantic import BaseModel

    class _Out(BaseModel):
        response: str

    engine.output_schema = _Out
    return engine


class TestLangGraphBuildSystemPrompt:
    def test_no_recall_block_when_request_context_is_none(self):
        engine = _make_minimal_langgraph_engine_for_prompt_test()
        prompt = engine._build_system_prompt(None)

        assert "You are a helpful assistant." in prompt
        # Preamble must NOT appear when there's no recall.
        assert MEMORY_RECALL_PREAMBLE not in prompt

    def test_no_recall_block_when_recall_key_absent(self):
        engine = _make_minimal_langgraph_engine_for_prompt_test()
        prompt = engine._build_system_prompt({"agent_name": "demo"})

        assert "You are a helpful assistant." in prompt
        assert MEMORY_RECALL_PREAMBLE not in prompt

    def test_recall_block_is_appended_to_base_instruction(self):
        engine = _make_minimal_langgraph_engine_for_prompt_test()
        block = f"{MEMORY_RECALL_PREAMBLE}\n\n- User lives in Bangalore"
        prompt = engine._build_system_prompt({MEMORY_RECALL_KEY: block})

        # Base instruction still present.
        assert "You are a helpful assistant." in prompt
        # Recall block + its preamble are inside the prompt.
        assert MEMORY_RECALL_PREAMBLE in prompt
        assert "- User lives in Bangalore" in prompt
        # Recall block comes AFTER the base instruction, not before.
        assert prompt.index("You are a helpful assistant.") < prompt.index(MEMORY_RECALL_PREAMBLE)
