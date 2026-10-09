import json
import time
from datetime import date, datetime

import httpx

from app import dashboard, review
from app.config import settings
from app.integrations import strava
from app.models import Goal, PlanItem
from app.prefs import DEFAULTS

WEEK = date(2026, 10, 12)
BEGIN, END = datetime(2026, 10, 12), datetime(2026, 10, 19)


def ev(title, day, s, e, cat="school", end_day=None):
    h1, m1 = map(int, s.split(":"))
    h2, m2 = map(int, e.split(":"))
    return dashboard.Event(title, datetime(2026, 10, day, h1, m1), datetime(2026, 10, end_day or day, h2, m2), cat, "gcal")


def test_distribution_clips_to_the_week_and_sorts():
    events = [ev("Lecture", 12, "10:00", "13:00"), ev("Gym", 13, "18:00", "19:30", "fitness"),
              ev("Late", 18, "23:00", "01:00", "social", end_day=19), ev("Mystery", 14, "09:00", "10:00", "other")]
    rows = dashboard.distribution(events, BEGIN, END)
    assert [(r["key"], r["hours"]) for r in rows] == [("school", 3.0), ("fitness", 1.5), ("social", 1.0), ("other", 1.0)]
    assert rows[-1]["label"] == "Uncategorised"


def test_sleep_is_last_event_to_first_event_next_day():
    events = [ev("Dinner", 12, "19:00", "22:30"), ev("Gym", 13, "07:30", "08:30"),
              ev("Party", 13, "22:00", "01:30", end_day=14),  # ends after midnight
              ev("Late call", 15, "00:30", "01:00"),  # before 4am counts as the 14th's night
              ev("Class", 15, "09:00", "11:00")]
    nights = dashboard.sleep_nights(events, WEEK)
    assert nights[0]["hours"] == 9.0  # Mon 22:30 to Tue 07:30
    # Tuesday night: no daytime events on Wednesday, so the gap is too long to be sleep.
    assert nights[1]["hours"] is None and nights[1]["bed"] == datetime(2026, 10, 14, 1, 30)
    # Wednesday night: the 00:30 call belongs to Wednesday, then class at 09:00.
    assert nights[2]["hours"] == 8.0
    assert nights[3]["bed"] is not None and nights[3]["hours"] is None  # nothing on Friday
    # Wednesday the 14th: the 00:30 call on the 15th is that night's last event.
    assert dashboard._day_of(datetime(2026, 10, 15, 0, 30)) == date(2026, 10, 14)


def test_sleep_target_wraps_midnight():
    assert dashboard.sleep_target({"day_start": "07:00", "day_end": "23:00"}) == 8.0
    assert dashboard.sleep_target({"day_start": "06:30", "day_end": "00:30"}) == 6.0


def test_muscles_match_word_starts_only():
    kw = DEFAULTS["muscle_keywords"]
    assert dashboard.muscles_for("Gym: Push day", kw) == {"chest", "shoulders", "triceps"}
    assert dashboard.muscles_for("Legs + core", kw) == {"quads", "hamstrings", "glutes", "calves", "abs"}
    assert dashboard.muscles_for("Warm up and backlog grooming", kw) == set()


def test_training_prefers_strava_and_counts_muscles():
    events = [ev("Gym: pull day", 13, "18:00", "19:15", "fitness"), ev("Gym: legs", 15, "18:00", "19:00", "fitness")]
    acts = [{"name": "Evening weights", "type": "WeightTraining", "start": datetime(2026, 10, 13, 18, 10),
             "minutes": 70, "km": 0, "source": "strava"},
            {"name": "Morning run", "type": "Run", "start": datetime(2026, 10, 17, 7), "minutes": 30, "km": 5.2,
             "source": "strava"}]
    t = dashboard.training(events, acts, DEFAULTS["muscle_keywords"], BEGIN, END)
    assert [s["name"] for s in t["sessions"]] == ["Evening weights", "Gym: legs", "Morning run"]
    assert t["muscles"]["quads"] == 2 and t["muscles"]["back"] == 0
    assert t["minutes"] == 160


def test_gather_timeline_uses_colours_and_plan_categories(db, fake_todo):
    from tests.conftest import FakeCalendar

    db.add(Goal(title="Fit", area="fitness", todoist_label="fit"))
    db.add(PlanItem(week_start=WEEK, title="Revise", category="revision", start=datetime(2026, 10, 12, 9), duration_min=90))
    db.commit()
    cal = FakeCalendar({"primary": [
        {"summary": "Dinner", "colorId": "5", "start": {"dateTime": "2026-10-12T19:00:00+08:00"}, "end": {"dateTime": "2026-10-12T21:00:00+08:00"}},
        {"summary": "Plain", "start": {"dateTime": "2026-10-13T10:00:00+08:00"}, "end": {"dateTime": "2026-10-13T11:00:00+08:00"}},
    ]})
    fake_todo._open = [{"id": "r", "content": "Run", "labels": ["fit"], "due": {"date": "2026-10-14T07:00:00"},
                        "duration": {"amount": 30, "unit": "minute"}}]
    tl = dashboard.gather_timeline(db, BEGIN, END, DEFAULTS, calendar=cal, todo=fake_todo)
    assert [(e.title, e.category) for e in tl.events] == [
        ("Revise", "revision"), ("Dinner", "social"), ("Plain", "other"), ("Run", "fitness")]


def test_review_groups_week_tasks_by_day(db, fake_todo):
    db.add(Goal(title="School", area="school", todoist_label="school"))
    db.commit()
    fake_todo._open = [
        {"id": "a", "content": "HW", "labels": ["school"], "priority": 4, "due": {"date": "2026-10-13T09:00:00"}},
        {"id": "b", "content": "Undated", "due": None},
        {"id": "c", "content": "Next week", "due": {"date": "2026-10-20"}},
    ]
    fake_todo._completed = [{"id": "d", "task_id": "d", "content": "Read", "labels": ["school"],
                             "completed_at": "2026-10-12T02:00:00Z"}]
    r = review.week_review(db, WEEK, todo=fake_todo)
    assert r["open_total"] == 1
    assert [t["content"] for t in r["days"][0]["tasks"]] == ["Read"]
    tue = r["days"][1]["tasks"][0]
    assert (tue["content"], tue["time"], tue["done"], tue["priority"]) == ("HW", "09:00", False, 1)
    assert (r["goals"][0]["done"], r["goals"][0]["open"]) == (1, 1)


def test_strava_refreshes_expired_token_and_reads_activities(tmp_path, monkeypatch):
    monkeypatch.setattr(type(settings), "strava_token_path", property(lambda self: tmp_path / "s.json"))
    (tmp_path / "s.json").write_text(json.dumps({"access_token": "old", "refresh_token": "r1", "expires_at": 0}))
    seen = []

    def handler(req: httpx.Request):
        seen.append(req.url.path)
        if req.url.path == "/oauth/token":
            assert b"grant_type=refresh_token" in req.content
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "r2", "expires_at": time.time() + 3600})
        assert req.headers["Authorization"] == "Bearer new"
        return httpx.Response(200, json=[{"name": "Run", "sport_type": "Run", "start_date_local": "2026-10-13T07:00:00Z",
                                          "moving_time": 1800, "distance": 5200}])

    acts = strava.activities(BEGIN, END, transport=httpx.MockTransport(handler))
    assert acts == [{"name": "Run", "type": "Run", "start": datetime(2026, 10, 13, 7), "minutes": 30, "km": 5.2,
                     "source": "strava"}]
    assert json.loads((tmp_path / "s.json").read_text())["refresh_token"] == "r2"
