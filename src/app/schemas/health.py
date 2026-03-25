"""Health check schemas.

Schemas define WHAT DATA looks like when it enters or leaves the API.
Pydantic validates it automatically -- if someone sends wrong data,
they get a clear error without you writing any validation code.
"""

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response schema for the health check endpoint.

    This is what the API returns when someone calls GET /health.
    Pydantic converts this to JSON automatically.
    """

    status: str
    version: str
