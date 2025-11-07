"""FLOW-FORGE Adapters Module"""

from .sora_client import get_sora_client  # noqa: F401

try:  # pragma: no cover - defensive fallback for python 3.9 environments
    from .veo_client import get_veo_client  # noqa: F401
except Exception as import_error:
    import structlog

    structlog.get_logger(__name__).warning(
        "veo_client_unavailable",
        error=str(import_error),
        message="VEO adapter skipped; continue without VEO support"
    )
