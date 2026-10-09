"""JSON API used by the planning screen."""

import time
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app import planning
from app.categories import CATEGORIES, category
from app.config import settings
from app.db import get_session
from app.models import Goal, PlanItem
from app.prefs import get_prefs
from app.weeks import parse_iso, parse_week, planning_week, to_aware

router = APIRouter(prefix="/api")

# External calendars are slow to read; keep each week's busy time for a short while.
_BUSY_TTL = 120
_busy_cache: dict[date, tuple[float, planning.Busy]] = {}


def clear_busy_cache() -> None:
    _busy_cache.clear()


def busy_for(db: Session, week: date, refresh: bool = False) -> planning.Busy:
    hit = _busy_cache.get(week)
    if hit and not refresh and time.time() - hit[0] < _BUSY_TTL:
        return hit[1]
    busy = planning.gather_busy(db, week, get_prefs(db))
    _busy_cache[week] = (time.time(), busy)
    return busy


def _week(value: str | None) -> date:
    try:
        return parse_week(value, planning_week())
    except ValueError:
        raise HTTPException(400, "Invalid week date")


def item_json(item: PlanItem, issues: list[dict] | None = None, goal_titles: dict[int, str] | None = None) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "kind": item.kind,
        "category": item.category,
        "color": category(item.category)["color"],
        "duration_min": item.duration_min,
        "start": to_aware(item.start).isoformat() if item.start else None,
        "end": to_aware(item.start + timedelta(minutes=item.duration_min)).isoformat() if item.start else None,
        "location": item.location,
        "priority": item.priority,
        "goal_id": item.goal_id,
        "goal_title": (goal_titles or {}).get(item.goal_id, ""),
        "notes": item.notes,
        "origin": item.origin,
        "committed": item.committed_at is not None and not item.dirty,
        "synced": bool(item.todoist_task_id or item.gcal_event_id),
        "issues": issues or [],
    }


@router.get("/plan")
def get_plan(week: str | None = None, refresh: bool = False, db: Session = Depends(get_session)):
    wk = _week(week)
    prefs = get_prefs(db)
    busy = busy_for(db, wk, refresh)
    items = planning.week_items(db, wk)
    issues = planning.issues_by_item(items, busy, prefs)
    titles = {g.id: g.title for g in db.query(Goal).all()}
    return {
        "week": wk.isoformat(),
        "items": [item_json(i, issues.get(i.id), titles) for i in items],
        "busy": [{**b, "start": to_aware(b["start"]).isoformat() if not b["allDay"] else b["start"].date().isoformat(),
                  "end": to_aware(b["end"]).isoformat() if not b["allDay"] else b["end"].date().isoformat()}
                 for b in busy.events],
        "errors": busy.errors,
        "summary": _summary(items),
    }


def _summary(items: list[PlanItem]) -> dict:
    hours: dict[str, float] = {}
    for i in items:
        if i.start is not None:
            hours[i.category] = hours.get(i.category, 0) + i.duration_min / 60
    return {"scheduled_hours": {k: round(v, 2) for k, v in hours.items()},
            "backlog": sum(1 for i in items if i.start is None)}


class ItemIn(BaseModel):
    title: str | None = None
    kind: str | None = None
    category: str | None = None
    duration_min: int | None = None
    start: str | None = None
    unschedule: bool = False
    location: str | None = None
    priority: int | None = None
    goal_id: int | None = None
    clear_goal: bool = False
    notes: str | None = None


def _apply(item: PlanItem, body: ItemIn) -> None:
    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(400, "Title is required")
        item.title = body.title.strip()
    if body.kind is not None:
        if body.kind not in ("task", "event"):
            raise HTTPException(400, "kind must be task or event")
        item.kind = body.kind
    if body.category is not None:
        if body.category not in CATEGORIES:
            raise HTTPException(400, "Unknown category")
        item.category = body.category
    if body.duration_min is not None:
        if not 5 <= body.duration_min <= 24 * 60:
            raise HTTPException(400, "Duration must be between 5 minutes and 24 hours")
        item.duration_min = body.duration_min
    if body.unschedule:
        item.start = None
    elif body.start is not None:
        item.start = parse_iso(body.start)
    if body.location is not None:
        item.location = body.location.strip().lower()
    if body.priority is not None:
        item.priority = min(4, max(1, body.priority))
    if body.clear_goal:
        item.goal_id = None
    elif body.goal_id is not None:
        item.goal_id = body.goal_id
    if body.notes is not None:
        item.notes = body.notes
    item.dirty = True


@router.post("/plan/items")
def create_item(body: ItemIn, week: str | None = None, db: Session = Depends(get_session)):
    item = PlanItem(week_start=_week(week), title="")
    _apply(item, ItemIn(title=body.title or "", **body.model_dump(exclude={"title"})))
    db.add(item)
    db.commit()
    return item_json(item)


@router.patch("/plan/items/{item_id}")
def update_item(item_id: int, body: ItemIn, db: Session = Depends(get_session)):
    item = db.get(PlanItem, item_id)
    if item is None:
        raise HTTPException(404)
    _apply(item, body)
    db.commit()
    return item_json(item)


@router.post("/plan/items/{item_id}/duplicate")
def duplicate_item(item_id: int, db: Session = Depends(get_session)):
    src = db.get(PlanItem, item_id)
    if src is None:
        raise HTTPException(404)
    copy = PlanItem(week_start=src.week_start, title=src.title, kind=src.kind, category=src.category,
                    duration_min=src.duration_min, location=src.location, priority=src.priority,
                    goal_id=src.goal_id, notes=src.notes, todoist_project_id=src.todoist_project_id)
    db.add(copy)
    db.commit()
    return item_json(copy)


@router.delete("/plan/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_session)):
    item = db.get(PlanItem, item_id)
    if item is None:
        raise HTTPException(404)
    return {"errors": planning.delete_item(db, item)}


@router.post("/plan/autofill")
def autofill(week: str | None = None, db: Session = Depends(get_session)):
    wk = _week(week)
    now = datetime.now(settings.tz).replace(tzinfo=None)
    placed = planning.run_autofill(db, wk, busy_for(db, wk), get_prefs(db), now)
    return {"placed": placed}


@router.post("/plan/commit")
def commit(week: str | None = None, db: Session = Depends(get_session)):
    wk = _week(week)
    result = planning.commit_week(db, wk)
    clear_busy_cache()
    return {"events": result.events, "tasks": result.tasks, "removed": result.removed, "errors": result.errors}


@router.post("/plan/import-todoist")
def import_todoist(week: str | None = None, db: Session = Depends(get_session)):
    try:
        return {"added": planning.import_todoist_tasks(db, _week(week))}
    except Exception as exc:
        raise HTTPException(502, f"Todoist: {exc}")


@router.post("/plan/from-goals")
def backlog_from_goals(week: str | None = None, db: Session = Depends(get_session)):
    """Seed the backlog with blocks covering each goal's weekly hours (in 90 min blocks)."""
    wk = _week(week)
    existing = planning.week_items(db, wk)
    added = 0
    for goal in db.query(Goal).filter(Goal.active.is_(True), Goal.weekly_hours > 0).all():
        planned = sum(i.duration_min for i in existing if i.goal_id == goal.id)
        remaining = int(goal.weekly_hours * 60) - planned
        cat = "revision" if goal.area == "school" else goal.area
        while remaining >= 30:
            length = min(90, remaining)
            db.add(PlanItem(week_start=wk, title=goal.title, kind="event", category=cat,
                            duration_min=length, goal_id=goal.id, priority=3))
            remaining -= length
            added += 1
    db.commit()
    return {"added": added}
