"""Clash detection and auto-fill. Pure functions, no I/O.

Two blocks clash when the gap between them is smaller than the required gap,
which is the larger of the configured buffer and the travel time between their
locations. Busy time comes from Google Calendar and existing timed Todoist tasks.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from app.prefs import travel_minutes

STEP = timedelta(minutes=15)


@dataclass
class Slot:
    start: datetime
    end: datetime
    location: str = ""
    title: str = ""
    item_id: int | None = None  # set for plan items, None for external busy time
    category: str = ""


@dataclass
class Issue:
    item_id: int
    level: str  # "clash" | "warning"
    message: str


def _hm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


def required_gap(a: Slot, b: Slot, prefs: dict) -> timedelta:
    minutes = max(int(prefs.get("buffer_min", 0)), travel_minutes(prefs, a.location, b.location))
    return timedelta(minutes=minutes)


def clashes(candidate: Slot, other: Slot, prefs: dict) -> bool:
    gap = required_gap(candidate, other, prefs)
    return candidate.start < other.end + gap and other.start < candidate.end + gap


def _describe(other: Slot, candidate: Slot, prefs: dict) -> str:
    name = other.title or "another block"
    overlaps = candidate.start < other.end and other.start < candidate.end
    if overlaps:
        return f"Overlaps {name}"
    travel = travel_minutes(prefs, candidate.location, other.location)
    if travel:
        return f"Needs {travel} min travel from/to {name}"
    return f"Less than {prefs.get('buffer_min', 0)} min buffer next to {name}"


def find_issues(items: list[Slot], busy: list[Slot], prefs: dict) -> list[Issue]:
    issues: list[Issue] = []
    day_start, day_end = _hm(prefs["day_start"]), _hm(prefs["day_end"])
    for i, item in enumerate(items):
        for other in busy + items[i + 1:]:
            if clashes(item, other, prefs):
                issues.append(Issue(item.item_id, "clash", _describe(other, item, prefs)))
                if other.item_id is not None:
                    issues.append(Issue(other.item_id, "clash", _describe(item, other, prefs)))
        if item.start.time() < day_start or (item.end.time() > day_end or item.end.date() > item.start.date()):
            issues.append(Issue(item.item_id, "warning", "Outside your day hours"))
    return issues


def _day_windows(day: date, category: str, prefs: dict) -> list[tuple[datetime, datetime]]:
    """Preferred window for the category first, then the full day as a fallback."""
    full = (datetime.combine(day, _hm(prefs["day_start"])), datetime.combine(day, _hm(prefs["day_end"])))
    preferred = prefs.get("preferred_windows", {}).get(category)
    if not preferred:
        return [full]
    pref = (datetime.combine(day, _hm(preferred[0])), datetime.combine(day, _hm(preferred[1])))
    return [pref, full]


def autofill(
    unscheduled: list[tuple[int, int, str, str, int]],
    occupied: list[Slot],
    week_start: date,
    prefs: dict,
    not_before: datetime | None = None,
) -> dict[int, datetime]:
    """Place unscheduled items into free time.

    unscheduled: (item_id, duration_min, category, location, priority) tuples.
    Items go in priority order, longest first, onto the day where that category
    has the least time so far, so work spreads across the week.
    Returns {item_id: start}. Items that do not fit are left out.
    """
    occupied = list(occupied)
    days = [week_start + timedelta(days=d) for d in range(7)]
    load: dict[tuple[date, str], int] = {}
    for s in occupied:
        if s.item_id is not None:
            key = (s.start.date(), s.category)
            load[key] = load.get(key, 0) + int((s.end - s.start).total_seconds() // 60)

    placed: dict[int, datetime] = {}
    for item_id, duration, category, location, _prio in sorted(unscheduled, key=lambda u: (u[4], -u[1])):
        length = timedelta(minutes=duration)
        ordered_days = sorted(days, key=lambda d: (load.get((d, category), 0), d))
        start = _first_fit(ordered_days, length, category, location, occupied, prefs, not_before)
        if start is None:
            continue
        placed[item_id] = start
        occupied.append(Slot(start, start + length, location, "", item_id, category))
        load[(start.date(), category)] = load.get((start.date(), category), 0) + duration
    return placed


def _first_fit(days, length, category, location, occupied, prefs, not_before):
    # Try every day's preferred window before falling back to full days.
    for pass_index in (0, 1):
        for day in days:
            windows = _day_windows(day, category, prefs)
            if pass_index >= len(windows):
                continue
            lo, hi = windows[pass_index]
            t = lo
            while t + length <= hi:
                if not_before is None or t >= not_before:
                    candidate = Slot(t, t + length, location)
                    if not any(clashes(candidate, o, prefs) for o in occupied):
                        return t
                t += STEP
    return None
