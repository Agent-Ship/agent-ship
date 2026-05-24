"""Unit tests for `MemoryMiddleware`.

These tests don't require Mem0 or any network — the `LongTermMemory`
contract is faked with a small AsyncMock-driven double.

What's covered:
  - Recall happy path: search runs, formatted block lands in request_context
  - Recall skip paths: disabled, anonymous, empty query, no results,
    request_context=None
  - Recall failure path: backend exception is swallowed and logged
  - Write happy path: sync mode awaits add(), async mode schedules a task
  - Write skip paths: disabled, anonymous, empty turn
  - Write failure path: sync error caught + logged
  - Query extraction: default fields (text/query/message/prompt) and the
    explicit `recall.query_field` override
  - Response extraction: str / pydantic model / dict / None / arbitrary
  - Recall block format: preamble + bulleted memories
  - Scope construction: user_id, agent_id from agent name, session_id
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from src.agent_framework.configs.memory import (
    MemoryBackend,
    MemoryConfig,
    MemoryRecallConfig,
    MemoryWriteConfig,
)
from src.agent_framework.memory.base import (
    LongTermMemory,
    MemoryRecord,
    MemoryScope,
)
from src.agent_framework.middleware.memory_middleware import (
    MEMORY_RECALL_KEY,
    MEMORY_RECALL_PREAMBLE,
    MemoryMiddleware,
    _first_string_field,
    _safe_json,
)


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _make_memory(search_returns=None, search_raises=None) -> LongTermMemory:
    """Build a `LongTermMemory` double whose key methods are AsyncMocks.

    `search` can be told to return a list (default empty) or raise. `add`
    always succeeds. Other methods are MagicMock stubs.
    """
    mem = MagicMock(spec=LongTermMemory)
    mem.search = AsyncMock(side_effect=search_raises) if search_raises else AsyncMock(return_value=search_returns or [])
    mem.add = AsyncMock(return_value=[])
    mem.get = AsyncMock(return_value=None)
    mem.get_all = AsyncMock(return_value=[])
    mem.update = AsyncMock()
    mem.delete = AsyncMock()
    mem.delete_scope = AsyncMock(return_value=0)
    mem.history = AsyncMock(return_value=[])
    return mem


def _make_config(
    *,
    enabled: bool = True,
    recall_enabled: bool = True,
    write_enabled: bool = True,
    write_async: bool = False,
    top_k: int = 6,
    threshold: float = 0.7,
    query_field: Optional[str] = None,
) -> MemoryConfig:
    """Build a fully-populated MemoryConfig for tests."""
    return MemoryConfig(
        enabled=enabled,
        backend=MemoryBackend.MEM0_PLATFORM,
        recall=MemoryRecallConfig(
            enabled=recall_enabled,
            top_k=top_k,
            threshold=threshold,
            query_field=query_field,
        ),
        # Use the YAML alias (`async`) so we exercise the same field shape
        # the agent loader produces.
        write=MemoryWriteConfig.model_validate({"enabled": write_enabled, "async": write_async}),
    )


def _make_middleware(
    memory: LongTermMemory,
    config: Optional[MemoryConfig] = None,
    agent_name: str = "demo_agent",
) -> MemoryMiddleware:
    return MemoryMiddleware(
        memory=memory,
        config=config or _make_config(),
        agent_name=agent_name,
    )


def _make_record(text: str = "User lives in Bangalore", **overrides) -> MemoryRecord:
    base = dict(
        id="m-1",
        text=text,
        kind="factual",
        scope=MemoryScope(user_id="alice"),
        metadata={},
        score=0.42,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    base.update(overrides)
    return MemoryRecord(**base)


class TextInput(BaseModel):
    """Mirrors the framework's default input schema (one `text` field)."""

    text: str


class CustomInput(BaseModel):
    """Input schema without a default-named field — needs the override."""

    user_question: str


class CustomOutput(BaseModel):
    response: str


# ---------------------------------------------------------------------------
# before_run — recall
# ---------------------------------------------------------------------------


