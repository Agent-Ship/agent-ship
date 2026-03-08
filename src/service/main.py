'''
This is the main file for the backend.
'''
import asyncio
import logging
import os
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from dotenv import load_dotenv
from src.agent_framework.registry import discover_agents
from src.service.routers.rest_router import router as rest_router
from src.service.routes.mcp_auth import router as mcp_auth_router
from src.service.routers.tax_router import router as tax_router
from studio.router import router as debug_router
load_dotenv()

# logger
logger = logging.getLogger(__name__)


def _mcp_gc_exception_handler(loop: asyncio.AbstractEventLoop, context: dict) -> None:
    """Suppress harmless anyio cancel scope errors from MCP STDIO GC cleanup.

    When the asyncio event loop changes between web requests, MCP STDIO clients
    reconnect. The old stdio_client async generator is then GC'd, but anyio's
    TaskGroup cleanup fails because it's in a different task. This is harmless —
    the connection is already superseded — but asyncio logs it at ERROR level.
    We downgrade it to DEBUG here.
    """
    msg = context.get("message", "")
    exc = context.get("exception")
    if (
        "closing of asynchronous generator" in msg
        and isinstance(exc, RuntimeError)
        and "cancel scope" in str(exc).lower()
    ):
        logger.debug("MCP STDIO GC cleanup (harmless): %s", exc)
        return
    loop.default_exception_handler(context)


# Install the exception handler immediately so it catches any early-startup GC
try:
    asyncio.get_event_loop().set_exception_handler(_mcp_gc_exception_handler)
except RuntimeError:
    pass  # No running loop at import time — handler is also set in startup event


# Get project root for static files (needed early for favicon)
# Use absolute path to ensure it works regardless of where the script is run from
project_root = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# Mount branding assets EARLY (before FastAPI app creation to ensure images are available)
branding_path = os.path.abspath(os.path.join(project_root, "branding"))

app = FastAPI(
    title="AgentShip API",
    description="AgentShip - An Agent Shipping Kit. Production-ready AI Agent framework with multiple agent patterns and observability.",
    version="1.0.0",
    contact={
        "name": "AgentShip Support",
        "email": "support@agentship.dev",
    },
    license_info={
        "name": "MIT License",
        "url": "https://opensource.org/licenses/MIT",
    },
    docs_url="/swagger",  # Swagger UI at /swagger (keep for direct access)
    redoc_url=None,  # Disable default /redoc (redirect to /docs)
    openapi_url="/openapi.json",  # OpenAPI JSON at /openapi.json
    swagger_ui_parameters={
        "faviconUrl": "/branding/favicons/favicon-32.png",
    },
)

# Add favicon route for Swagger/ReDoc (must be before other routes)
@app.get("/favicon.ico")
async def favicon():
    """Serve favicon for Swagger UI and browser tabs."""
    favicon_path = os.path.join(branding_path, "favicons", "favicon-32.png")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path, media_type="image/png")
    # Fallback to 204 No Content if favicon not found
    from fastapi.responses import Response
    return Response(status_code=204)

@app.get("/")
async def read_root():
    '''
    Root endpoint for the backend.
    '''
    logger.info("Root endpoint hit")
    return {"message": "Welcome to the AgentShip API!"}


@app.get("/health")
async def health_check():
    '''
    Health check endpoint for the backend.
    Includes basic memory info (uses psutil if available).
    '''
    try:
        import psutil
        process = psutil.Process()
        mem_info = process.memory_info()
        mem_mb = mem_info.rss / (1024 * 1024)
        eco_limit_mb = 512.0
        
        return {
            "status": "running",
            "memory_mb": round(mem_mb, 2),
            "memory_limit_mb": eco_limit_mb,
            "within_limit": mem_mb < eco_limit_mb,
            "percent_of_limit": round((mem_mb / eco_limit_mb) * 100, 1)
        }
    except ImportError:
        # psutil not installed, return basic status
        return {"status": "running"}

# Serve unified documentation at /docs (single source of truth)
# This serves Sphinx docs if built, otherwise shows a hub page with links
docs_sphinx_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "docs_sphinx", "build", "html")
if os.path.exists(docs_sphinx_path) and os.path.exists(os.path.join(docs_sphinx_path, "index.html")):
    # Mount static files for Sphinx site at /docs
    app.mount("/docs", StaticFiles(directory=docs_sphinx_path, html=True), name="docs")
