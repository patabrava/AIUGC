"""
Lippe Lift Studio Main Application
FastAPI application with error handling and middleware.
Per Constitution § I: Canon Supremacy
"""

import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlparse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings, google_ai_context_fingerprint
from app.core.logging import configure_logging, get_logger, set_correlation_id
from app.core.errors import FlowForgeException, ErrorResponse, error_code_for_status
from app.adapters.supabase_client import get_supabase
from app.features.batches.handlers import router as batches_router
from app.features.topics.handlers import recover_stalled_batches, router as topics_router
from app.features.topics.hub import recover_stalled_topic_research_runs
from app.features.topics.queries import get_topic_research_cron_monitoring
from app.features.posts.handlers import router as posts_router
from app.features.videos.handlers import router as videos_router
from app.features.qa.handlers import router as qa_router
from app.features.publish.handlers import router as publish_router, run_scheduled_publish_job
from app.features.blog.handlers import router as blog_router, run_scheduled_blog_publish_job
from app.features.auth.handlers import router as auth_router
from app.features.auth.middleware import require_auth, is_public_path

try:
    from app.features.publish.tiktok import router as tiktok_router
except ModuleNotFoundError:
    tiktok_router = None


# Configure logging on module import
configure_logging()
logger = get_logger(__name__)
settings = get_settings()


def _trusted_hosts_from_settings() -> list[str]:
    hosts = {"localhost", "127.0.0.1", "[::1]", "testserver"}
    if settings.app_url:
        parsed = urlparse(settings.app_url)
        if parsed.hostname:
            hosts.add(parsed.hostname)
    return sorted(hosts)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    scheduler = AsyncIOScheduler(timezone="UTC")
    logger.info(
        "application_startup",
        environment=settings.environment,
        debug=settings.debug,
        **google_ai_context_fingerprint(settings),
    )
    if not settings.google_ai_keys_aligned():
        logger.warning(
            "google_ai_key_alignment_mismatch",
            gemini_api_key_present=bool(settings.gemini_api_key),
            google_ai_api_key_present=bool(settings.google_ai_api_key),
            message="GEMINI_API_KEY and GOOGLE_AI_API_KEY differ; VEO requests will keep using the active Google AI key fingerprint"
        )
    logger.info(
        "google_ai_key_alignment_verified",
        gemini_api_key_present=bool(settings.gemini_api_key),
        google_ai_api_key_present=bool(settings.google_ai_api_key),
    )
    
    # Defer Supabase client creation until the first real database operation.
    # A bad deployment secret should not prevent the web process from booting.
    logger.info("supabase_client_initialization_deferred")

    scheduler.add_job(
        run_scheduled_publish_job,
        "interval",
        minutes=1,
        id="meta_publish_dispatch",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    scheduler.start()
    logger.info("publish_scheduler_started", interval_minutes=1)

    scheduler.add_job(
        run_scheduled_blog_publish_job,
        "interval",
        minutes=1,
        id="blog_publish_dispatch",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=30,
    )
    logger.info("blog_publish_scheduler_started", interval_minutes=1)

    try:
        recovered_batches = recover_stalled_batches(limit=1, max_age_hours=6)
    except Exception as exc:
        recovered_batches = []
        logger.warning("startup_batch_recovery_failed", error=str(exc))
    if recovered_batches:
        logger.info("startup_batch_recovery_scheduled", batch_ids=recovered_batches)

    try:
        recovered_topic_runs = recover_stalled_topic_research_runs(limit=1, max_age_hours=6)
    except Exception as exc:
        recovered_topic_runs = []
        logger.warning("startup_topic_research_recovery_failed", error=str(exc))
    if recovered_topic_runs:
        logger.info("startup_topic_research_recovery_scheduled", run_ids=recovered_topic_runs)

    try:
        cron_monitoring = get_topic_research_cron_monitoring()
    except Exception as exc:
        logger.warning("startup_topic_cron_monitoring_failed", error=str(exc))
    else:
        logger.info("startup_topic_cron_monitoring", **cron_monitoring)
    
    yield

    scheduler.shutdown(wait=False)
    logger.info("publish_scheduler_stopped")
    logger.info("blog_publish_scheduler_stopped")
    logger.info("application_shutdown")


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
TIKTOK_VERIFICATION_FILENAME = "tiktokM1iYTqs7dJ1raJALxFS3sJhodU2gFDuk.txt"
TIKTOK_SANDBOX_VERIFICATION_FILENAME = "tiktokdcXzbIXpURopZpk1bkFGKLkXFMtWeX9T.txt"


# Middleware for correlation IDs
@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Add correlation ID to each request."""
    correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    set_correlation_id(correlation_id)

    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id

    return response


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Redirect unauthenticated requests to login."""
    redirect = await require_auth(request)
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


# Health endpoint
@app.get("/health")
async def health_check():
    """
    Health check endpoint.
    Returns 200 if application is healthy, 503 otherwise.
    Per Constitution § IX: Observable Implementation
    """
    settings = get_settings()
    db_healthy = True
    db_error = None
    try:
        supabase = get_supabase()
        db_healthy = supabase.health_check()
    except Exception as exc:
        db_healthy = False
        db_error = str(exc)
    
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
app.include_router(batches_router)
app.include_router(topics_router)
app.include_router(posts_router)
app.include_router(videos_router)
app.include_router(qa_router)
app.include_router(publish_router)
app.include_router(blog_router)
if tiktok_router is not None:
    app.include_router(tiktok_router)


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint - redirect to batches dashboard."""
    return RedirectResponse(url="/batches", status_code=status.HTTP_302_FOUND)


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
