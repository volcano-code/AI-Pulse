from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

UTC = timezone.utc

def utcnow() -> datetime:
    return datetime.now(UTC)

def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        try:
            dt = parsedate_to_datetime(value)
        except (ValueError, TypeError, OverflowError):
            return None
    # A timezone-less date is not silently interpreted as UTC.
    if dt.tzinfo is None:
        return None
    return dt.astimezone(UTC)
