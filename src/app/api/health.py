"""Health check endpoint.

Endpoints are THIN. They:
  1. Receive the HTTP request
  2. Call a service
  3. Return the response

All the logic lives in the service, not here.

This file shows the pattern you should follow for every new endpoint.
"""

import logging

from fastapi import APIRouter

from src.app.schemas.health import HealthResponse
from src.app.services.health import HealthService

logger = logging.getLogger(__name__)

router = APIRouter()

# In a real app, this would be injected via FastAPI's Depends() system.
# For now we create it directly to keep the example simple.
health_service = HealthService()


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description="Returns the current health status of the application.",
)
def get_health() -> HealthResponse:
    """Check if the application is running and healthy."""
    return health_service.check()
