"""Sprint review: what got done this week according to Todoist."""

from datetime import date

from sqlalchemy.orm import Session

from app.integrations import todoist
from app.models import Goal, PlanItem
from app.weeks import week_bounds


def week_review(db: Session, week: date, todo=None) -> dict:
    begin, end = week_bounds(week)
    result = {"goals": [], "unmatched": [], "completed": [], "planned_total": 0, "planned_done": 0, "error": ""}
    completed: list[dict] = []
    td = todo if todo is not None else todoist.Todoist()
    if not td.configured:
        result["error"] = "Set TODOIST_API_TOKEN to see completed tasks."
    else:
        try:
            completed = td.completed_between(begin, end)
        except Exception as exc:
            result["error"] = f"Could not read Todoist: {exc}"

    result["completed"] = completed
    goals = db.query(Goal).filter(Goal.active.is_(True)).order_by(Goal.area, Goal.id).all()
    matched: set[int] = set()
    for g in goals:
        hits = [t for t in completed if _matches(g, t)]
        matched.update(id(t) for t in hits)
        result["goals"].append({"id": g.id, "title": g.title, "area": g.area, "target": g.target,
                                "done": len(hits), "examples": [t.get("content", "") for t in hits]})
    result["unmatched"] = [t for t in completed if id(t) not in matched]

    done_ids = {str(t.get("task_id") or t.get("id")) for t in completed}
    planned = db.query(PlanItem).filter(PlanItem.week_start == week, PlanItem.kind == "task",
                                        PlanItem.todoist_task_id != "").all()
    result["planned_total"] = len(planned)
    result["planned_done"] = sum(1 for p in planned if p.todoist_task_id in done_ids)
    return result


def _matches(goal: Goal, task: dict) -> bool:
    if goal.todoist_label and goal.todoist_label in (task.get("labels") or []):
        return True
    return bool(goal.todoist_project_id) and goal.todoist_project_id == str(task.get("project_id", ""))
