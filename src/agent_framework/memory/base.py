"""LongTermMemory contract and shared data types.

Every backend adapter implements LongTermMemory. Middleware and agent code
depend only on this module — never on a specific adapter.

Spec: agent-ship/.spec-dev/agentship-long-term-memory/design.md §4.1
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


MemoryKind = Literal["factual", "episodic", "semantic", "procedural"]
MemorySource = Literal["user_turn", "assistant_turn", "explicit_remember"]


class MemoryScope(BaseModel):
    """Identifies who/what a memory belongs to.

    - `user_id`, `agent_id`, `app_id` are scope FILTERS — backends use them
      to partition storage and limit search.
    - `session_id` is NOT a search filter; it lives in metadata only so we
      get cross-session recall (the whole point of long-term memory).
    """

    user_id: Optional[str] = None
    agent_id: Optional[str] = None
    session_id: Optional[str] = None
    app_id: Optional[str] = None


class MemoryRecord(BaseModel):
    """One stored memory, as returned by backends.

    The shape is the same across every backend — backend-specific fields
    that don't map cleanly get tucked into `metadata`.
    """

    id: str
    text: str
    kind: MemoryKind
    scope: MemoryScope
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: Optional[float] = None
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime] = None


class MemoryWrite(BaseModel):
    """Input to `LongTermMemory.add()`.

    Provide ONE of:
    - `messages`: raw chat turns — the backend runs its own extraction prompt
      to decide what's worth remembering.
    - `facts`: pre-extracted facts — the backend stores them directly,
      skipping extraction.

    `source` records provenance for trust framing (see design.md §4.8) —
    auto-injection writes from the middleware are tagged `"user_turn"`.
    """

    messages: Optional[list[dict]] = None
    facts: Optional[list[str]] = None
    kind: Optional[MemoryKind] = None
    source: MemorySource = "user_turn"
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySearchQuery(BaseModel):
    """Input to `LongTermMemory.search()`.

    `kinds` is recorded but currently not used as a search filter — type-
    aware retrieval is added when an agent needs it (see design.md §2.2).
    """

    query: str
    scope: MemoryScope
    kinds: Optional[list[MemoryKind]] = None
    limit: int = 10
    threshold: Optional[float] = None


class LongTermMemory(ABC):
    """Contract every memory backend implements.

    Implementations live under `memory/backends/`. Callers (the middleware,
    the DELETE endpoint, tests) depend only on this ABC.
    """

    @abstractmethod
    async def add(
        self, write: MemoryWrite, scope: MemoryScope
    ) -> list[MemoryRecord]:
        """Persist a new memory or set of memories for the given scope.

        Backends decide whether to extract from raw `messages` or store
        `facts` directly. Implementations should record `write.source` in
        the stored metadata for trust framing.

        Args:
            write: What to remember (raw turns or pre-extracted facts).
            scope: Who this memory belongs to. `session_id` should be stored
                in metadata for traceability, NOT used as a partition key.

        Returns:
            The records the backend created (one per stored fact after
            extraction/dedup).
        """

    @abstractmethod
    async def search(
        self, query: MemorySearchQuery
    ) -> list[MemoryRecord]:
        """Find memories relevant to a query within the given scope.

        Cross-session recall is the design goal — implementations MUST NOT
        scope results to `query.scope.session_id`.

        Args:
            query: Search text, scope filter, top-k, and similarity threshold.

        Returns:
            Records sorted by relevance, up to `query.limit`, with score
            populated when the backend exposes one.
        """

    @abstractmethod
    async def get(self, memory_id: str) -> Optional[MemoryRecord]:
        """Fetch a single memory by id.

        Returns:
            The record, or None if the id is unknown.
        """

    @abstractmethod
    async def get_all(
        self, scope: MemoryScope, limit: int = 100, offset: int = 0
    ) -> list[MemoryRecord]:
        """List all memories in a scope (paginated).

        Like `search()`, this MUST NOT filter on `scope.session_id`.

        Args:
            scope: Partition filter (user_id / agent_id / app_id).
            limit: Page size.
            offset: How many records to skip.
        """

    @abstractmethod
    async def update(
        self,
        memory_id: str,
        text: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> MemoryRecord:
        """Modify an existing memory.

        Args:
            memory_id: Which memory to update.
            text: New body, or None to leave unchanged.
            metadata: Fields to merge into existing metadata, or None.

        Returns:
            The updated record.
        """

    @abstractmethod
    async def delete(self, memory_id: str) -> None:
        """Hard-delete one memory by id. Idempotent."""

    @abstractmethod
    async def delete_scope(self, scope: MemoryScope) -> int:
        """Hard-delete every memory matching the scope.

        Privacy operation — implementations MUST refuse a scope-less call
        (no `user_id` AND no `agent_id`) to prevent accidental wipe-the-world.

        Returns:
            How many memories were deleted.
        """

    @abstractmethod
    async def history(self, memory_id: str) -> list[dict]:
        """Return the mutation history for a memory.

        Args:
            memory_id: Which memory to inspect.

        Returns:
            Per-revision dicts (shape is backend-specific; common fields
            are `text`, `metadata`, `updated_at`).
        """
