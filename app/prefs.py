"""User preferences stored in the settings table, with defaults."""

from copy import deepcopy

from sqlalchemy.orm import Session

from app.models import Setting

DEFAULTS: dict = {
    # Wake-up time and bedtime. Blocks go between them; the rest of the day is
    # shaded on the planning calendar as sleep.
    "day_start": "07:00",
    "day_end": "23:00",
    # Gap kept between any two blocks, in minutes.
    "buffer_min": 10,
    # Preferred hours per category for auto-fill, e.g. revision in the morning.
    "preferred_windows": {
        "revision": ["09:00", "13:00"],
        "school": ["09:00", "18:00"],
        "work": ["10:00", "18:00"],
        "fitness": ["17:00", "21:00"],
        "social": ["18:00", "23:00"],
    },
    # Travel time between named places, in minutes. Keys are "A|B" (either order).
    "travel_minutes": {"home|smu": 50, "smu|gym": 15, "home|gym": 20},
    # Google calendar ids whose events count as busy. Empty means the primary calendar.
    "busy_calendar_ids": [],
    # Calendars to ignore entirely, such as the Todoist sync calendar
    # (Todoist tasks are read straight from Todoist instead).
    "ignored_calendar_ids": [],
    # Id of the calendar this app writes events to. Created on first commit if empty.
    "planner_calendar_id": "",
    # Words in a workout's title (calendar block or Strava activity) and the muscles they train.
    "muscle_keywords": {
        "push": ["chest", "shoulders", "triceps"],
        "pull": ["back", "biceps", "forearms"],
        "leg": ["quads", "hamstrings", "glutes", "calves"],
        "upper": ["chest", "back", "shoulders", "biceps", "triceps"],
        "lower": ["quads", "hamstrings", "glutes", "calves"],
        "full body": ["chest", "back", "shoulders", "quads", "glutes", "abs"],
        "chest": ["chest"], "back": ["back"], "shoulder": ["shoulders"], "arm": ["biceps", "triceps"],
        "core": ["abs"], "abs": ["abs"], "glute": ["glutes"],
        "run": ["quads", "hamstrings", "calves"], "running": ["quads", "hamstrings", "calves"],
        "ride": ["quads", "glutes", "calves"], "cycling": ["quads", "glutes", "calves"],
        "swim": ["back", "shoulders", "chest"], "swimming": ["back", "shoulders", "chest"],
        "climb": ["back", "biceps", "forearms"], "climbing": ["back", "biceps", "forearms"],
        "handball": ["shoulders", "quads", "calves"],
    },
    # Default Todoist project for new tasks when an item has none.
    "todoist_default_project_id": "",
}


def get_prefs(db: Session) -> dict:
    prefs = deepcopy(DEFAULTS)
    for row in db.query(Setting).all():
        prefs[row.key] = row.value
    return prefs


def set_pref(db: Session, key: str, value) -> None:
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def travel_minutes(prefs: dict, a: str, b: str) -> int:
    a, b = a.strip().lower(), b.strip().lower()
    if not a or not b or a == b:
        return 0
    table = prefs.get("travel_minutes", {})
    return int(table.get(f"{a}|{b}", table.get(f"{b}|{a}", 0)))
