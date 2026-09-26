"""Timezone helpers shared by services (per-user timezone with safe fallback)."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings

logger = logging.getLogger(__name__)


def resolve_timezone(name: Optional[str]) -> ZoneInfo:
    """Return a ZoneInfo for `name`, falling back to DEFAULT_TIMEZONE.

    `users.timezone` is free text; an invalid value must never crash a
    briefing or a chat reply.
    """
    for candidate in (name, settings.default_timezone, "UTC"):
        if not candidate:
            continue
        try:
            return ZoneInfo(str(candidate))
        except (ZoneInfoNotFoundError, ValueError):
            if candidate == name:
                logger.warning("Invalid user timezone %r, using default", name)
    return ZoneInfo("UTC")


def local_day_bounds_utc(tz: ZoneInfo, now: Optional[datetime] = None) -> tuple[datetime, datetime]:
    """Return [start, end) of the current local day in `tz`, as UTC datetimes."""
    local_now = (now or datetime.now(timezone.utc)).astimezone(tz)
    start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)
