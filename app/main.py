"""
Lippe Lift Studio Main Application
FastAPI application with error handling and middleware.
Per Constitution § I: Canon Supremacy
"""

import asyncio
import uuid
import time
import os
from contextlib import asynccontextmanager, suppress
from urllib.parse import urlparse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse, HTMLResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings, google_ai_context_fingerprint
from app.core.video_profiles import get_duration_profile_for_creation_mode
from app.core.logging import configure_logging, get_logger, set_correlation_id
from app.core.errors import FlowForgeException, ErrorResponse, error_code_for_status
from app.adapters.supabase_client import get_supabase
from app.features.batches.handlers import router as batches_router
from app.features.topics.handlers import (
    find_recoverable_stalled_batch_ids,
    router as topics_router,
    schedule_recovered_batch_discovery,
)
from app.features.topics.hub import recover_stalled_topic_research_runs
from app.features.topics.queries import get_topic_research_cron_monitoring
from app.features.posts.handlers import router as posts_router
from app.features.videos.handlers import router as videos_router
from app.features.qa.handlers import router as qa_router
from app.features.publish.handlers import router as publish_router, run_scheduled_publish_job
from app.features.blog.handlers import router as blog_router, run_scheduled_blog_publish_job
from app.features.auth.handlers import router as auth_router
from app.features.auth.middleware import (
    require_auth,
    is_public_path,
    load_authenticated_user,
    encode_session_cookie,
)
from app.features.characters.handlers import router as characters_router
from app.features.scenes.handlers import router as scenes_router

try:
    from app.features.publish.tiktok import router as tiktok_router
except ModuleNotFoundError:
    tiktok_router = None


# Configure logging on module import
configure_logging()
logger = get_logger(__name__)

_HEALTH_DB_CACHE_SECONDS = 60
_HEALTH_DB_TIMEOUT_SECONDS = 5
_TRUE_ENV_VALUES = {"1", "true", "yes"}
_health_db_cache = {
    "checked_at": 0.0,
    "healthy": True,
    "error": None,
}
settings = get_settings()


def _video_route_fingerprint() -> dict:
    """Expose non-secret video routing state for deployment verification."""
    current_settings = get_settings()
    profile_16 = get_duration_profile_for_creation_mode(16, "automated")
    profile_32 = get_duration_profile_for_creation_mode(32, "automated")
    return {
        "segmented_route_enabled": bool(getattr(current_settings, "veo_enable_segmented_route", False)),
        "tier_16_route": profile_16.route,
        "tier_32_route": profile_32.route,
    }


def _trusted_hosts_from_settings() -> list[str]:
    hosts = {"localhost", "127.0.0.1", "[::1]", "testserver"}
    if settings.app_url:
        parsed = urlparse(settings.app_url)
        if parsed.hostname:
            hosts.add(parsed.hostname)
            if not parsed.hostname.startswith("www."):
                hosts.add(f"www.{parsed.hostname}")
    return sorted(hosts)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    startup_recovery_task = None
    logger.info(
        "application_startup",
        environment=settings.environment,
        debug=settings.debug,
        **google_ai_context_fingerprint(settings),
        video_routes=_video_route_fingerprint(),
    )
    logger.info(
        "gemini_provider_alignment_verified",
        gemini_provider=settings.gemini_provider,
        gemini_deep_research_provider=settings.gemini_deep_research_provider,
        gemini_api_fallback_enabled=settings.gemini_api_fallback_enabled,
    )
    
    # Defer Supabase client creation until the first real database operation.
    # A bad deployment secret should not prevent the web process from booting.
    logger.info("supabase_client_initialization_deferred")

    schedulers_disabled = _env_flag_enabled("DISABLE_BACKGROUND_SCHEDULERS")
    if schedulers_disabled:
        logger.info("background_schedulers_disabled")
    else:
        scheduler.add_job(
            _run_meta_publish_scheduler_job_sync,
            "interval",
            minutes=1,
            id="meta_publish_dispatch",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30,
        )
        scheduler.add_job(
            _run_blog_publish_scheduler_job_sync,
            "interval",
            minutes=1,
            id="blog_publish_dispatch",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30,
        )
        scheduler.start()
        logger.info("publish_scheduler_started", interval_minutes=1)
        logger.info("blog_publish_scheduler_started", interval_minutes=1)

    if _env_flag_enabled("DISABLE_STARTUP_RECOVERY_CHECKS"):
        logger.info("startup_recovery_checks_disabled")
    else:
        startup_recovery_task = asyncio.create_task(_run_startup_recovery_checks())

    yield

    if startup_recovery_task and not startup_recovery_task.done():
        startup_recovery_task.cancel()
        with suppress(asyncio.CancelledError):
            await startup_recovery_task

    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("publish_scheduler_stopped")
        logger.info("blog_publish_scheduler_stopped")
    logger.info("application_shutdown")


