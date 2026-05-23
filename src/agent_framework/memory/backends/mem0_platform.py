"""Mem0 Platform memory backend adapter.

Implements `LongTermMemory` by delegating to Mem0's hosted SaaS via the
`mem0ai` SDK. The SDK is synchronous; every call is wrapped in
`asyncio.to_thread` so the event loop never blocks.

Construction takes a validated `Mem0PlatformSettings`. Tests can inject a
pre-built client to avoid touching the network or installing `mem0ai`.

Spec: agent-ship/.spec-dev/agentship-long-term-memory/backends/mem0.md
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from src.agent_framework.configs.memory import Mem0PlatformSettings
from src.agent_framework.memory.base import (
    LongTermMemory,
    MemoryRecord,
    MemoryScope,
    MemorySearchQuery,
    MemoryWrite,
)


log = logging.getLogger(__name__)


class Mem0PlatformMemory(LongTermMemory):
    """LongTermMemory backed by Mem0's hosted Platform.

    See the `LongTermMemory` ABC for what each method is supposed to do;
    the docstrings below only note Mem0-specific behavior.

    Tests should pass a pre-built mock as `client` so the real `mem0ai`
    SDK is never imported during unit testing.
    """

    def __init__(
        self,
        settings: Mem0PlatformSettings,
        client: Optional[Any] = None,
    ):
        """Construct the adapter.

        Args:
            settings: Validated env-driven config. The `api_key` is required;
                `api_url` is optional and used only for self-hosted Mem0.
            client: Inject a pre-built client (typically a mock) for tests.
                When None, a real `mem0.MemoryClient` is constructed from
                `settings` via `_build_client()` — this is when the SDK is
                actually imported.
        """
        if client is None:
            client = _build_client(settings)
        self._client = client

    # ----------------------------------------------------------------------
    # LongTermMemory contract
    # ----------------------------------------------------------------------

    async def add(
        self, write: MemoryWrite, scope: MemoryScope
    ) -> list[MemoryRecord]:
        """Persist a memory via Mem0's `add` endpoint.

        Mem0 runs its own extraction prompt on `write.messages` to decide
        what's worth remembering. If you already have extracted facts,
        pass them via `write.facts` and Mem0 skips extraction.

        `scope.user_id` and `scope.agent_id` are passed to Mem0 as scope
        kwargs. `scope.session_id` is deliberately NOT passed as a kwarg;
        it goes into the metadata dict so future searches stay cross-
        session. `write.source` also goes into metadata for trust framing.

        Args:
            write: Either `messages` (raw turns) or `facts` (pre-extracted).
                One of the two is required.
            scope: Who this memory belongs to.

        Returns:
            The records Mem0 created — typically one per extracted fact.

        Raises:
            ValueError: When `write` has neither `messages` nor `facts`.
            Exceptions from the Mem0 SDK (network, auth, rate limit) are
            propagated unchanged — the middleware layer handles graceful
            degradation.
        """
        params = scope.model_dump(exclude_none=True, exclude={"session_id"})
        payload = write.messages if write.messages else write.facts
        if payload is None:
            raise ValueError("MemoryWrite requires either `messages` or `facts`")
        metadata = {
            **write.metadata,
            "source": write.source,
            "session_id": scope.session_id,
        }
        raw = await asyncio.to_thread(
            self._client.add, payload, **params, metadata=metadata
        )
        return self._normalize_results(raw)

    async def search(self, query: MemorySearchQuery) -> list[MemoryRecord]:
        """Run a semantic search against Mem0 for memories matching the query.

        Cross-session by design: `scope.session_id` is stripped before
        calling Mem0 so recall isn't bounded to one session. Other scope
        fields (`user_id`, `agent_id`, `app_id`) are passed inside a
        `filters={...}` dict — the Mem0 SDK rejects them as top-level
        kwargs on `search()` (only `add()` still accepts top-level).

        Args:
            query: Search text, scope filter, top-k limit, similarity threshold.

        Returns:
            Records ranked by similarity, malformed rows dropped with a log
            warning (one bad row should never fail the whole search).
        """
        filters = query.scope.model_dump(exclude_none=True, exclude={"session_id"})
        raw = await asyncio.to_thread(
            self._client.search,
            query=query.query,
            filters=filters,
            limit=query.limit,
            threshold=query.threshold,
        )
        return self._normalize_results(raw)

    async def get(self, memory_id: str) -> Optional[MemoryRecord]:
        """Fetch one memory by Mem0's id.

        Returns:
            The record, or None when Mem0 reports no such id.
        """
        raw = await asyncio.to_thread(self._client.get, memory_id=memory_id)
        return self._to_record(raw) if raw else None

    async def get_all(
        self, scope: MemoryScope, limit: int = 100, offset: int = 0
    ) -> list[MemoryRecord]:
        """List memories in the scope (paginated).

        Like `search()`, `session_id` is excluded — this is a cross-session
        listing. Scope fields go through Mem0's `filters={...}` parameter,
        which is the only shape the current SDK accepts on `get_all()`.
        `offset` is currently ignored because the SDK doesn't expose
        pagination on `get_all`; callers needing real paging should use
        `search()` or filter post-hoc.

        Args:
            scope: Partition filter (user/agent/app).
            limit: Page size.
            offset: Currently ignored; accepted for ABC compatibility.

        Returns:
            Records, malformed rows dropped with a warning.
        """
        filters = scope.model_dump(exclude_none=True, exclude={"session_id"})
        raw = await asyncio.to_thread(
            self._client.get_all, filters=filters, limit=limit
        )
        return self._normalize_results(raw)

    async def update(
        self,
        memory_id: str,
        text: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> MemoryRecord:
        """Modify the body and/or metadata of an existing memory.

        Args:
            memory_id: Which memory to update.
            text: New body. None leaves the body unchanged.
            metadata: Metadata to merge in. None leaves metadata unchanged.

        Returns:
            The updated record after Mem0 has persisted the change.

        Raises:
            ValueError: When Mem0 returns a malformed record for the
                updated memory — an update must always yield a valid record.
        """
        raw = await asyncio.to_thread(
            self._client.update,
            memory_id=memory_id,
            data=text,
            metadata=metadata,
        )
        record = self._to_record(raw)
        if record is None:
            raise ValueError(
                f"Mem0 returned a malformed record for update({memory_id!r})"
            )
        return record

    async def delete(self, memory_id: str) -> None:
        """Hard-delete a single memory by id. Idempotent on Mem0's side."""
        await asyncio.to_thread(self._client.delete, memory_id=memory_id)

    async def delete_scope(self, scope: MemoryScope) -> int:
        """Hard-delete every memory matching the scope (privacy / forget-me).

        Refuses to run unless at least one of `user_id` or `agent_id` is
        set — a scope-less `delete_all` would wipe every memory for every
        user, which is never what we want. `session_id` and `app_id`
        cannot stand alone for this reason.

        Args:
            scope: Must include `user_id` and/or `agent_id`.

        Returns:
            How many memories Mem0 reports as deleted. 0 when Mem0's
            response shape doesn't include a count.

        Raises:
            ValueError: When the scope has neither `user_id` nor `agent_id`.
        """
        params = scope.model_dump(
            exclude_none=True, include={"user_id", "agent_id"}
        )
        if not params:
            raise ValueError(
                "delete_scope requires at least one of user_id or agent_id "
                "to avoid scope-less deletion"
            )
        raw = await asyncio.to_thread(self._client.delete_all, **params)
        if isinstance(raw, dict):
            return int(raw.get("deleted_count", 0))
        return 0

    async def history(self, memory_id: str) -> list[dict]:
        """Return Mem0's mutation log for a memory.

        Returns:
            Per-revision dicts as Mem0 emits them. The shape is Mem0-
            specific (not normalized into `MemoryRecord`) because the
            audit log isn't load-bearing for any framework code today.
        """
        return await asyncio.to_thread(
            self._client.history, memory_id=memory_id
        )

    # ----------------------------------------------------------------------
    # Response normalization
    # ----------------------------------------------------------------------

    def _normalize_results(self, raw: Any) -> list[MemoryRecord]:
        """Turn a Mem0 multi-row response into a list of MemoryRecord.

        Mem0 returns either `{"results": [...]}` or a bare list depending
        on the endpoint and SDK version — `_extract_rows` handles both
        shapes. Rows that fail to convert into `MemoryRecord` are dropped
        with a logged warning rather than failing the whole call.
        """
        rows = _extract_rows(raw)
        return [r for r in (self._to_record(row) for row in rows) if r is not None]

    @staticmethod
    def _to_record(raw: Any) -> Optional[MemoryRecord]:
        """Convert a single Mem0 response dict into a MemoryRecord.

        Handles the small shape differences between Mem0 versions:
        - id can be `id` or `memory_id`
        - body can be `memory` or `text`
        - `kind` defaults to `"factual"` when metadata doesn't carry one
        - `session_id` is read out of metadata (where we put it on write)

        Returns:
            A validated MemoryRecord, or None (with a warning log) when
            the row is missing required fields. Dropping a malformed row
            is preferable to failing the whole multi-row response.
        """
        if not isinstance(raw, dict):
            return None
        try:
            memory_id = raw.get("id") or raw.get("memory_id")
            text = raw.get("memory") or raw.get("text")
            if memory_id is None or text is None:
                raise KeyError("id/memory_id and memory/text are both required")
            metadata = raw.get("metadata") or {}
            return MemoryRecord(
                id=str(memory_id),
                text=str(text),
                kind=metadata.get("kind", "factual"),
                scope=MemoryScope(
                    user_id=raw.get("user_id"),
                    agent_id=raw.get("agent_id"),
                    session_id=metadata.get("session_id"),
                    app_id=raw.get("app_id"),
                ),
                metadata=metadata,
                score=raw.get("score"),
                created_at=_parse_dt(raw.get("created_at")),
                updated_at=_parse_dt(
                    raw.get("updated_at") or raw.get("created_at")
                ),
            )
        except (KeyError, ValueError) as e:
            log.warning(
                "mem0.malformed_record raw=%r error=%s", raw, str(e)
            )
            return None


