"""Unit tests for the Mem0 Platform adapter.

These tests do NOT require the `mem0ai` package or any network access — the
SDK is mocked via dependency injection in the adapter's constructor.

What's covered:
  - Pure conversion functions: _to_record, _parse_dt, _extract_rows
  - Adapter method dispatch with a mock Mem0 client
  - The key invariant: session_id is in metadata only, never in Mem0 scope kwargs
  - Input validation: delete_scope refuses scope-less deletion; add() requires
    either messages or facts
"""

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from src.agent_framework.configs.memory import Mem0PlatformSettings
from src.agent_framework.memory.backends.mem0_platform import (
    Mem0PlatformMemory,
    _extract_rows,
    _parse_dt,
)
from src.agent_framework.memory.base import (
    MemoryRecord,
    MemoryScope,
    MemorySearchQuery,
    MemoryWrite,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings() -> Mem0PlatformSettings:
    return Mem0PlatformSettings(api_key="sk-test", api_url=None)


def _make_adapter(mock_client: Mock) -> Mem0PlatformMemory:
    return Mem0PlatformMemory(settings=_make_settings(), client=mock_client)


def _record_dict(**overrides) -> dict:
    base = {
        "id": "mem-1",
        "memory": "the user lives in Bangalore",
        "user_id": "u1",
        "agent_id": "a1",
        "score": 0.92,
        "created_at": "2026-05-17T10:00:00Z",
        "updated_at": "2026-05-17T10:00:00Z",
        "metadata": {"source": "user_turn", "session_id": "s1"},
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _parse_dt — pure function
# ---------------------------------------------------------------------------


class TestParseDt:
    def test_parses_iso_string_with_z(self):
        dt = _parse_dt("2026-05-17T10:00:00Z")
        assert dt.year == 2026 and dt.month == 5 and dt.day == 17

    def test_returns_datetime_unchanged(self):
        ref = datetime(2026, 5, 17, tzinfo=timezone.utc)
        assert _parse_dt(ref) is ref

    def test_returns_now_for_none(self):
        result = _parse_dt(None)
        assert isinstance(result, datetime)

    def test_returns_now_for_garbage_string(self):
        result = _parse_dt("not-a-date")
        assert isinstance(result, datetime)


# ---------------------------------------------------------------------------
# _extract_rows — pure function
# ---------------------------------------------------------------------------


class TestExtractRows:
    def test_extracts_from_results_envelope(self):
        assert _extract_rows({"results": [{"id": "a"}, {"id": "b"}]}) == [
            {"id": "a"},
            {"id": "b"},
        ]

    def test_passes_through_bare_list(self):
        rows = [{"id": "a"}]
        assert _extract_rows(rows) == rows

    def test_returns_empty_for_dict_without_results(self):
        assert _extract_rows({"foo": "bar"}) == []

    def test_returns_empty_for_garbage(self):
        assert _extract_rows(None) == []
        assert _extract_rows("string") == []

    def test_returns_empty_when_results_is_not_a_list(self):
        assert _extract_rows({"results": "not a list"}) == []


# ---------------------------------------------------------------------------
# _to_record — staticmethod, pure conversion
# ---------------------------------------------------------------------------


class TestToRecord:
    def test_happy_path(self):
        rec = Mem0PlatformMemory._to_record(_record_dict())
        assert isinstance(rec, MemoryRecord)
        assert rec.id == "mem-1"
        assert rec.text == "the user lives in Bangalore"
        assert rec.scope.user_id == "u1"
        assert rec.scope.session_id == "s1"  # pulled from metadata
        assert rec.metadata["source"] == "user_turn"
        assert rec.score == 0.92

    def test_defaults_kind_to_factual_when_metadata_lacks_it(self):
        rec = Mem0PlatformMemory._to_record(
            _record_dict(metadata={"session_id": "s1"})
        )
        assert rec is not None
        assert rec.kind == "factual"

    def test_uses_kind_from_metadata_when_present(self):
        rec = Mem0PlatformMemory._to_record(
            _record_dict(metadata={"kind": "episodic", "session_id": "s1"})
        )
        assert rec is not None
        assert rec.kind == "episodic"

    def test_accepts_memory_id_as_id_fallback(self):
        raw = _record_dict()
        del raw["id"]
        raw["memory_id"] = "mem-2"
        rec = Mem0PlatformMemory._to_record(raw)
        assert rec is not None
        assert rec.id == "mem-2"

    def test_returns_none_when_id_and_memory_id_both_missing(self):
        raw = _record_dict()
        del raw["id"]
        assert Mem0PlatformMemory._to_record(raw) is None

    def test_returns_none_when_text_missing(self):
        raw = _record_dict()
        del raw["memory"]
        assert Mem0PlatformMemory._to_record(raw) is None

    def test_returns_none_for_non_dict_input(self):
        assert Mem0PlatformMemory._to_record(None) is None
        assert Mem0PlatformMemory._to_record("string") is None
        assert Mem0PlatformMemory._to_record([1, 2, 3]) is None


# ---------------------------------------------------------------------------
# Adapter construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_construction_with_injected_client_does_not_import_sdk(self):
        """If we pass a client, the real Mem0 SDK should never be imported."""
        adapter = _make_adapter(Mock())
        assert adapter._client is not None  # noqa: SLF001


# ---------------------------------------------------------------------------
# search() — the cross-session invariant
# ---------------------------------------------------------------------------


class TestSearch:
    @pytest.mark.asyncio
    async def test_session_id_is_excluded_from_mem0_filters(self):
        """The whole point of long-term memory: recall across sessions.

        If session_id ever leaks into Mem0's scope filters, we accidentally
        scope recall to a single session and the feature breaks silently.

        Mem0's current SDK requires scope to go inside a `filters={...}`
        kwarg (top-level scope kwargs on `search()` raise ValueError), so
        we verify both that scope is in `filters` and that scope kwargs
        are NOT top-level.
        """
        client = Mock()
        client.search.return_value = {"results": []}
        adapter = _make_adapter(client)

        await adapter.search(
            MemorySearchQuery(
                query="where do I live?",
                scope=MemoryScope(user_id="u1", session_id="should-not-leak"),
                limit=5,
                threshold=0.7,
            )
        )

        kwargs = client.search.call_args.kwargs
        filters = kwargs.get("filters", {})
        # session_id must not appear anywhere — not as filter, not top-level
        assert "session_id" not in filters, filters
        assert "session_id" not in kwargs, kwargs
        # scope goes inside filters, not as a top-level kwarg
        assert filters.get("user_id") == "u1"
        assert "user_id" not in kwargs, kwargs
        # query, limit, threshold remain top-level
        assert kwargs["query"] == "where do I live?"
        assert kwargs["limit"] == 5
        assert kwargs["threshold"] == 0.7

    @pytest.mark.asyncio
    async def test_returns_normalized_records(self):
        client = Mock()
        client.search.return_value = {"results": [_record_dict()]}
        adapter = _make_adapter(client)

        results = await adapter.search(
            MemorySearchQuery(query="x", scope=MemoryScope(user_id="u1"))
        )
        assert len(results) == 1
        assert results[0].id == "mem-1"

    @pytest.mark.asyncio
    async def test_malformed_rows_are_dropped_not_raised(self):
        client = Mock()
        client.search.return_value = {
            "results": [_record_dict(), {"bad": "row"}]
        }
        adapter = _make_adapter(client)

        results = await adapter.search(
            MemorySearchQuery(query="x", scope=MemoryScope(user_id="u1"))
        )
        assert len(results) == 1  # only the well-formed row survives


# ---------------------------------------------------------------------------
# add()
# ---------------------------------------------------------------------------


class TestAdd:
    @pytest.mark.asyncio
    async def test_records_provenance_in_metadata(self):
        client = Mock()
        client.add.return_value = {"results": []}
        adapter = _make_adapter(client)

        await adapter.add(
            MemoryWrite(
                messages=[{"role": "user", "content": "hi"}],
                source="user_turn",
            ),
            MemoryScope(user_id="u1", session_id="s1"),
        )

        kwargs = client.add.call_args.kwargs
        assert kwargs["metadata"]["source"] == "user_turn"
        assert kwargs["metadata"]["session_id"] == "s1"

    @pytest.mark.asyncio
    async def test_session_id_not_passed_as_scope_kwarg(self):
        client = Mock()
        client.add.return_value = {"results": []}
        adapter = _make_adapter(client)

        await adapter.add(
            MemoryWrite(facts=["lives in Bangalore"]),
            MemoryScope(user_id="u1", agent_id="a1", session_id="s1"),
        )

        kwargs = client.add.call_args.kwargs
        assert "session_id" not in kwargs
        assert kwargs["user_id"] == "u1"
        assert kwargs["agent_id"] == "a1"

    @pytest.mark.asyncio
    async def test_raises_when_neither_messages_nor_facts_provided(self):
        adapter = _make_adapter(Mock())
        with pytest.raises(ValueError, match="messages.*facts"):
            await adapter.add(
                MemoryWrite(), MemoryScope(user_id="u1")
            )


# ---------------------------------------------------------------------------
# delete_scope() — privacy safety
# ---------------------------------------------------------------------------


class TestDeleteScope:
    @pytest.mark.asyncio
    async def test_refuses_scope_with_neither_user_nor_agent(self):
        """Privacy: must never delete with no scope filter — that wipes the world."""
        adapter = _make_adapter(Mock())
        with pytest.raises(ValueError, match="user_id or agent_id"):
            await adapter.delete_scope(MemoryScope(session_id="s1"))

    @pytest.mark.asyncio
    async def test_returns_deleted_count_from_response(self):
        client = Mock()
        client.delete_all.return_value = {"deleted_count": 7}
        adapter = _make_adapter(client)

        count = await adapter.delete_scope(MemoryScope(user_id="u1"))
        assert count == 7

    @pytest.mark.asyncio
    async def test_returns_zero_when_response_lacks_count(self):
        client = Mock()
        client.delete_all.return_value = None
        adapter = _make_adapter(client)

        count = await adapter.delete_scope(MemoryScope(user_id="u1"))
        assert count == 0
