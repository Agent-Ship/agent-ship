"""Map harness errors to RFC-9457 ``application/problem+json`` responses.

Every failure a client sees is a problem+json body: a stable machine ``code``, an HTTP
``status``, a human ``title``/``detail``, and the request ``trace_id`` — never a stack
trace, SQL, or other internal detail. :func:`install_error_handlers` registers one handler
per harness error type on a FastAPI app; :func:`problem_response` builds the body, and
:func:`problem_dict` builds the same shape for an ``error`` :class:`StreamEvent` on a
stream (where the HTTP status was already 200 when the failure happened mid-stream).
"""

from __future__ import annotations

from agentship.errors import (
    AgentShipError,
    AuthError,
    CapabilityError,
    EngineNotFoundError,
    ModelError,
    ResumeError,
    SpecError,
    TenantViolation,
    ThreadBusyError,
)
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .context import current_trace_id
from .models.v1 import ProblemDetail

#: The media type RFC-9457 defines for a problem document.
PROBLEM_JSON = "application/problem+json"

#: Auth failure codes that mean "who are you?" (401) rather than "you may not" (403).
#: ``forbidden`` is an authorization denial → 403; everything else here is authentication.
_AUTHZ_CODES = frozenset({"forbidden"})


def problem_dict(
    *, status: int, title: str, code: str | None = None, detail: str | None = None
) -> dict:
    """Build an RFC-9457 problem body as a plain dict (for a JSON body or a stream frame)."""
    return ProblemDetail(
        title=title,
        status=status,
        code=code,
        detail=detail,
        trace_id=current_trace_id(),
    ).model_dump()


def problem_response(
    *, status: int, title: str, code: str | None = None, detail: str | None = None
) -> JSONResponse:
    """Build a problem+json :class:`JSONResponse` with the correct media type."""
    return JSONResponse(
        status_code=status,
        content=problem_dict(status=status, title=title, code=code, detail=detail),
        media_type=PROBLEM_JSON,
    )


def _auth_error(request: Request, exc: AuthError) -> JSONResponse:
    """A failed authentication → 401, an authorization denial (``forbidden``) → 403."""
    if exc.code in _AUTHZ_CODES:
        return problem_response(status=403, title="Forbidden", code=exc.code, detail=str(exc))
    return problem_response(
        status=401, title="Unauthorized", code=exc.code, detail=str(exc)
    )


def _tenant_violation(request: Request, exc: TenantViolation) -> JSONResponse:
    """Cross-tenant access: **404** on a read (hide existence), **403** on a write.

    Using 404 for a safe (GET/HEAD) method means a client cannot even learn that another
    tenant's resource exists; a mutating method gets an explicit 403.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return problem_response(status=404, title="Not Found", code="not_found")
    return problem_response(status=403, title="Forbidden", code="forbidden")


def _thread_busy(request: Request, exc: ThreadBusyError) -> JSONResponse:
    """Another owner holds this thread's lock → 409 Conflict."""
    return problem_response(status=409, title="Conflict", code="thread_busy", detail=str(exc))


def _resume_error(request: Request, exc: ResumeError) -> JSONResponse:
    """A resume token that cannot be replayed → 409 (the run must be restarted)."""
    return problem_response(
        status=409, title="Conflict", code="resume_failed", detail=str(exc)
    )


def _capability_error(request: Request, exc: CapabilityError) -> JSONResponse:
    """The request asked for something the engine does not support → 400 Bad Request."""
    return problem_response(
        status=400, title="Bad Request", code="unsupported", detail=str(exc)
    )


def _model_error(request: Request, exc: ModelError) -> JSONResponse:
    """An upstream model/provider call failed → 502 Bad Gateway."""
    return problem_response(
        status=502, title="Bad Gateway", code="model_error", detail=str(exc)
    )


def _agentship_error(request: Request, exc: AgentShipError) -> JSONResponse:
    """Any other known harness error → 500, with its actionable message (no internals)."""
    return problem_response(
        status=500, title="Internal Server Error", code="internal_error", detail=str(exc)
    )


def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    """An unexpected error → 500 with a generic body (never leak the exception text)."""
    return problem_response(status=500, title="Internal Server Error", code="internal_error")


def install_error_handlers(app: FastAPI) -> None:
    """Register the problem+json handlers on ``app``, most specific first.

    Subclasses are registered before their bases so the most specific handler wins:
    :class:`TenantViolation`/:class:`ThreadBusyError`/etc. before the catch-all
    :class:`AgentShipError`, and :class:`Exception` last as the leak-proof backstop.
    """
    app.add_exception_handler(AuthError, _auth_error)
    app.add_exception_handler(TenantViolation, _tenant_violation)
    app.add_exception_handler(ThreadBusyError, _thread_busy)
    app.add_exception_handler(ResumeError, _resume_error)
    app.add_exception_handler(CapabilityError, _capability_error)
    app.add_exception_handler(ModelError, _model_error)
    # SpecError / EngineNotFoundError are server-side misconfig → generic 500 via the base.
    app.add_exception_handler(SpecError, _agentship_error)
    app.add_exception_handler(EngineNotFoundError, _agentship_error)
    app.add_exception_handler(AgentShipError, _agentship_error)
    app.add_exception_handler(Exception, _unhandled)