# ---------------------------------------------------------------------------
# Module-level helpers (testable without instantiating the adapter)
# ---------------------------------------------------------------------------


def _extract_rows(raw: Any) -> list[Any]:
    """Normalize Mem0's two response shapes into a plain list of rows.

    Mem0 returns either `{"results": [...]}` (search, get_all on some
    versions) or a bare `[...]` (other endpoints / versions). Anything
    else (None, strings, dicts without `results`) becomes an empty list
    so callers can iterate without defensive type checks.
    """
    if isinstance(raw, dict) and "results" in raw:
        rows = raw["results"]
        return rows if isinstance(rows, list) else []
    if isinstance(raw, list):
        return raw
    return []


def _parse_dt(value: Any) -> datetime:
    """Best-effort parse of Mem0's various timestamp shapes.

    Accepts a `datetime` (returned as-is), an ISO-8601 string (parsed,
    `"Z"` suffix tolerated), or anything else (falls back to "now" so a
    missing or malformed timestamp doesn't bring the request down).
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _build_client(settings: Mem0PlatformSettings) -> Any:
    """Lazy-import the `mem0ai` SDK and build a configured client.

    Done lazily (and not at module load) so that:
    - The adapter module imports cleanly without `mem0ai` installed.
    - Unit tests can pass a mock `client` to the adapter and never
      trigger the import.

    `host` is only passed to `MemoryClient` when `settings.api_url` is
    actually set. Passing `host=None` or `host=""` would either confuse
    the SDK or get forwarded to httpx as an invalid URL.

    Raises:
        ImportError: When `mem0ai` isn't installed, with the exact
            install command in the message.
    """
    try:
        from mem0 import MemoryClient
    except ImportError as e:
        raise ImportError(
            "The 'mem0ai' package is required for the mem0_platform backend. "
            "Install it with: pipenv install mem0ai"
        ) from e

    client_kwargs: dict[str, Any] = {"api_key": settings.api_key}
    if settings.api_url:
        client_kwargs["host"] = settings.api_url
    return MemoryClient(**client_kwargs)