class TestBeforeRunHappyPath:
    @pytest.mark.asyncio
    async def test_writes_formatted_block_into_request_context_on_hit(self):
        memory = _make_memory(search_returns=[_make_record("Lives in Bangalore"), _make_record("Partner is Tanya")])
        mw = _make_middleware(memory)
        ctx: dict[str, Any] = {}

        result = await mw.before_run(
            user_id="alice",
            session_id="s1",
            input_data=TextInput(text="where do I live?"),
            request_context=ctx,
        )

        assert result.text == "where do I live?"   # input is returned unchanged
        assert MEMORY_RECALL_KEY in ctx
        block = ctx[MEMORY_RECALL_KEY]
        assert block.startswith(MEMORY_RECALL_PREAMBLE)
        assert "- Lives in Bangalore" in block
        assert "- Partner is Tanya" in block

    @pytest.mark.asyncio
    async def test_search_is_called_with_correct_scope_and_tuning(self):
        memory = _make_memory(search_returns=[])
        mw = _make_middleware(
            memory,
            config=_make_config(top_k=3, threshold=0.5),
            agent_name="my_agent",
        )

        await mw.before_run(
            user_id="alice",
            session_id="s1",
            input_data=TextInput(text="hello"),
            request_context={},
        )

        memory.search.assert_awaited_once()
        call_query = memory.search.await_args.args[0]
        assert call_query.query == "hello"
        assert call_query.limit == 3
        assert call_query.threshold == 0.5
        assert call_query.scope.user_id == "alice"
        assert call_query.scope.agent_id == "my_agent"
        assert call_query.scope.session_id == "s1"   # carried for traceability


class TestBeforeRunSkipPaths:
    @pytest.mark.asyncio
    async def test_skipped_when_recall_disabled(self):
        memory = _make_memory()
        mw = _make_middleware(memory, config=_make_config(recall_enabled=False))
        ctx: dict[str, Any] = {}

        await mw.before_run(
            user_id="alice",
            session_id="s1",
            input_data=TextInput(text="hi"),
            request_context=ctx,
        )

        memory.search.assert_not_awaited()
        assert MEMORY_RECALL_KEY not in ctx

    @pytest.mark.asyncio
    async def test_skipped_when_user_id_is_empty(self):
        memory = _make_memory()
        mw = _make_middleware(memory)
        ctx: dict[str, Any] = {}

        await mw.before_run(
            user_id="",
            session_id="s1",
            input_data=TextInput(text="hi"),
            request_context=ctx,
        )

        memory.search.assert_not_awaited()
        assert MEMORY_RECALL_KEY not in ctx

    @pytest.mark.asyncio
    async def test_skipped_when_query_text_is_empty(self):
        memory = _make_memory()
        mw = _make_middleware(memory)
        ctx: dict[str, Any] = {}

        await mw.before_run(
            user_id="alice",
            session_id="s1",
            input_data=TextInput(text=""),
            request_context=ctx,
        )

        memory.search.assert_not_awaited()
        assert MEMORY_RECALL_KEY not in ctx

    @pytest.mark.asyncio
    async def test_no_block_when_no_results(self):
        memory = _make_memory(search_returns=[])
        mw = _make_middleware(memory)
        ctx: dict[str, Any] = {}

        await mw.before_run(
            user_id="alice",
            session_id="s1",
            input_data=TextInput(text="hi"),
            request_context=ctx,
        )

        memory.search.assert_awaited_once()   # search ran
        assert MEMORY_RECALL_KEY not in ctx   # but nothing was written

    @pytest.mark.asyncio
    async def test_no_crash_when_request_context_is_none(self):
        memory = _make_memory(search_returns=[_make_record()])
        mw = _make_middleware(memory)

        # Doesn't raise — middleware just has nowhere to put the block.
        result = await mw.before_run(
            user_id="alice",
            session_id="s1",
            input_data=TextInput(text="hi"),
            request_context=None,
        )
        assert result.text == "hi"


class TestBeforeRunErrorHandling:
    @pytest.mark.asyncio
    async def test_backend_exception_is_swallowed(self):
        memory = _make_memory(search_raises=RuntimeError("mem0 is down"))
        mw = _make_middleware(memory)
        ctx: dict[str, Any] = {}

        result = await mw.before_run(
            user_id="alice",
            session_id="s1",
            input_data=TextInput(text="hi"),
            request_context=ctx,
        )

        # Request proceeds without recall; no exception bubbles up.
        assert result.text == "hi"
        assert MEMORY_RECALL_KEY not in ctx