async def _run_startup_recovery_checks() -> None:
    """Run optional recovery scans without blocking ASGI startup/liveness."""
    try:
        recoverable_batch_ids = await asyncio.to_thread(
            find_recoverable_stalled_batch_ids,
            limit=1,
            max_age_hours=6,
        )
        recovered_batches = [
            batch_id
            for batch_id in recoverable_batch_ids
            if schedule_recovered_batch_discovery(
                batch_id,
                reason="startup_recovery",
            )
        ]
    except Exception as exc:
        recovered_batches = []
        logger.warning("startup_batch_recovery_failed", error=str(exc))
    if recovered_batches:
        logger.info("startup_batch_recovery_scheduled", batch_ids=recovered_batches)

    try:
        recovered_topic_runs = await asyncio.to_thread(
            recover_stalled_topic_research_runs,
            limit=1,
            max_age_hours=6,
        )
    except Exception as exc:
        recovered_topic_runs = []
        logger.warning("startup_topic_research_recovery_failed", error=str(exc))
    if recovered_topic_runs:
        logger.info("startup_topic_research_recovery_scheduled", run_ids=recovered_topic_runs)

    try:
        cron_monitoring = await asyncio.to_thread(get_topic_research_cron_monitoring)
    except Exception as exc:
        logger.warning("startup_topic_cron_monitoring_failed", error=str(exc))
    else:
        logger.info("startup_topic_cron_monitoring", **cron_monitoring)


def _probe_database_health() -> bool:
    supabase = get_supabase()
    return supabase.health_check()


def _env_flag_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUE_ENV_VALUES


def _request_prefers_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return request.method.upper() == "GET" and "text/html" in accept


def _looks_like_database_dependency_error(error_text: str) -> bool:
    normalized = error_text.lower()
    return any(
        marker in normalized
        for marker in (
            "supabase",
            "postgrest",
            "database",
            "connection timed out",
            "read operation timed out",
            "connection terminated due to connection timeout",
            "522",
            "503",
            "504",
        )
    )


