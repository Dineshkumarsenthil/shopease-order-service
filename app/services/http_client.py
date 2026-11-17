import httpx

from app.core.config import settings


def new_client() -> httpx.Client:
    """Single place that builds HTTP clients (tests replace this with a mock transport)."""
    return httpx.Client(timeout=settings.HTTP_TIMEOUT_SECONDS)
