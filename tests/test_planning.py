from datetime import date, datetime

from app import planning
from app.models import Goal, PlanItem
from app.prefs import get_prefs, set_pref

WEEK = date(2026, 10, 12)


def add(db, **kw):
    item = PlanItem(week_start=WEEK, title=kw.pop("title", "Block"), **kw)
    db.add(item)
    db.commit()
    return item


def test_commit_pushes_events_and_timed_tasks(db, fake_cal, fake_todo):
    goal = Goal(title="ST2131", area="school", todoist_label="st2131", todoist_project_id="p1")
    db.add(goal)
    db.commit()
    ev = add(db, title="Lecture", kind="event", category="school", start=datetime(2026, 10, 13, 12), duration_min=180)
    task = add(db, title="HW3", kind="task", start=datetime(2026, 10, 14, 9), duration_min=90, priority=1, goal_id=goal.id)
    add(db, title="Unscheduled", kind="task")

    res = planning.commit_week(db, WEEK, calendar=fake_cal, todo=fake_todo)

    assert (res.events, res.tasks, res.errors) == (1, 1, [])
    assert ev.gcal_event_id == "ev1" and task.todoist_task_id == "t1"
    assert get_prefs(db)["planner_calendar_id"] == "planner-cal"
    body = fake_cal.upserts[0][2]
    assert body["start"]["dateTime"] == "2026-10-13T12:00:00+08:00"
    assert body["extendedProperties"]["private"]["planner_item_id"] == str(ev.id)
    payload = fake_todo.created[0]
    assert payload["due_datetime"] == "2026-10-14T01:00:00Z"
    assert payload["duration"] == 90 and payload["duration_unit"] == "minute"
    assert payload["priority"] == 4  # UI P1 is API priority 4
    assert payload["labels"] == ["st2131"] and payload["project_id"] == "p1"
    assert not ev.dirty and not task.dirty


def test_recommit_updates_instead_of_duplicating(db, fake_cal, fake_todo):
    task = add(db, title="HW3", kind="task", start=datetime(2026, 10, 14, 9), duration_min=60)
    planning.commit_week(db, WEEK, calendar=fake_cal, todo=fake_todo)
    task.start = datetime(2026, 10, 15, 9)
    db.commit()
    planning.commit_week(db, WEEK, calendar=fake_cal, todo=fake_todo)
    assert len(fake_todo.created) == 1
    assert fake_todo.updated[-1][0] == "t1"
    assert fake_todo.updated[-1][1]["due_datetime"] == "2026-10-15T01:00:00Z"


def test_unscheduling_a_committed_item_removes_it(db, fake_cal, fake_todo):
    ev = add(db, title="Gym", kind="event", start=datetime(2026, 10, 13, 18), duration_min=60)
    task = add(db, title="HW", kind="task", start=datetime(2026, 10, 13, 9), duration_min=60)
    planning.commit_week(db, WEEK, calendar=fake_cal, todo=fake_todo)
    ev.start = task.start = None
    db.commit()
    res = planning.commit_week(db, WEEK, calendar=fake_cal, todo=fake_todo)
    assert res.removed == 2
    assert fake_cal.deleted == ["ev1"] and fake_todo.deleted == ["t1"]
    assert ev.gcal_event_id == "" and task.todoist_task_id == ""


def test_imported_todoist_task_is_never_deleted(db, fake_cal, fake_todo):
    task = add(db, title="Existing", kind="task", origin="todoist", todoist_task_id="orig")
    # Never committed: an unscheduled import is left alone entirely.
    planning.commit_week(db, WEEK, calendar=fake_cal, todo=fake_todo)
    assert fake_todo.updated == [] and fake_todo.deleted == []
    task.start = datetime(2026, 10, 13, 9)
    db.commit()
    planning.commit_week(db, WEEK, calendar=fake_cal, todo=fake_todo)
    assert fake_todo.updated[-1][0] == "orig"
    task.start = None
    db.commit()
    planning.commit_week(db, WEEK, calendar=fake_cal, todo=fake_todo)
    assert fake_todo.deleted == []
    assert fake_todo.updated[-1] == ("orig", {"due_string": "no date"})
    assert task.todoist_task_id == "orig"


