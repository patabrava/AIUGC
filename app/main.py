"""
FLOW-FORGE Main Application
FastAPI application with error handling and middleware.
Per Constitution § I: Canon Supremacy
"""

import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger, set_correlation_id
from app.core.errors import FlowForgeException, ErrorResponse
from app.adapters.supabase_client import get_supabase
from app.features.batches.handlers import router as batches_router
from app.features.topics.handlers import router as topics_router
from app.features.posts.handlers import router as posts_router
from app.features.videos.handlers import router as videos_router
from app.features.qa.handlers import router as qa_router
from app.features.publish.handlers import router as publish_router


# Configure logging on module import
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    settings = get_settings()
    logger.info(
        "application_startup",
        environment=settings.environment,
        debug=settings.debug
    )
    
    # Initialize Supabase connection
    supabase = get_supabase()
    if not supabase.health_check():
        logger.error("supabase_connection_failed_on_startup")
    
    yield
    
    logger.info("application_shutdown")


# Create FastAPI application
app = FastAPI(
    title="FLOW-FORGE UGC System",
    description="Deterministic UGC video production system",
    version="1.0.0",
    lifespan=lifespan
)

app.mount("/static", StaticFiles(directory="static"), name="static")


# Middleware for correlation IDs
@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    """Add correlation ID to each request."""
    correlation_id = request.headers.get("X-Correlation-ID") or str(uuid.uuid4())
    set_correlation_id(correlation_id)
    
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    
    return response


# Global exception handler
@app.exception_handler(FlowForgeException)
async def flowforge_exception_handler(request: Request, exc: FlowForgeException):
    """Handle custom FLOW-FORGE exceptions."""
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


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle unexpected exceptions."""
    logger.exception(
        "unhandled_exception",
        error=str(exc),
        path=request.url.path
    )
    
    error_response = ErrorResponse(
        code="internal_error",
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
    supabase = get_supabase()
    
    db_healthy = supabase.health_check()
    
    health_status = {
        "status": "healthy" if db_healthy else "unhealthy",
        "version": "1.0.0",
        "environment": settings.environment,
        "checks": {
            "database": "ok" if db_healthy else "fail"
        }
    }
    
    logger.info("health_check", **health_status)
    
    if not db_healthy:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=health_status
        )
    
    return health_status


# Register routers
app.include_router(batches_router)
app.include_router(topics_router)
app.include_router(posts_router)
app.include_router(videos_router)
app.include_router(qa_router)
app.include_router(publish_router)


# Root endpoint
@app.get("/")
async def root():
    """Root endpoint - redirect to batches dashboard."""
    return RedirectResponse(url="/batches", status_code=status.HTTP_302_FOUND)


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
