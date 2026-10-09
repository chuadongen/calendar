"""Weekly planning: gather busy time, check clashes, auto-fill, and commit."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app import scheduler
from app.categories import category
from app.config import settings
from app.integrations import gcal, todoist
from app.models import Goal, PlanItem
from app.prefs import get_prefs, set_pref
from app.weeks import week_bounds


@dataclass
class Busy:
    events: list[dict] = field(default_factory=list)  # for display
    slots: list[scheduler.Slot] = field(default_factory=list)  # for clash checks
    errors: list[str] = field(default_factory=list)


def week_items(db: Session, week: date) -> list[PlanItem]:
    return db.query(PlanItem).filter(PlanItem.week_start == week).order_by(PlanItem.priority, PlanItem.id).all()


def item_slot(item: PlanItem) -> scheduler.Slot:
    return scheduler.Slot(item.start, item.start + timedelta(minutes=item.duration_min),
                          item.location, item.title, item.id, item.category)


def gather_busy(db: Session, week: date, prefs: dict, calendar=None, todo=None) -> Busy:
    """Existing commitments from Google Calendar and timed Todoist tasks not owned by this plan."""
    busy = Busy()
    begin, end = week_bounds(week)
    linked_tasks = {i.todoist_task_id for i in db.query(PlanItem).filter(PlanItem.todoist_task_id != "").all()}

    try:
        cal = calendar if calendar is not None else (gcal.Calendar() if gcal.connected() else None)
        if cal is None:
            busy.errors.append("Google Calendar is not connected, so existing events are not shown.")
        else:
            ignored = set(prefs.get("ignored_calendar_ids") or [])
            ids = [c for c in (prefs.get("busy_calendar_ids") or ["primary"]) if c not in ignored]
            for cal_id in ids:
                for ev in cal.events(cal_id, begin, end):
                    if gcal.is_planner_event(ev) or ev.get("transparency") == "transparent":
                        continue
                    if ev.get("status") == "cancelled":
                        continue
                    start, finish, all_day = gcal.event_span(ev)
                    title = ev.get("summary", "(busy)")
                    busy.events.append({"title": title, "start": start, "end": finish,
                                        "allDay": all_day, "source": "gcal"})
                    if not all_day:
                        busy.slots.append(scheduler.Slot(start, finish, ev.get("location", ""), title))
    except Exception as exc:
        busy.errors.append(f"Could not read Google Calendar: {exc}")

    try:
        td = todo if todo is not None else todoist.Todoist()
        if not td.configured:
            busy.errors.append("Todoist token is not set, so existing timed tasks are not shown.")
        else:
            for task in td.open_tasks():
                if task["id"] in linked_tasks:
                    continue
                start = todoist.task_start(task)
                if start is None or not (begin <= start < end):
                    continue
                finish = start + timedelta(minutes=todoist.task_duration(task))
                busy.events.append({"title": task["content"], "start": start, "end": finish,
                                    "allDay": False, "source": "todoist"})
                busy.slots.append(scheduler.Slot(start, finish, "", task["content"]))
    except Exception as exc:
        busy.errors.append(f"Could not read Todoist: {exc}")
    return busy


def issues_by_item(items: list[PlanItem], busy: Busy, prefs: dict) -> dict[int, list[dict]]:
    slots = [item_slot(i) for i in items if i.start is not None]
    out: dict[int, list[dict]] = {}
    for issue in scheduler.find_issues(slots, busy.slots, prefs):
        out.setdefault(issue.item_id, []).append({"level": issue.level, "message": issue.message})
    return out


def run_autofill(db: Session, week: date, busy: Busy, prefs: dict, now: datetime | None = None) -> int:
    items = week_items(db, week)
    occupied = busy.slots + [item_slot(i) for i in items if i.start is not None]
    todo = [(i.id, i.duration_min, i.category, i.location, i.priority) for i in items if i.start is None]
    placed = scheduler.autofill(todo, occupied, week, prefs, not_before=now)
    for item in items:
        if item.id in placed:
            item.start = placed[item.id]
            item.dirty = True
    db.commit()
    return len(placed)


# Commit


@dataclass
class CommitResult:
    events: int = 0
    tasks: int = 0
    removed: int = 0
    errors: list[str] = field(default_factory=list)


def _labels(goal: Goal | None) -> list[str]:
    return [goal.todoist_label] if goal and goal.todoist_label else []


def _project(item: PlanItem, goal: Goal | None, prefs: dict) -> str:
    return item.todoist_project_id or (goal.todoist_project_id if goal else "") or prefs.get("todoist_default_project_id", "")


def _drop_task(td, item: PlanItem) -> None:
    """Remove an item's Todoist presence: delete app-made tasks, unschedule imported ones."""
    if not item.todoist_task_id:
        return
    if item.origin == "todoist":
        td.update_task(item.todoist_task_id, {"due_string": "no date"})
    else:
        td.delete_task(item.todoist_task_id)
        item.todoist_task_id = ""


