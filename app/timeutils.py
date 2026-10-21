from datetime import datetime, timezone


def to_utc_iso(dt: datetime) -> str:
    """ISO timestamp that always says it is UTC, so browsers show the right local time."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()