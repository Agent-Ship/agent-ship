"""Unit tests for the DELETE /api/agents/memories endpoint.

These tests use FastAPI's TestClient against a minimal app that only
mounts the memory router. The agent registry lookup and the
`MemoryFactory.create()` call are patched so the tests don't touch
real agents or real backends.
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agent_framework.memory.base import LongTermMemory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    """A FastAPI TestClient with just the memory router mounted.

    Importing the router after the patches in tests would be too late
    (FastAPI captures the function objects at decoration time), so we
    mount it once here and have individual tests patch the symbols the
    handler actually calls at runtime.
    """
    from src.service.routers.memory_router import router

    app = FastAPI()
    app.include_router(router, prefix="/api/agents")
    # `raise_server_exceptions=False` makes TestClient behave like a real
    # uvicorn server: unhandled handler exceptions become 500 responses
    # instead of bubbling up into the test. We need that to assert the
    # "privacy ops must fail loud" behaviour without having to wrap
    # `client.delete(...)` in `pytest.raises`.
    return TestClient(app, raise_server_exceptions=False)


def _fake_agent_with_memory(enabled: bool = True) -> MagicMock:
    """Build a fake agent object whose `agent_config.memory.enabled` is set."""
    agent = MagicMock()
    agent.agent_config.memory.enabled = enabled
    return agent


def _fake_backend(*, delete_returns: int = 0, delete_raises: Optional[Exception] = None) -> LongTermMemory:
    backend = MagicMock(spec=LongTermMemory)
    if delete_raises is not None:
        backend.delete_scope = AsyncMock(side_effect=delete_raises)
    else:
        backend.delete_scope = AsyncMock(return_value=delete_returns)
    return backend


# ---------------------------------------------------------------------------
# Header / scope validation
# ---------------------------------------------------------------------------


class TestHeaderAndScopeValidation:
    def test_missing_header_is_400(self, client):
        resp = client.delete("/api/agents/memories", params={"user_id": "alice", "agent_id": "demo"})
        assert resp.status_code == 422   # FastAPI returns 422 for missing required header

    def test_header_mismatch_is_400(self, client):
        resp = client.delete(
            "/api/agents/memories",
            params={"user_id": "alice", "agent_id": "demo"},
            headers={"X-Confirm-Delete": "not-alice"},
        )
        assert resp.status_code == 400
        assert "must match" in resp.json()["detail"]

    def test_missing_user_id_is_422(self, client):
        resp = client.delete(
            "/api/agents/memories",
            params={"agent_id": "demo"},
            headers={"X-Confirm-Delete": "alice"},
        )
        assert resp.status_code == 422   # FastAPI's required-param validation

    def test_missing_agent_id_is_422(self, client):
        resp = client.delete(
            "/api/agents/memories",
            params={"user_id": "alice"},
            headers={"X-Confirm-Delete": "alice"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Agent lookup + memory-enabled check
# ---------------------------------------------------------------------------


class TestAgentLookup:
    def test_unknown_agent_is_404(self, client):
        with patch("src.service.routers.memory_router.get_agent_instance",
                   side_effect=KeyError("no_such_agent")):
            resp = client.delete(
                "/api/agents/memories",
                params={"user_id": "alice", "agent_id": "no_such_agent"},
                headers={"X-Confirm-Delete": "alice"},
            )
        assert resp.status_code == 404
        assert "no_such_agent" in resp.json()["detail"]

    def test_agent_without_memory_enabled_is_400(self, client):
        agent = _fake_agent_with_memory(enabled=False)
        with patch("src.service.routers.memory_router.get_agent_instance", return_value=agent):
            resp = client.delete(
                "/api/agents/memories",
                params={"user_id": "alice", "agent_id": "demo"},
                headers={"X-Confirm-Delete": "alice"},
            )
        assert resp.status_code == 400
        assert "not have memory enabled" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_returns_deleted_count_and_passes_scope_correctly(self, client):
        agent = _fake_agent_with_memory()
        backend = _fake_backend(delete_returns=7)

        with patch("src.service.routers.memory_router.get_agent_instance", return_value=agent), \
             patch("src.service.routers.memory_router.MemoryFactory.create", return_value=backend):
            resp = client.delete(
                "/api/agents/memories",
                params={"user_id": "alice", "agent_id": "demo"},
                headers={"X-Confirm-Delete": "alice"},
            )

        assert resp.status_code == 200
        assert resp.json() == {"deleted_count": 7}
        # Backend was called with the right scope.
        backend.delete_scope.assert_awaited_once()
        scope_arg = backend.delete_scope.await_args.args[0]
        assert scope_arg.user_id == "alice"
        assert scope_arg.agent_id == "demo"

    def test_zero_deleted_is_still_200(self, client):
        """Deletion is idempotent — wiping nothing isn't an error."""
        agent = _fake_agent_with_memory()
        backend = _fake_backend(delete_returns=0)

        with patch("src.service.routers.memory_router.get_agent_instance", return_value=agent), \
             patch("src.service.routers.memory_router.MemoryFactory.create", return_value=backend):
            resp = client.delete(
                "/api/agents/memories",
                params={"user_id": "alice", "agent_id": "demo"},
                headers={"X-Confirm-Delete": "alice"},
            )

        assert resp.status_code == 200
        assert resp.json() == {"deleted_count": 0}


# ---------------------------------------------------------------------------
# Backend errors must NOT be swallowed (privacy operation)
# ---------------------------------------------------------------------------


class TestBackendErrorsPropagate:
    def test_backend_exception_surfaces_as_500(self, client):
        """Privacy must be reliable. If the backend errored, the user
        must learn the delete didn't happen."""
        agent = _fake_agent_with_memory()
        backend = _fake_backend(delete_raises=RuntimeError("mem0 down"))

        with patch("src.service.routers.memory_router.get_agent_instance", return_value=agent), \
             patch("src.service.routers.memory_router.MemoryFactory.create", return_value=backend):
            resp = client.delete(
                "/api/agents/memories",
                params={"user_id": "alice", "agent_id": "demo"},
                headers={"X-Confirm-Delete": "alice"},
            )

        assert resp.status_code == 500
