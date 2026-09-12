"""``create_app`` — assemble the runtime-service FastAPI app with its middleware stack.

The middleware are mounted in one documented order (outermost → innermost):

    CORS → SecurityHeaders (+ trace id) → ProblemFallback → Auth (+ TenantScope) → router

so a request is CORS-checked, stamped, rate-limited (when enabled, P04 C5), and
authenticated *before* any route runs, and a CORS preflight (``OPTIONS``) short-circuits at
the outermost layer without ever reaching authentication. Error handlers render every
failure as RFC-9457 problem+json. The agent/discovery/task routers are attached here as
they land (P04 C1); this module owns the wiring, not the routes.
"""

from __future__ import annotations

from collections.abc import Sequence

from agentship.auth import AuthProvider
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from .build_info import build_info
from .errors import install_error_handlers
from .middleware import (
    AuthMiddleware,
    ProblemFallbackMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from .registry import AgentRegistry
from .routers import (
    a2a_router,
    agents_router,
    live_router,
    studio_router,
    tasks_router,
    voice_router,
)
from .routers.tasks import TaskStore


def create_app(
    *,
    auth: AuthProvider,
    agents: AgentRegistry | None = None,
    cors_origins: Sequence[str] = (),
    hsts: bool = False,
    rate_limit: bool = False,
    requests_per_second: float = 10.0,
    rate_limit_burst: int = 20,
    title: str = "AgentShip",
) -> FastAPI:
    """Build the runtime-service app authenticated by ``auth`` serving ``agents``.

    ``agents`` is the catalog the v1 routes invoke and discover (an empty registry when
    omitted). ``cors_origins`` is an explicit allow-list (never ``*`` with credentials);
    ``hsts`` turns on Strict-Transport-Security for a TLS deployment. ``rate_limit`` enables
    the optional in-process token-bucket limiter (off by default — a gateway-free safety
    net only; real rate-limiting is agentgateway's job). The returned app already has a
    public ``GET /healthz`` liveness probe, the public ``GET /studio`` debug UI, the v1
    agent + task routers, and the problem+json error handlers installed.
    """
    app = FastAPI(title=title, docs_url="/docs", redoc_url="/redoc")
    app.state.agents = agents if agents is not None else AgentRegistry()
    app.state.tasks = TaskStore()
    install_error_handlers(app)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, object]:
        """Unauthenticated liveness probe, carrying which build is answering.

        The build stamp and package versions are here so a caller can tell *what code*
        is running without shelling into the container — the question "is this the
        latest?" should be answerable from outside.
        """
        return {"status": "ok", **build_info()}

    app.include_router(agents_router)
    app.include_router(live_router)
    app.include_router(voice_router)
    app.include_router(studio_router)
    app.include_router(tasks_router)
    app.include_router(a2a_router)

    # Mount inner → outer. add_middleware makes each call the new outermost layer, so the
    # last call (CORS) runs first on a request and the first call (Auth) runs last. The
    # resulting request-path order is
    # CORS → SecurityHeaders → ProblemFallback → RateLimit → Auth → router.
    app.add_middleware(AuthMiddleware, auth=auth)
    app.add_middleware(
        RateLimitMiddleware,
        enabled=rate_limit,
        requests_per_second=requests_per_second,
        burst=rate_limit_burst,
    )
    # Directly inside SecurityHeaders: an exception escaping ANY inner layer (auth, rate
    # limit, a route) becomes problem+json here, so the response still travels back out
    # through SecurityHeaders and is stamped like any other. Starlette's own
    # ServerErrorMiddleware is outside everything the app mounts and cannot be.
    app.add_middleware(ProblemFallbackMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, hsts=hsts)
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["authorization", "content-type", "x-api-key"],
        )
    return app
