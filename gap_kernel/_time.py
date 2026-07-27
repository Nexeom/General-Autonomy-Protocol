"""UTC time helpers.

Every timestamp GAP produces is timezone-aware UTC.

``datetime.utcnow()`` returns a *naive* datetime. A naive datetime raises
``TypeError`` when compared against an aware one, and aware timestamps arrive
routinely — from deserialized JSON, from a caller's ISO-8601 input, from any
real upstream system. Where that comparison sits inside an expiry check or the
reconciler's drift arithmetic, the result is not a clean error but a governance
control that crashes instead of deciding.

Use ``utcnow()`` for the current time and ``ensure_utc()`` at every boundary
where a datetime enters the system from outside.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    """
    Normalize a datetime to timezone-aware UTC.

    A naive input is assumed to be UTC — that is what every naive timestamp in
    GAP's own history meant — so this is safe to apply to persisted values.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