def test_switching_kind_moves_item_between_services(db, fake_cal, fake_todo):
    item = add(db, title="Read", kind="task", start=datetime(2026, 10, 13, 9), duration_min=60)
    planning.commit_week(db, WEEK, calendar=fake_cal, todo=fake_todo)
    item.kind = "event"
    db.commit()
    planning.commit_week(db, WEEK, calendar=fake_cal, todo=fake_todo)
    assert fake_todo.deleted == ["t1"] and item.gcal_event_id == "ev1"


def test_gather_busy_skips_own_and_free_events_and_linked_tasks(db, fake_todo):
    add(db, title="Mine", kind="task", todoist_task_id="linked")
    set_pref(db, "busy_calendar_ids", ["primary", "todoist-cal"])
    set_pref(db, "ignored_calendar_ids", ["todoist-cal"])
    cal_events = {
        "primary": [
            {"summary": "Class", "start": {"dateTime": "2026-10-13T10:00:00+08:00"}, "end": {"dateTime": "2026-10-13T12:00:00+08:00"}},
            {"summary": "Ours", "start": {"dateTime": "2026-10-13T13:00:00+08:00"}, "end": {"dateTime": "2026-10-13T14:00:00+08:00"},
             "extendedProperties": {"private": {"planner_item_id": "9"}}},
            {"summary": "Free", "transparency": "transparent", "start": {"dateTime": "2026-10-13T15:00:00+08:00"}, "end": {"dateTime": "2026-10-13T16:00:00+08:00"}},
            {"summary": "Holiday", "start": {"date": "2026-10-14"}, "end": {"date": "2026-10-15"}},
        ],
        "todoist-cal": [{"summary": "dup", "start": {"dateTime": "2026-10-13T08:00:00+08:00"}, "end": {"dateTime": "2026-10-13T09:00:00+08:00"}}],
    }
    from tests.conftest import FakeCalendar

    fake_todo._open = [
        {"id": "linked", "content": "Mine", "due": {"date": "2026-10-13T09:00:00"}},
        {"id": "x", "content": "Dentist", "due": {"date": "2026-10-15T02:00:00Z"}, "duration": {"amount": 45, "unit": "minute"}},
        {"id": "y", "content": "Undated", "due": None},
        {"id": "z", "content": "Next month", "due": {"date": "2026-11-15T09:00:00"}},
    ]
    busy = planning.gather_busy(db, WEEK, get_prefs(db), calendar=FakeCalendar(cal_events), todo=fake_todo)
    titles = [e["title"] for e in busy.events]
    assert titles == ["Class", "Holiday", "Dentist"]
    assert [s.title for s in busy.slots] == ["Class", "Dentist"]
    dentist = busy.slots[1]
    assert dentist.start == datetime(2026, 10, 15, 10) and (dentist.end - dentist.start).seconds == 45 * 60
    assert busy.errors == []


def test_import_todoist_tasks_maps_goal_and_skips_timed(db, fake_todo):
    db.add(Goal(title="Fitness", area="fitness", todoist_label="fit"))
    db.commit()
    fake_todo._open = [
        {"id": "a", "content": "Meal prep", "labels": ["fit"], "priority": 3, "due": None},
        {"id": "b", "content": "Timed", "due": {"date": "2026-10-13T09:00:00"}},
        {"id": "c", "content": "Due this week", "due": {"date": "2026-10-16"}, "duration": {"amount": 30, "unit": "minute"}},
        {"id": "d", "content": "Due later", "due": {"date": "2026-12-01"}},
        {"id": "e", "content": "Subtask", "parent_id": "a"},
    ]
    assert planning.import_todoist_tasks(db, WEEK, todo=fake_todo) == 2
    items = {i.title: i for i in planning.week_items(db, WEEK)}
    assert items["Meal prep"].category == "fitness" and items["Meal prep"].priority == 2
    assert items["Due this week"].duration_min == 30 and items["Due this week"].origin == "todoist"
    # Running it again does not duplicate.
    assert planning.import_todoist_tasks(db, WEEK, todo=fake_todo) == 0
