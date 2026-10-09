"""Weekly dashboard: where the time went, estimated sleep, training load and energy.

All figures are derived from what is already recorded: plan blocks, Google Calendar
events (sorted into categories by their colour), timed Todoist tasks, Strava
activities and retrospectives.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.categories import CATEGORIES, COLOR_TO_CATEGORY
from app.integrations import gcal, strava, todoist
from app.models import Goal, PlanItem, Retro
from app.weeks import week_bounds

# Events starting before this hour belong to the previous day (a late night, not an early morning).
DAY_ROLLOVER_HOUR = 4
MAX_SLEEP_HOURS = 14
MUSCLES = ("chest", "shoulders", "biceps", "triceps", "forearms", "abs", "back",
           "glutes", "quads", "hamstrings", "calves")
ENERGY = {1: ("😫", "Drained"), 2: ("😕", "Low"), 3: ("😐", "Okay"), 4: ("🙂", "Good"), 5: ("😄", "Great")}


@dataclass
class Event:
    title: str
    start: datetime
    end: datetime
    category: str  # a CATEGORIES key, or "other" when the colour is not mapped
    source: str


@dataclass
class Timeline:
    events: list[Event] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def gather_timeline(db: Session, begin: datetime, end: datetime, prefs: dict, calendar=None, todo=None) -> Timeline:
    """Every timed commitment between begin and end, each with a category."""
    tl = Timeline()
    for item in db.query(PlanItem).filter(PlanItem.start >= begin, PlanItem.start < end).all():
        tl.events.append(Event(item.title, item.start, item.start + timedelta(minutes=item.duration_min),
                               item.category if item.category in CATEGORIES else "life", "plan"))
    linked = {i.todoist_task_id for i in db.query(PlanItem).filter(PlanItem.todoist_task_id != "").all()}

    try:
        cal = calendar if calendar is not None else (gcal.Calendar() if gcal.connected() else None)
        if cal is not None:
            ignored = set(prefs.get("ignored_calendar_ids") or [])
            for cal_id in [c for c in (prefs.get("busy_calendar_ids") or ["primary"]) if c not in ignored]:
                for ev in cal.events(cal_id, begin, end):
                    if gcal.is_planner_event(ev) or ev.get("status") == "cancelled":
                        continue
                    start, finish, all_day = gcal.event_span(ev)
                    if all_day:
                        continue
                    cat = COLOR_TO_CATEGORY.get(str(ev.get("colorId", "")), "other")
                    tl.events.append(Event(ev.get("summary", "(busy)"), start, finish, cat, "gcal"))
        else:
            tl.errors.append("Connect Google Calendar to include your calendar events.")
    except Exception as exc:
        tl.errors.append(f"Could not read Google Calendar: {exc}")

    try:
        td = todo if todo is not None else todoist.Todoist()
        if td.configured:
            goals = db.query(Goal).filter(Goal.todoist_label != "").all()
            area_by_label = {g.todoist_label: g.area for g in goals}
            for task in td.open_tasks() + td.completed_between(begin, end):
                tid = str(task.get("task_id") or task.get("id"))
                start = todoist.task_start(task)
                if tid in linked or start is None or not (begin <= start < end):
                    continue
                area = next((area_by_label[l] for l in task.get("labels") or [] if l in area_by_label), "other")
                tl.events.append(Event(task.get("content", ""), start,
                                       start + timedelta(minutes=todoist.task_duration(task)), area, "todoist"))
    except Exception as exc:
        tl.errors.append(f"Could not read Todoist: {exc}")
    tl.events.sort(key=lambda e: e.start)
    return tl


def distribution(events: list[Event], begin: datetime, end: datetime) -> list[dict]:
    """Hours per category inside [begin, end), largest first. Overlapping events both count."""
    minutes: dict[str, float] = {}
    for e in events:
        s, f = max(e.start, begin), min(e.end, end)
        if f > s:
            minutes[e.category] = minutes.get(e.category, 0) + (f - s).total_seconds() / 60
    rows = []
    for key, m in minutes.items():
        meta = CATEGORIES.get(key, {"label": "Uncategorised", "color": "#9a968b"})
        rows.append({"key": key, "label": meta["label"], "color": meta["color"], "hours": round(m / 60, 1)})
    return sorted(rows, key=lambda r: -r["hours"])


def sleep_target(prefs: dict) -> float:
    """Hours from bedtime to wake-up, e.g. 23:00 to 07:00 is 8."""
    def minutes(v: str) -> int:
        h, m = v.split(":")
        return int(h) * 60 + int(m)
    return round(((minutes(prefs["day_start"]) - minutes(prefs["day_end"])) % 1440) / 60, 1)


def _day_of(dt: datetime) -> date:
    return (dt - timedelta(hours=DAY_ROLLOVER_HOUR)).date()


def sleep_nights(events: list[Event], week: date) -> list[dict]:
    """Estimated sleep per night: last event of a day to the first event of the next day."""
    last_end: dict[date, datetime] = {}
    first_start: dict[date, datetime] = {}
    for e in events:
        d = _day_of(e.start)
        last_end[d] = max(last_end.get(d, e.end), e.end)
        first_start[d] = min(first_start.get(d, e.start), e.start)
    nights = []
    for offset in range(7):
        d = week + timedelta(days=offset)
        bed, wake = last_end.get(d), first_start.get(d + timedelta(days=1))
        hours = round((wake - bed).total_seconds() / 3600, 1) if bed and wake and wake > bed else None
        if hours is not None and hours > MAX_SLEEP_HOURS:
            hours = None  # an empty day in the calendar, not a 20 hour sleep
        nights.append({"date": d, "bed": bed, "wake": wake, "hours": hours})
    return nights


def muscles_for(title: str, keywords: dict[str, list[str]]) -> set[str]:
    text = title.lower()
    hit: set[str] = set()
    for word, muscles in keywords.items():
        if re.search(r"\b" + re.escape(word.lower()) + r"s?\b", text):
            hit.update(muscles)
    return hit


def training(events: list[Event], activities: list[dict], keywords: dict, begin: datetime, end: datetime) -> dict:
    """Workout sessions (Strava first, calendar blocks that Strava did not log) and muscle counts."""
    sessions = [dict(a) for a in activities]
    for e in events:
        if e.category != "fitness" or not (begin <= e.start < end):
            continue
        if any(abs((a["start"] - e.start).total_seconds()) < 3 * 3600 and a["start"].date() == e.start.date()
               for a in activities):
            continue  # Strava already logged this one
        sessions.append({"name": e.title, "type": "Calendar", "start": e.start,
                         "minutes": round((e.end - e.start).total_seconds() / 60), "km": 0, "source": e.source})
    sessions.sort(key=lambda s: s["start"])
    counts = {m: 0 for m in MUSCLES}
    for s in sessions:
        s["muscles"] = sorted(muscles_for(f"{s['name']} {s['type']}", keywords))
        for m in s["muscles"]:
            if m in counts:
                counts[m] += 1
    return {"sessions": sessions, "muscles": counts, "minutes": sum(s["minutes"] for s in sessions)}


def build(db: Session, week: date, prefs: dict, review: dict, calendar=None, todo=None,
          activities: list[dict] | None = None) -> dict:
    begin, end = week_bounds(week)
    # One extra day so Sunday night's sleep can see Monday's first event.
    tl = gather_timeline(db, begin, end + timedelta(days=1), prefs, calendar, todo)
    errors = list(tl.errors)

    if activities is None:
        activities = []
        if strava.connected():
            try:
                activities = strava.activities(begin, end)
            except Exception as exc:
                errors.append(f"Could not read Strava: {exc}")
        elif strava.configured():
            errors.append("Connect Strava in Settings to log workouts automatically.")

    dist = distribution(tl.events, begin, end)
    nights = sleep_nights(tl.events, week)
    known = [n["hours"] for n in nights if n["hours"] is not None]
    retros = db.query(Retro).filter(Retro.week_start <= week).order_by(Retro.week_start.desc()).limit(8).all()

    done, still_open = len(review["completed"]), review["open_total"]
    return {
        "errors": errors,
        "distribution": dist,
        "total_hours": round(sum(r["hours"] for r in dist), 1),
        "nights": nights,
        "sleep_avg": round(sum(known) / len(known), 1) if known else None,
        "short_nights": sum(1 for h in known if h < 7),
        "training": training(tl.events, activities, prefs.get("muscle_keywords", {}), begin, end),
        "tasks": {"done": done, "open": still_open,
                  "rate": round(done / (done + still_open) * 100) if done + still_open else None},
        "energy": [{"week": r.week_start, "value": r.energy, "emoji": ENERGY[r.energy][0], "label": ENERGY[r.energy][1]}
                   for r in reversed(retros) if r.energy in ENERGY],
    }
