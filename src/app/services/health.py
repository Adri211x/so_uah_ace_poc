"""Health check service.

Services contain BUSINESS LOGIC. In this simple example there is not much,
but in a real service you might check database connectivity, external APIs,
queue health, etc.

Notice this service:
  - Does NOT know about HTTP (no Request, no Response, no status codes)
  - Does NOT access the database directly (it would call a repository)
  - Returns a schema, not a dict -- this gives you type safety
"""

import logging

from src.app.schemas.health import HealthResponse

logger = logging.getLogger(__name__)


class HealthService:
    """Service that checks the application health."""

    def check(self) -> HealthResponse:
        """Run health checks and return the current status.

        Returns:
            HealthResponse with status and version info.
        """
        logger.debug("Running health check")

        return HealthResponse(
            status="healthy",
            version="0.1.0",
        )