def _drop_event(cal, calendar_id: str, item: PlanItem) -> None:
    if item.gcal_event_id and cal is not None:
        cal.delete_event(calendar_id, item.gcal_event_id)
        item.gcal_event_id = ""


def commit_week(db: Session, week: date, calendar=None, todo=None) -> CommitResult:
    """Push the week's plan: events to the Planner calendar, tasks to Todoist with a time."""
    prefs = get_prefs(db)
    result = CommitResult()
    items = week_items(db, week)
    needs_cal = any(i.kind == "event" and i.start for i in items) or any(i.gcal_event_id for i in items)
    needs_todo = any(i.kind == "task" and i.start for i in items) or any(i.todoist_task_id for i in items)

    cal, calendar_id = None, prefs.get("planner_calendar_id", "")
    if needs_cal:
        try:
            cal = calendar if calendar is not None else gcal.Calendar()
            new_id = cal.ensure_planner_calendar(calendar_id)
            if new_id != calendar_id:
                set_pref(db, "planner_calendar_id", new_id)
                calendar_id = new_id
        except Exception as exc:
            result.errors.append(f"Google Calendar: {exc}")
            cal = None
    td = todo if todo is not None else todoist.Todoist()
    if needs_todo and not td.configured:
        result.errors.append("Todoist: TODOIST_API_TOKEN is not set")

    for item in items:
        goal = db.get(Goal, item.goal_id) if item.goal_id else None
        try:
            if item.start is None:
                # Only undo what an earlier commit pushed; never touch an imported task's own date.
                if item.committed_at is not None:
                    _drop_event(cal, calendar_id, item)
                    if td.configured:
                        _drop_task(td, item)
                    item.committed_at = None
                    result.removed += 1
                item.dirty = False
                continue
            end = item.start + timedelta(minutes=item.duration_min)
            if item.kind == "event":
                if cal is None:
                    continue
                body = gcal.event_body(item.id, item.title, item.start, end,
                                       category(item.category)["gcal_color_id"], item.location, item.notes)
                item.gcal_event_id = cal.upsert_event(calendar_id, item.gcal_event_id, body)
                if td.configured:
                    _drop_task(td, item)
                result.events += 1
            else:
                if not td.configured:
                    continue
                payload = todoist.task_payload(item.title, item.start, item.duration_min, item.priority,
                                               _project(item, goal, prefs), _labels(goal), item.notes)
                if item.todoist_task_id:
                    # Moving a task between projects needs a separate move call; keep it where it is.
                    payload.pop("project_id", None)
                    td.update_task(item.todoist_task_id, payload)
                else:
                    item.todoist_task_id = td.create_task(payload)["id"]
                _drop_event(cal, calendar_id, item)
                result.tasks += 1
            item.committed_at = datetime.now(settings.tz).replace(tzinfo=None, microsecond=0)
            item.dirty = False
        except Exception as exc:
            result.errors.append(f"{item.title}: {exc}")
        finally:
            db.commit()
    return result


def delete_item(db: Session, item: PlanItem, calendar=None, todo=None) -> list[str]:
    """Delete a plan item and whatever it created outside the app."""
    errors: list[str] = []
    prefs = get_prefs(db)
    if item.gcal_event_id:
        try:
            cal = calendar if calendar is not None else gcal.Calendar()
            cal.delete_event(prefs.get("planner_calendar_id", ""), item.gcal_event_id)
        except Exception as exc:
            errors.append(f"Google Calendar: {exc}")
    if item.todoist_task_id and item.origin == "app":
        try:
            (todo if todo is not None else todoist.Todoist()).delete_task(item.todoist_task_id)
        except Exception as exc:
            errors.append(f"Todoist: {exc}")
    db.delete(item)
    db.commit()
    return errors


def import_todoist_tasks(db: Session, week: date, todo=None) -> int:
    """Pull open Todoist tasks that have no time yet (or are due this week) into the backlog."""
    td = todo if todo is not None else todoist.Todoist()
    begin, end = week_bounds(week)
    known = {i.todoist_task_id for i in db.query(PlanItem).filter(PlanItem.todoist_task_id != "").all()}
    goals = db.query(Goal).filter(Goal.active.is_(True)).all()
    added = 0
    for task in td.open_tasks():
        if task["id"] in known or task.get("parent_id"):
            continue
        due = (task.get("due") or {}).get("date", "")[:10]
        if due and not (begin.date().isoformat() <= due < end.date().isoformat()):
            continue
        if todoist.task_start(task) is not None:
            continue  # already timed: shown as busy time instead
        goal = next((g for g in goals if g.todoist_label and g.todoist_label in (task.get("labels") or [])), None)
        goal = goal or next((g for g in goals if g.todoist_project_id and g.todoist_project_id == task.get("project_id")), None)
        db.add(PlanItem(
            week_start=week, title=task["content"], kind="task",
            category=goal.area if goal else "life",
            duration_min=todoist.task_duration(task, default=60), priority=todoist.ui_priority(task),
            goal_id=goal.id if goal else None, origin="todoist",
            todoist_task_id=task["id"], todoist_project_id=task.get("project_id", ""),
        ))
        added += 1
    db.commit()
    return added
