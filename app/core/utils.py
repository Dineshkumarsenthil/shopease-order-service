from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC timestamp (stored as UTC in the database)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
