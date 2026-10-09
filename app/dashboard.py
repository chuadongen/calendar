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
from app.integrations import gcal, money, strava, todoist
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


def gather_timeline(db: Session, begin: datetime, end: datetime, prefs: dict, calendar=None, todo=None,
                    completed: list[dict] | None = None) -> Timeline:
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
            done = completed if completed is not None else td.completed_between(begin, end)
            for task in td.open_tasks() + done:
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


def is_cardio(text: str, keywords: list[str]) -> bool:
    text = text.lower()
    return any(re.search(r"\b" + re.escape(w.lower()) + r"s?\b", text) for w in keywords)


def training(events: list[Event], activities: list[dict], keywords: dict, begin: datetime, end: datetime,
             cardio_keywords: list[str] | None = None) -> dict:
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
    sessions = [x for x in sessions if begin <= x["start"] < end]
    sessions.sort(key=lambda s: s["start"])
    counts = {m: 0 for m in MUSCLES}
    cardio = {"sessions": 0, "minutes": 0}
    for s in sessions:
        text = f"{s['name']} {s['type']}"
        s["muscles"] = sorted(muscles_for(text, keywords))
        s["cardio"] = is_cardio(text, cardio_keywords or [])
        for m in s["muscles"]:
            if m in counts:
                counts[m] += 1
        if s["cardio"]:
            cardio["sessions"] += 1
            cardio["minutes"] += s["minutes"]
    return {"sessions": sessions, "muscles": counts, "cardio": cardio, "minutes": sum(s["minutes"] for s in sessions)}


TREND_WEEKS = 8


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def compare(now: list[dict], before: list[dict]) -> list[dict]:
    """Category rows for this week with last week's hours alongside, for the hover comparison."""
    prev = {r["key"]: r["hours"] for r in before}
    rows = [{**r, "prev": prev.pop(r["key"], 0.0)} for r in now]
    for r in before:
        if r["key"] in prev:  # categories that dropped to zero this week
            rows.append({**r, "hours": 0.0, "prev": r["hours"]})
    return rows


def _money(first_week: date, weeks: list[date], errors: list[str]) -> dict | None:
    if not money.configured():
        return None
    try:
        raw = money.weekly(first_week, len(weeks))
    except Exception as exc:
        errors.append(f"Could not read the money app: {exc}")
        return None
    by_week = {w.get("week_start"): w for w in raw.get("weeks", [])}
    this = by_week.get(weeks[-1].isoformat(), {})
    cats = sorted((this.get("categories") or {}).items(), key=lambda kv: -kv[1])
    return {
        "currency": raw.get("currency", ""),
        "budget": raw.get("weekly_budget"),
        "spent": this.get("spent"),
        "categories": [{"name": k, "amount": round(v, 2)} for k, v in cats],
        "trend": [{"week": w, "value": by_week.get(w.isoformat(), {}).get("spent")} for w in weeks],
    }


def build(db: Session, week: date, prefs: dict, review: dict, calendar=None, todo=None,
          activities: list[dict] | None = None) -> dict:
    weeks = [week - timedelta(weeks=i) for i in range(TREND_WEEKS - 1, -1, -1)]
    first, _ = week_bounds(weeks[0])
    begin, end = week_bounds(week)
    errors: list[str] = []

    td = todo if todo is not None else todoist.Todoist()
    completed: list[dict] = []
    if td.configured:
        try:
            completed = td.completed_between(first, end)
        except Exception as exc:
            errors.append(f"Could not read Todoist history: {exc}")

    # One extra day so Sunday night's sleep can see Monday's first event.
    tl = gather_timeline(db, first, end + timedelta(days=1), prefs, calendar, td, completed)
    errors += tl.errors

    if activities is None:
        activities = []
        if strava.connected():
            try:
                activities = strava.activities(first, end)
            except Exception as exc:
                errors.append(f"Could not read Strava: {exc}")
        elif strava.configured():
            errors.append("Connect Strava in Settings to log workouts automatically.")

    keywords, cardio_kw = prefs.get("muscle_keywords", {}), prefs.get("cardio_keywords", [])
    retros = {r.week_start: r.energy for r in db.query(Retro).filter(Retro.week_start >= weeks[0],
                                                                       Retro.week_start <= week).all()}

    trend = []
    for w in weeks:
        b, e = week_bounds(w)
        hours = [n["hours"] for n in sleep_nights(tl.events, w) if n["hours"] is not None]
        done = sum(1 for t in completed if t.get("completed_at") and b <= _local(t["completed_at"]) < e)
        trend.append({
            "week": w,
            "sleep": _avg(hours),
            "tasks": done,
            "workouts": len(training(tl.events, activities, keywords, b, e, cardio_kw)["sessions"]),
            "energy": retros.get(w) if retros.get(w) in ENERGY else None,
        })

    dist = distribution(tl.events, begin, end)
    last_dist = distribution(tl.events, begin - timedelta(days=7), begin)
    nights = sleep_nights(tl.events, week)
    known = [n["hours"] for n in nights if n["hours"] is not None]
    done, still_open = len(review["completed"]), review["open_total"]
    energy_now = retros.get(week)
    return {
        "errors": errors,
        "distribution": compare(dist, last_dist),
        "total_hours": round(sum(r["hours"] for r in dist), 1),
        "nights": nights,
        "sleep_avg": _avg(known),
        "short_nights": sum(1 for h in known if h < 7),
        "training": training(tl.events, activities, keywords, begin, end, cardio_kw),
        "tasks": {"done": done, "open": still_open,
                  "rate": round(done / (done + still_open) * 100) if done + still_open else None},
        "energy_now": {"emoji": ENERGY[energy_now][0], "label": ENERGY[energy_now][1]} if energy_now in ENERGY else None,
        "energy": [{"week": t["week"], "value": t["energy"], "emoji": ENERGY[t["energy"]][0],
                    "label": ENERGY[t["energy"]][1]} for t in trend if t["energy"]],
        "trend": trend,
        "money": _money(weeks[0], weeks, errors),
    }


def _local(stamp: str) -> datetime:
    from app.weeks import to_local_naive

    return to_local_naive(datetime.fromisoformat(stamp.replace("Z", "+00:00")))
