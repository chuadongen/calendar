"""Sprint weeks run Monday to Sunday. Planning on Sunday targets the following Monday."""

from datetime import date, datetime, time, timedelta

from app.config import settings


def today() -> date:
    return datetime.now(settings.tz).date()


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def current_week() -> date:
    return week_start(today())


def planning_week() -> date:
    """The week being planned: next week from Friday onwards, otherwise this week."""
    t = today()
    return week_start(t) + timedelta(days=7) if t.weekday() >= 4 else week_start(t)


def week_bounds(start: date) -> tuple[datetime, datetime]:
    begin = datetime.combine(start, time.min)
    return begin, begin + timedelta(days=7)


def parse_week(value: str | None, default: date) -> date:
    if not value:
        return default
    return week_start(date.fromisoformat(value))


def to_local_naive(dt: datetime) -> datetime:
    """Convert an aware datetime to naive wall-clock time in the configured timezone."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(settings.tz).replace(tzinfo=None)


def to_aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=settings.tz) if dt.tzinfo is None else dt


def parse_iso(value: str) -> datetime:
    return to_local_naive(datetime.fromisoformat(value.replace("Z", "+00:00")))