# ---------------------------------------------------------------------------
# Query extraction
# ---------------------------------------------------------------------------


class TestQueryExtraction:
    @pytest.mark.asyncio
    async def test_default_field_text(self):
        memory = _make_memory()
        mw = _make_middleware(memory)
        await mw.before_run(
            user_id="alice", session_id="s1",
            input_data=TextInput(text="lookup-text"),
            request_context={},
        )
        assert memory.search.await_args.args[0].query == "lookup-text"

    @pytest.mark.asyncio
    async def test_default_field_query(self):
        class Q(BaseModel):
            query: str

        memory = _make_memory()
        mw = _make_middleware(memory)
        await mw.before_run(
            user_id="alice", session_id="s1",
            input_data=Q(query="lookup-query"),
            request_context={},
        )
        assert memory.search.await_args.args[0].query == "lookup-query"

    @pytest.mark.asyncio
    async def test_default_field_message(self):
        class M(BaseModel):
            message: str

        memory = _make_memory()
        mw = _make_middleware(memory)
        await mw.before_run(
            user_id="alice", session_id="s1",
            input_data=M(message="lookup-message"),
            request_context={},
        )
        assert memory.search.await_args.args[0].query == "lookup-message"

    @pytest.mark.asyncio
    async def test_explicit_query_field_override(self):
        memory = _make_memory()
        cfg = _make_config(query_field="user_question")
        mw = _make_middleware(memory, config=cfg)

        await mw.before_run(
            user_id="alice", session_id="s1",
            input_data=CustomInput(user_question="custom-field-value"),
            request_context={},
        )

        assert memory.search.await_args.args[0].query == "custom-field-value"

    @pytest.mark.asyncio
    async def test_falls_back_to_json_dump_when_no_default_field_present(self):
        """Input has none of `text`/`query`/`message`/`prompt` — last resort
        is a JSON dump so the search still runs (even if poorly)."""

        class Bag(BaseModel):
            foo: str
            bar: int

        memory = _make_memory()
        mw = _make_middleware(memory)
        await mw.before_run(
            user_id="alice", session_id="s1",
            input_data=Bag(foo="x", bar=7),
            request_context={},
        )

        query = memory.search.await_args.args[0].query
        assert '"foo"' in query and '"bar"' in query


# ---------------------------------------------------------------------------
# after_run — write
# ---------------------------------------------------------------------------


class TestAfterRunSyncWrite:
    @pytest.mark.asyncio
    async def test_sync_write_persists_user_and_assistant_turns(self):
        memory = _make_memory()
        cfg = _make_config(write_async=False)
        mw = _make_middleware(memory, config=cfg, agent_name="demo")

        await mw.after_run(
            user_id="alice", session_id="s1",
            input_data=TextInput(text="hi"),
            output_data=CustomOutput(response="hello back"),
            request_context={},
        )

        memory.add.assert_awaited_once()
        write_arg, scope_arg = memory.add.await_args.args
        assert write_arg.source == "user_turn"
        assert write_arg.messages == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello back"},
        ]
        assert scope_arg.user_id == "alice"
        assert scope_arg.agent_id == "demo"
        assert scope_arg.session_id == "s1"

    @pytest.mark.asyncio
    async def test_sync_write_failure_is_swallowed(self):
        memory = _make_memory()
        memory.add = AsyncMock(side_effect=RuntimeError("write failed"))
        cfg = _make_config(write_async=False)
        mw = _make_middleware(memory, config=cfg)

        # Doesn't raise.
        await mw.after_run(
            user_id="alice", session_id="s1",
            input_data=TextInput(text="hi"),
            output_data="reply",
            request_context={},
        )

        memory.add.assert_awaited_once()


class TestAfterRunAsyncWrite:
    @pytest.mark.asyncio
    async def test_async_write_schedules_a_task(self):
        memory = _make_memory()
        cfg = _make_config(write_async=True)
        mw = _make_middleware(memory, config=cfg)

        await mw.after_run(
            user_id="alice", session_id="s1",
            input_data=TextInput(text="hi"),
            output_data="reply",
            request_context={},
        )

        # `create_task` should have been called immediately — the actual
        # `add` may or may not have run yet, depending on scheduler. Drain
        # pending tasks so the assertion below is deterministic.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        memory.add.assert_awaited_once()


