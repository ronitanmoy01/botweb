import uuid

from app.models.catalog import MovieStatus

_STATUSES = {status.value for status in MovieStatus}


def valid_uuids(values: list[str]) -> list[uuid.UUID]:
    """Parse form ids and drop malformed ones, so a tampered form field cannot cause a 500."""
    parsed = []
    for value in values:
        try:
            parsed.append(uuid.UUID(value))
        except ValueError:
            continue
    return parsed


def clean_status(value: str) -> str:
    return value if value in _STATUSES else MovieStatus.DRAFT


def safe_url(value: str | None) -> str | None:
    """Return the value only if it is an absolute http(s) URL; blocks javascript:, data: and other schemes."""
    if not value:
        return None
    value = value.strip()
    if any(ord(ch) < 32 or ch == "\x7f" for ch in value):
        return None
    if not value.lower().startswith(("http://", "https://")):
        return None
    return value
