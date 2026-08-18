"""``create_app`` — assemble the runtime-service FastAPI app with its middleware stack.

The middleware are mounted in one documented order (outermost → innermost):

    CORS → SecurityHeaders (+ trace id) → Auth (+ TenantScope) → router

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

from .errors import install_error_handlers
from .middleware import AuthMiddleware, SecurityHeadersMiddleware


def create_app(
    *,
    auth: AuthProvider,
    cors_origins: Sequence[str] = (),
    hsts: bool = False,
    title: str = "AgentShip",
) -> FastAPI:
    """Build the runtime-service app authenticated by ``auth``.

    ``cors_origins`` is an explicit allow-list (never ``*`` with credentials); ``hsts``
    turns on Strict-Transport-Security for a TLS deployment. The returned app already has
    a public ``GET /healthz`` liveness probe and the problem+json error handlers installed.
    """
    app = FastAPI(title=title, docs_url="/docs", redoc_url="/redoc")
    install_error_handlers(app)

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        """Unauthenticated liveness probe."""
        return {"status": "ok"}

    # Mount inner → outer. add_middleware makes each call the new outermost layer, so the
    # last call (CORS) runs first on a request and the first call (Auth) runs last.
    app.add_middleware(AuthMiddleware, auth=auth)
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
