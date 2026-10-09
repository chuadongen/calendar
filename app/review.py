"""Sprint review: this week's Todoist tasks and how each goal moved."""

from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.integrations import todoist
from app.models import Goal, PlanItem
from app.weeks import to_local_naive, week_bounds


def week_review(db: Session, week: date, todo=None) -> dict:
    begin, end = week_bounds(week)
    result = {"goals": [], "unmatched": [], "completed": [], "days": [], "planned_total": 0,
              "planned_done": 0, "open_total": 0, "error": ""}
    completed: list[dict] = []
    open_week: list[dict] = []
    td = todo if todo is not None else todoist.Todoist()
    if not td.configured:
        result["error"] = "Set TODOIST_API_TOKEN to see this week's tasks."
    else:
        try:
            completed = td.completed_between(begin, end)
            first, last = begin.date().isoformat(), (end - timedelta(days=1)).date().isoformat()
            open_week = [t for t in td.open_tasks() if first <= todoist.due_date(t) <= last]
        except Exception as exc:
            result["error"] = f"Could not read Todoist: {exc}"

    result["completed"] = completed
    result["open_total"] = len(open_week)
    result["days"] = _by_day(week, open_week, completed)

    goals = db.query(Goal).filter(Goal.active.is_(True)).order_by(Goal.area, Goal.id).all()
    planned = db.query(PlanItem).filter(PlanItem.week_start == week, PlanItem.start.isnot(None)).all()
    matched: set[int] = set()
    for g in goals:
        hits = [t for t in completed if _matches(g, t)]
        still_open = [t for t in open_week if _matches(g, t)]
        matched.update(id(t) for t in hits)
        hours = sum(p.duration_min for p in planned if p.goal_id == g.id) / 60
        result["goals"].append({
            "id": g.id, "title": g.title, "area": g.area, "target": g.target, "horizon": g.horizon,
            "done": len(hits), "open": len(still_open), "examples": [t.get("content", "") for t in hits],
            "planned_hours": round(hours, 1), "weekly_hours": g.weekly_hours,
        })
    result["unmatched"] = [t for t in completed if id(t) not in matched]

    done_ids = {_task_id(t) for t in completed}
    planned_tasks = [p for p in planned if p.kind == "task" and p.todoist_task_id]
    result["planned_total"] = len(planned_tasks)
    result["planned_done"] = sum(1 for p in planned_tasks if p.todoist_task_id in done_ids)
    return result


def _task_id(task: dict) -> str:
    return str(task.get("task_id") or task.get("id"))


def _by_day(week: date, open_tasks: list[dict], completed: list[dict]) -> list[dict]:
    """Seven days, each listing its tasks (done and open) in time order, Todoist style."""
    days = [{"date": week + timedelta(days=d), "tasks": []} for d in range(7)]
    index = {d["date"].isoformat(): d for d in days}

    def row(task: dict, done: bool, day: str) -> None:
        if day not in index:
            return
        start = todoist.task_start(task)
        index[day]["tasks"].append({
            "id": _task_id(task), "content": task.get("content", ""), "done": done,
            "time": start.strftime("%H:%M") if start else "", "labels": task.get("labels") or [],
            "priority": todoist.ui_priority(task) if task.get("priority") else 4,
        })

    for t in open_tasks:
        row(t, False, todoist.due_date(t))
    for t in completed:
        day = todoist.due_date(t)
        if not day and t.get("completed_at"):
            day = to_local_naive(datetime.fromisoformat(t["completed_at"].replace("Z", "+00:00"))).date().isoformat()
        row(t, True, day)
    for d in days:
        d["tasks"].sort(key=lambda r: (r["time"] or "99", r["done"]))
    return days


def _matches(goal: Goal, task: dict) -> bool:
    if goal.todoist_label and goal.todoist_label in (task.get("labels") or []):
        return True
    return bool(goal.todoist_project_id) and goal.todoist_project_id == str(task.get("project_id", ""))


def upcoming(week: date, todo=None) -> dict:
    """Open Todoist tasks due in the given week, grouped by day."""
    td = todo if todo is not None else todoist.Todoist()
    if not td.configured:
        return {"days": [], "count": 0, "error": "Set TODOIST_API_TOKEN to see next week's tasks."}
    try:
        tasks = td.open_tasks()
    except Exception as exc:
        return {"days": [], "count": 0, "error": f"Could not read Todoist: {exc}"}
    first, last = week.isoformat(), (week + timedelta(days=6)).isoformat()
    in_week = [t for t in tasks if first <= todoist.due_date(t) <= last]
    return {"days": _by_day(week, in_week, []), "count": len(in_week), "error": ""}
