"""Application entrypoint.

This is where the FastAPI application is created and configured.
All routers (groups of endpoints) are registered here.

To run the app:
    just dev            (with auto-reload, for development)
    just run            (without auto-reload, production-like)
    uv run uvicorn src.app.main:app --reload   (manual command)
"""

import logging

from fastapi import FastAPI

from src.app.api.health import router as health_router

logger = logging.getLogger(__name__)

app = FastAPI(
    title="so_ua_ace_poc",
    version="0.1.0",
)

# Register routers. Each router is a group of related endpoints.
# As the app grows, you add more routers here:
#   app.include_router(users_router, prefix="/api/v1", tags=["users"])
#   app.include_router(orders_router, prefix="/api/v1", tags=["orders"])
app.include_router(health_router, tags=["health"])

logger.info("Application initialized")