class TestAfterRunSkipPaths:
    @pytest.mark.asyncio
    async def test_skipped_when_write_disabled(self):
        memory = _make_memory()
        cfg = _make_config(write_enabled=False)
        mw = _make_middleware(memory, config=cfg)

        await mw.after_run(
            user_id="alice", session_id="s1",
            input_data=TextInput(text="hi"),
            output_data="reply",
            request_context={},
        )
        memory.add.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skipped_when_anonymous(self):
        memory = _make_memory()
        mw = _make_middleware(memory)

        await mw.after_run(
            user_id="",
            session_id="s1",
            input_data=TextInput(text="hi"),
            output_data="reply",
            request_context={},
        )
        memory.add.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skipped_when_query_or_response_empty(self):
        memory = _make_memory()
        mw = _make_middleware(memory, config=_make_config(write_async=False))

        # Empty query
        await mw.after_run(
            user_id="alice", session_id="s1",
            input_data=TextInput(text=""),
            output_data="reply",
            request_context={},
        )
        # Empty response
        await mw.after_run(
            user_id="alice", session_id="s1",
            input_data=TextInput(text="hi"),
            output_data="",
            request_context={},
        )
        # None response
        await mw.after_run(
            user_id="alice", session_id="s1",
            input_data=TextInput(text="hi"),
            output_data=None,
            request_context={},
        )

        memory.add.assert_not_awaited()


# ---------------------------------------------------------------------------
# Response extraction
# ---------------------------------------------------------------------------


class TestResponseExtraction:
    @pytest.mark.asyncio
    async def test_string_output_used_verbatim(self):
        memory = _make_memory()
        mw = _make_middleware(memory, config=_make_config(write_async=False))
        await mw.after_run(
            user_id="alice", session_id="s1",
            input_data=TextInput(text="hi"),
            output_data="raw streamed text",
            request_context={},
        )
        assert memory.add.await_args.args[0].messages[1]["content"] == "raw streamed text"

    @pytest.mark.asyncio
    async def test_pydantic_output_uses_response_field(self):
        memory = _make_memory()
        mw = _make_middleware(memory, config=_make_config(write_async=False))
        await mw.after_run(
            user_id="alice", session_id="s1",
            input_data=TextInput(text="hi"),
            output_data=CustomOutput(response="model reply"),
            request_context={},
        )
        assert memory.add.await_args.args[0].messages[1]["content"] == "model reply"

    @pytest.mark.asyncio
    async def test_dict_output_uses_response_key(self):
        memory = _make_memory()
        mw = _make_middleware(memory, config=_make_config(write_async=False))
        await mw.after_run(
            user_id="alice", session_id="s1",
            input_data=TextInput(text="hi"),
            output_data={"response": "dict reply"},
            request_context={},
        )
        assert memory.add.await_args.args[0].messages[1]["content"] == "dict reply"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestModuleHelpers:
    def test_first_string_field_returns_first_present(self):
        data = {"text": None, "query": "found", "message": "also-present"}
        assert _first_string_field(data, ("text", "query", "message")) == "found"

    def test_first_string_field_empty_when_none_present(self):
        assert _first_string_field({"x": 1}, ("text", "query")) == ""

    def test_safe_json_falls_back_to_str_on_unserialisable(self):
        class NotSerialisable:
            def __repr__(self):
                return "<NotSerialisable instance>"

        out = _safe_json({"obj": NotSerialisable()})
        # default=str runs first, so it serialises via str(NotSerialisable())
        assert "NotSerialisable" in out


# ---------------------------------------------------------------------------
# Recall block formatting
# ---------------------------------------------------------------------------


class TestRecallBlockFormat:
    @pytest.mark.asyncio
    async def test_block_starts_with_trust_framing_preamble(self):
        memory = _make_memory(search_returns=[_make_record("one"), _make_record("two")])
        mw = _make_middleware(memory)
        ctx: dict[str, Any] = {}

        await mw.before_run(
            user_id="alice", session_id="s1",
            input_data=TextInput(text="lookup"),
            request_context=ctx,
        )

        block = ctx[MEMORY_RECALL_KEY]
        assert block.startswith(MEMORY_RECALL_PREAMBLE)
        # Every recalled memory becomes one bullet.
        assert block.count("\n- ") == 2