else:
    # If docs not built, show a documentation hub page
    @app.get("/docs", response_class=HTMLResponse)
    @app.get("/docs/{path:path}", response_class=HTMLResponse)
    async def docs_info(path: str = ""):
        """Show documentation hub page with links to all documentation sources."""
        html_content = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>AgentShip Documentation</title>
            <link rel="icon" type="image/png" href="/branding/favicons/favicon-32.png">
            <style>
                body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; 
                       max-width: 900px; margin: 50px auto; padding: 20px; 
                       background: #fafafa; }
                .container { background: white; padding: 40px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
                h1 { color: #333; margin-bottom: 10px; }
                .subtitle { color: #666; margin-bottom: 30px; }
                .link { display: block; margin: 20px 0; padding: 20px; 
                        background: #f5f5f5; border-radius: 8px; text-decoration: none; 
                        color: #0066cc; border-left: 4px solid #0066cc; 
                        transition: all 0.2s; }
                .link:hover { background: #e3f2fd; transform: translateX(4px); }
                .link strong { display: block; font-size: 1.1em; margin-bottom: 5px; }
                .link small { color: #666; }
                .primary { background: #e3f2fd; border-left-color: #1976d2; }
                .note { margin-top: 30px; padding: 15px; background: #fff3cd; border-radius: 5px; color: #856404; }
            </style>
        </head>
        <body>
            <div class="container">
                <div style="text-align: center; margin: -40px -40px 30px -40px; padding: 20px 0; background: #F8FAFC; border-radius: 8px 8px 0 0;">
                    <img src="/branding/banners/docs-header@3x.png" alt="AgentShip Documentation" style="width: 100%; max-width: 960px; height: auto;" />
                </div>
                <h1>📚 AgentShip Documentation</h1>
                <p class="subtitle">All documentation in one place</p>
                
                <a href="/swagger" class="link primary">
                    <strong>🔧 Interactive API Documentation (Swagger)</strong>
                    <small>Test API endpoints directly in your browser</small>
                </a>
                
                <div class="note">
                    <strong>Note:</strong> Full documentation (API reference + user guides) will be available here once built. 
                    Run <code>make docs-build</code> to generate it.
                </div>
            </div>
        </body>
        </html>
        """
        return HTMLResponse(content=html_content)

# Redirect /redoc to /docs for consistency
@app.get("/redoc")
async def redoc_redirect():
    """Redirect ReDoc to unified /docs endpoint."""
    return RedirectResponse(url="/docs", status_code=301)

# Mount branding assets FIRST (before routers to ensure it takes precedence)
if os.path.exists(branding_path):
    try:
        app.mount("/branding", StaticFiles(directory=branding_path), name="branding")
        logger.info(f"🎨 Branding assets mounted at /branding from {branding_path}")
        # Test that a file exists
        test_file = os.path.join(branding_path, "icons", "icon-light-2048.png")
        if os.path.exists(test_file):
            logger.info(f"✅ Test file exists: {test_file}")
        else:
            logger.warning(f"⚠️  Test file not found: {test_file}")
    except Exception as e:
        logger.error(f"❌ Failed to mount branding assets: {e}")
else:
    logger.warning(f"⚠️  Branding assets not found at {branding_path}")

@app.on_event("startup")
async def _install_exception_handler() -> None:
    """Reinstall the MCP GC exception handler on the ASGI event loop."""
    asyncio.get_event_loop().set_exception_handler(_mcp_gc_exception_handler)


# Ensure agents are discovered (idempotent)
# Uses AGENT_DIRECTORIES env var or defaults to framework agents only
discover_agents()

app.include_router(rest_router)

# Include MCP OAuth router
app.include_router(mcp_auth_router)

# Include Debug API router
app.include_router(debug_router, prefix="/api/debug", tags=["debug"])

# Include Tax demo router
app.include_router(tax_router, prefix="/api/tax", tags=["tax"])

# Serve AgentShip Studio (formerly Debug UI)
debug_ui_enabled = os.environ.get("STUDIO_ENABLED", os.environ.get("DEBUG_UI_ENABLED", "true")).lower() == "true"
if debug_ui_enabled:
    # studio/ is at project root level - use absolute path
    static_path = os.path.abspath(os.path.join(project_root, "studio", "static"))
    if os.path.exists(static_path):
        # Serve static files under /studio/static
        app.mount("/studio/static", StaticFiles(directory=static_path), name="studio-static")

        @app.get("/studio", response_class=HTMLResponse)
        @app.get("/studio/", response_class=HTMLResponse)
        async def studio_ui():
            """Serve AgentShip Studio with no-cache headers for development."""
            response = FileResponse(os.path.join(static_path, "index.html"))
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            return response

        from fastapi.responses import RedirectResponse

        @app.get("/debug-ui")
        @app.get("/debug-ui/")
        async def debug_ui_redirect():
            """Redirect legacy /debug-ui to /studio."""
            return RedirectResponse(url="/studio", status_code=301)

        # TaxBot demo UI at /tax
        @app.get("/tax", response_class=HTMLResponse)
        @app.get("/tax/", response_class=HTMLResponse)
        async def tax_ui():
            """Serve TaxBot — 2024 US Federal Tax Filing demo."""
            tax_path = os.path.join(static_path, "tax.html")
            response = FileResponse(tax_path)
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            return response

        logger.info("🎨 AgentShip Studio mounted at /studio (legacy /debug-ui → 301 redirect)")
    else:
        logger.warning(f"AgentShip Studio static files not found at {static_path}")

# Prometheus metrics (optional - install prometheus-fastapi-instrumentator)
# Uncomment to enable standard Prometheus metrics including memory/CPU
# from prometheus_fastapi_instrumentator import Instrumentator
# instrumentator = Instrumentator()
# instrumentator.instrument(app).expose(app)
# Then access metrics at: http://localhost:7001/metrics

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 7001))
    # Respect LOG_LEVEL env var; fallback to INFO
    uvicorn_log_level = os.environ.get("LOG_LEVEL", "INFO").lower()
    uvicorn.run(app, host="0.0.0.0", port=port, log_level=uvicorn_log_level)
