"""
app/rate_limiter.py

Robust SlowAPI limiter setup for the AML Entity Resolution API.

Behavior:
- Prefer Redis storage (REDIS_URL). If Redis is reachable and USE_REDIS=true, uses Redis via storage_uri.
- If Redis is unavailable, falls back to an in-process memory limiter (dev only).
- Exposes `limiter` (Limiter instance) and `rate_limit_exceeded_handler` (function to register with FastAPI).
- Respects env vars for limits and adds X-RateLimit-* headers.
"""

import os
import logging
from typing import Optional

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi import _rate_limit_exceeded_handler
from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# -----------------------------
# Configuration (env or defaults)
# -----------------------------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
USE_REDIS = os.getenv("USE_REDIS", "true").lower() == "true"

# Ingest and burst limits — keep as string style accepted by limits/slowapi
INGEST_RATE_LIMIT = os.getenv("RATE_LIMIT_INGEST", "100/minute")
BURST_RATE_LIMIT = os.getenv("RATE_LIMIT_BURST", "10/second")
GLOBAL_FALLBACK_LIMIT = os.getenv("RATE_LIMIT_GLOBAL", "1000/hour")

# If you want these as integers (for headers) you can parse them; normally X-RateLimit-Limit shows the textual limit.
DEFAULT_HEADERS_ENABLED = os.getenv("RATE_LIMIT_HEADERS_ENABLED", "true").lower() == "true"

# -----------------------------
# Storage detection helper
# -----------------------------
def _test_redis_connection(url: str) -> bool:
    """
    Ping Redis to check availability. Returns True if reachable.
    """
    try:
        import redis as _redis  # redis-py
        client = _redis.from_url(url, decode_responses=True)
        client.ping()
        return True
    except Exception as e:
        logger.warning("Redis ping failed: %s", e)
        return False

# -----------------------------
# Create limiter (Redis if available)
# -----------------------------
def _build_limiter() -> Limiter:
    """
    Build a Limiter instance. Prefer storage_uri pointing to Redis; fallback to memory.
    """
    if USE_REDIS and REDIS_URL:
        if _test_redis_connection(REDIS_URL):
            logger.info("Rate limiter using Redis at %s", REDIS_URL)
            # use storage_uri per slowapi docs (most portable)
            return Limiter(
                key_func=get_remote_address,
                storage_uri=REDIS_URL,
                default_limits=[GLOBAL_FALLBACK_LIMIT],
                headers_enabled=DEFAULT_HEADERS_ENABLED
            )
        else:
            logger.warning("USE_REDIS=true but Redis is unreachable; falling back to in-memory limiter.")
    # memory fallback
    logger.info("Rate limiter using in-memory storage (development only)")
    return Limiter(
        key_func=get_remote_address,
        default_limits=[GLOBAL_FALLBACK_LIMIT],
        headers_enabled=DEFAULT_HEADERS_ENABLED
    )

# module-level limiter instance (import this from main.py)
limiter: Limiter = _build_limiter()

# -----------------------------
# Recommended per-endpoint decorators:
#     @limiter.limit(INGEST_RATE_LIMIT)
#     @limiter.limit(BURST_RATE_LIMIT)
# Note: decorator order matters — the route decorator must be above limiter.limit.
# -----------------------------

# -----------------------------
# Custom 429 handler that returns JSON matching your API style.
# Keep the signature (request, exc) to be registered with FastAPI:
#    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
# If you prefer slowapi's default JSON body, register _rate_limit_exceeded_handler instead.
# -----------------------------
def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """
    Standardized JSON response for rate-limited requests.
    The RateLimitExceeded exception object may carry a .detail message depending on the limits implementation.
    We avoid brittle string parsing and return consistent fields. If `exc` has usable details we include them.
    """
    # Try to extract a numeric retry-after if available, else send default 60
    retry_after = 60
    try:
        # some limiters include a "retry_after" attribute or info in exc.detail
        if hasattr(exc, "retry_after"):
            retry_after = int(getattr(exc, "retry_after"))
        elif isinstance(exc.detail, str) and exc.detail.isdigit():
            retry_after = int(exc.detail)
        # else keep default
    except Exception:
        pass

    payload = {
        "status": "error",
        "error_type": "rate_limit_exceeded",
        "message": "Too many requests. Slow down and try again.",
        "retry_after_seconds": retry_after,
    }
    headers = {
        "Retry-After": str(retry_after),
        "X-RateLimit-Remaining": "0",
    }
    return JSONResponse(status_code=429, content=payload, headers=headers)