def _database_unavailable_response(request: Request, error_text: str) -> HTMLResponse:
    correlation_id = getattr(request.state, "correlation_id", None) or request.headers.get("X-Correlation-ID", "")
    safe_error = "The database API is temporarily unavailable. Please retry in a few minutes."
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lippe Lift Studio - Database unavailable</title>
  <style>
    :root {{ color-scheme: light; --ink:#17211b; --muted:#5d6b61; --bg:#f4efe5; --card:#fffaf0; --accent:#bb4a2c; }}
    body {{ margin:0; min-height:100vh; display:grid; place-items:center; background:radial-gradient(circle at 20% 10%, #ffe0b7, transparent 32%), var(--bg); color:var(--ink); font:16px/1.5 Georgia, 'Times New Roman', serif; }}
    main {{ max-width:680px; margin:32px; padding:36px; background:var(--card); border:1px solid #e5d7bd; box-shadow:0 24px 80px rgba(45,35,20,.12); }}
    h1 {{ margin:0 0 12px; font-size:clamp(32px, 6vw, 58px); line-height:.95; letter-spacing:-.04em; }}
    p {{ margin:0 0 16px; color:var(--muted); }}
    a {{ color:var(--accent); font-weight:700; }}
    code {{ background:#efe3cf; padding:2px 6px; border-radius:4px; }}
  </style>
</head>
<body>
  <main>
    <h1>Studio database is recovering.</h1>
    <p>{safe_error}</p>
    <p>The web service is online, but Supabase is currently returning timeout/gateway errors for data requests. Browser routes are being held on this page instead of showing a raw internal error.</p>
    <p><a href="/health">Check readiness</a> or try reloading shortly.</p>
    <p><small>Correlation: <code>{correlation_id or "unavailable"}</code></small></p>
  </main>
</body>
</html>"""
    return HTMLResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=html)


def _run_meta_publish_scheduler_job_sync() -> None:
    """Run the async publish scheduler in APScheduler's thread executor."""
    asyncio.run(run_scheduled_publish_job())


def _run_blog_publish_scheduler_job_sync() -> None:
    """Run the async blog scheduler in APScheduler's thread executor."""
    asyncio.run(run_scheduled_blog_publish_job())


# Create FastAPI application
app = FastAPI(
    title="Lippe Lift Studio UGC System",
    description="Deterministic UGC video production system",
    version="1.0.0",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
    lifespan=lifespan
)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=_trusted_hosts_from_settings())
app.mount("/static", StaticFiles(directory="static"), name="static")
TIKTOK_VERIFICATION_FILENAME = "tiktokfMAUp90SKLqchsEPpV3O5uLRr3ySu5h7.txt"
TIKTOK_SANDBOX_VERIFICATION_FILENAME = "tiktokdcXzbIXpURopZpk1bkFGKLkXFMtWeX9T.txt"


# Middleware for correlation IDs
@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Add correlation ID to each request."""
    correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    set_correlation_id(correlation_id)
    request.state.correlation_id = correlation_id

    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id

    return response


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Redirect unauthenticated requests to login."""
    redirect = None if request.url.path == "/" else await require_auth(request)
    if redirect is not None:
        return redirect

    response = await call_next(request)

    # If middleware flagged a refreshed session, update the cookie
    new_session = getattr(request.state, "new_session", None)
    if new_session:
        from app.features.auth.middleware import encode_session_cookie
        settings = get_settings()
        cookie_data = {
            "access_token": new_session["access_token"],
            "refresh_token": new_session["refresh_token"],
        }
        cookie_value = encode_session_cookie(cookie_data, settings.token_encryption_key)
        response.set_cookie(
            key=settings.session_cookie_name,
            value=cookie_value,
            max_age=settings.session_max_age,
            httponly=True,
            secure=settings.is_production,
            samesite="lax",
        )

    return response


# Global exception handler
@app.exception_handler(FlowForgeException)
async def flowforge_exception_handler(request: Request, exc: FlowForgeException):
    """Handle custom Lippe Lift Studio exceptions."""
    logger.error(
        "flowforge_exception",
        code=exc.code,
        message=exc.message,
        details=exc.details,
        path=request.url.path
    )

    error_text = f"{exc.message} {exc.details or ''}"
    if exc.status_code >= 500 and _request_prefers_html(request) and _looks_like_database_dependency_error(error_text):
        return _database_unavailable_response(request, error_text)
    
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_response().model_dump()
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Normalize framework-level HTTP errors into the shared error envelope."""
    detail = exc.detail
    if isinstance(detail, dict):
        message = str(detail.get("message") or detail.get("detail") or "Request failed")
        details = {key: value for key, value in detail.items() if key not in {"message", "detail"}}
        if not details:
            details = None
    else:
        message = str(detail or "Request failed")
        details = None

    if exc.status_code >= 500 and _request_prefers_html(request) and _looks_like_database_dependency_error(message):
        logger.warning(
            "database_dependency_error_rendered_as_html",
            path=request.url.path,
            status_code=exc.status_code,
            error=message,
        )
        return _database_unavailable_response(request, message)

    error_response = ErrorResponse(
        status=exc.status_code,
        code=error_code_for_status(exc.status_code),
        message=message,
        details=details,
    )
    return JSONResponse(status_code=exc.status_code, content=error_response.model_dump())


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions."""
    logger.exception(
        "unhandled_exception",
        error=str(exc),
        path=request.url.path
    )

    error_text = str(exc)
    if _request_prefers_html(request) and _looks_like_database_dependency_error(error_text):
        return _database_unavailable_response(request, error_text)
    
    error_response = ErrorResponse(
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        code=error_code_for_status(status.HTTP_500_INTERNAL_SERVER_ERROR),
        message="An unexpected error occurred",
        details={"error": str(exc)} if get_settings().debug else None
    )
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content=error_response.model_dump()
    )


# Liveness endpoint
@app.get("/livez")
async def live_check():
    """Lightweight process liveness endpoint for container/router health checks."""
    settings = get_settings()
    return {
        "status": "alive",
        "version": "1.0.0",
        "environment": settings.environment,
        "video_routes": _video_route_fingerprint(),
    }


# Readiness endpoint
@app.get("/health")
async def health_check():
    """
    Health check endpoint.
    Returns 200 if application is healthy, 503 otherwise.
    Per Constitution § IX: Observable Implementation
    """
    settings = get_settings()
    db_healthy = bool(_health_db_cache["healthy"])
    db_error = _health_db_cache["error"]
    now = time.monotonic()
    checked_at = float(_health_db_cache["checked_at"])
    if checked_at <= 0 or now - checked_at >= _HEALTH_DB_CACHE_SECONDS:
        try:
            db_healthy = await asyncio.wait_for(
                asyncio.to_thread(_probe_database_health),
                timeout=_HEALTH_DB_TIMEOUT_SECONDS,
            )
            db_error = None
        except asyncio.TimeoutError:
            db_healthy = False
            db_error = f"database readiness probe timed out after {_HEALTH_DB_TIMEOUT_SECONDS}s"
        except Exception as exc:
            db_healthy = False
            db_error = str(exc)
        _health_db_cache.update(
            {
                "checked_at": now,
                "healthy": db_healthy,
                "error": db_error,
            }
        )
    
    health_status = {
        "status": "healthy" if db_healthy else "unhealthy",
        "version": "1.0.0",
        "environment": settings.environment,
        "checks": {
            "database": "ok" if db_healthy else "fail"
        }
    }
    if db_error:
        health_status["checks"]["database_error"] = db_error
    
    logger.info("health_check", **health_status)
    
    if not db_healthy:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=health_status
        )
    
    return health_status


# Register routers
app.include_router(auth_router)
app.include_router(characters_router)
app.include_router(scenes_router)
app.include_router(batches_router)
app.include_router(topics_router)
app.include_router(posts_router)
app.include_router(videos_router)
app.include_router(qa_router)
app.include_router(publish_router)
app.include_router(blog_router)
if tiktok_router is not None:
    app.include_router(tiktok_router)


templates = Jinja2Templates(directory="templates")


# Root endpoint
@app.get("/")
async def root(request: Request):
    """Serve the public product page used for app review and unauthenticated visitors."""
    if await load_authenticated_user(request):
        response = RedirectResponse(url="/batches", status_code=302)
        new_session = getattr(request.state, "new_session", None)
        if new_session:
            settings = get_settings()
            cookie_data = {
                "access_token": new_session["access_token"],
                "refresh_token": new_session["refresh_token"],
            }
            cookie_value = encode_session_cookie(cookie_data, settings.token_encryption_key)
            response.set_cookie(
                key=settings.session_cookie_name,
                value=cookie_value,
                max_age=settings.session_max_age,
                httponly=True,
                secure=settings.is_production,
                samesite="lax",
            )
        return response
    return templates.TemplateResponse("public/home.html", {"request": request})

@app.get("/terms")
async def terms_page(request: Request):
    """Serve the Terms of Service."""
    return templates.TemplateResponse("legal/terms.html", {"request": request})

@app.get("/privacy")
async def privacy_page(request: Request):
    """Serve the Privacy Policy."""
    return templates.TemplateResponse("legal/privacy.html", {"request": request})


@app.get(f"/{TIKTOK_VERIFICATION_FILENAME}")
async def tiktok_url_verification():
    """Serve the TikTok URL prefix verification token at the site root."""
    return FileResponse(f"static/{TIKTOK_VERIFICATION_FILENAME}", media_type="text/plain")


@app.get(f"/{TIKTOK_SANDBOX_VERIFICATION_FILENAME}")
async def tiktok_sandbox_url_verification():
    """Serve the TikTok sandbox URL prefix verification token at the site root."""
    return FileResponse(f"static/{TIKTOK_SANDBOX_VERIFICATION_FILENAME}", media_type="text/plain")


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level=settings.log_level.lower()
    )
