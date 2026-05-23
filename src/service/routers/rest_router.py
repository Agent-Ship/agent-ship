from fastapi import APIRouter
from src.service.routers.agent_conversation_router import router as agent_conversation_router
from src.service.routers.memory_router import router as memory_router

router = APIRouter(prefix="/api")

# Include routers in order of specificity (most specific first)
router.include_router(agent_conversation_router, prefix="/agents", tags=["agents"])
# Memory admin endpoints live under /api/agents/memories — co-located with
# the agent endpoints because memory scope is always tied to an agent.
router.include_router(memory_router, prefix="/agents", tags=["memory"])

# Add other API endpoints here as needed
