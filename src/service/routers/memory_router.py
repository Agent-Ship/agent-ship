"""Memory administration endpoints.

Today this is just the "forget-me" path (DELETE) — the minimum required
to honour a user's right to deletion. Listing / reading / updating
memories via REST is explicitly out of scope (design §2.4); operators
should use the backend's own UI (Mem0 dashboard, etc.) for those.

Spec: agent-ship/.spec-dev/agentship-long-term-memory/design.md §4.7
"""

import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query

from src.agent_framework.factories import MemoryFactory
from src.agent_framework.memory.base import MemoryScope
from src.agent_framework.registry import get_agent_instance


logger = logging.getLogger(__name__)
router = APIRouter()


@router.delete("/memories")
async def delete_memories(
    user_id: str = Query(..., description="The user whose memories should be deleted."),
    agent_id: str = Query(
        ...,
        description=(
            "Which agent's memory backend to delete from. Required because "
            "the backend (and its connection settings) is selected per-agent "
            "via the agent's YAML `memory.backend`."
        ),
    ),
    x_confirm_delete: str = Header(
        ...,
        alias="X-Confirm-Delete",
        description=(
            "Must equal the `user_id` query param. Bare-minimum safeguard "
            "against accidental deletes from misconfigured callers."
        ),
    ),
) -> dict:
    """Hard-delete every memory for a `(user_id, agent_id)` scope.

    Forget-me operation. Idempotent: deleting non-existent memories
    returns `{deleted_count: 0}` rather than 404. Backend errors are
    NOT swallowed — privacy operations must fail loud so callers know
    the delete did not happen.

    Returns:
        `{"deleted_count": N}` — how many memories the backend reports
        as deleted. Counts may be approximate depending on the backend
        (Mem0 returns `deleted_count`; some backends don't report a
        precise number).

    Raises:
        400: `X-Confirm-Delete` header missing or doesn't match `user_id`.
        400: Target agent doesn't have memory enabled — nothing to delete.
        404: Target agent name isn't registered.
        500: Backend raised during delete (privacy must be reliable —
             errors propagate).
    """
    if x_confirm_delete != user_id:
        # 400 (not 401/403) — this is a structural header check, not auth.
        raise HTTPException(
            status_code=400,
            detail="X-Confirm-Delete header must match the user_id query parameter.",
        )

    try:
        agent = get_agent_instance(agent_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown agent: {agent_id}") from exc

    memory_config = agent.agent_config.memory
    if not memory_config.enabled:
        raise HTTPException(
            status_code=400,
            detail=f"Agent '{agent_id}' does not have memory enabled — nothing to delete.",
        )

    # Build a backend instance just for this operation. Cheap — adapters
    # are stateless wrappers around their SDK client, and we don't keep
    # this one around once the request returns.
    backend = MemoryFactory.create(memory_config=memory_config)

    logger.info(
        "memory.delete.scope.start agent=%s user=%s",
        agent_id, user_id,
    )
    # Errors here are NOT caught — privacy ops must fail loud so the
    # caller knows the delete did not happen.
    deleted_count = await backend.delete_scope(
        MemoryScope(user_id=user_id, agent_id=agent_id)
    )
    logger.info(
        "memory.delete.scope.done agent=%s user=%s deleted_count=%d",
        agent_id, user_id, deleted_count,
    )

    return {"deleted_count": deleted_count}
