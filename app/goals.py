"""Goal state: versioning, context export for an LLM chat, and validated import of its changes."""

import json
from datetime import date, datetime

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.models import AREAS, Goal, GoalVersion, Milestone, Retro

GOAL_FIELDS = ("area", "title", "metric", "target", "deadline", "notes",
               "todoist_label", "todoist_project_id", "weekly_hours", "active")


def goal_dict(g: Goal) -> dict:
    d = {f: getattr(g, f) for f in GOAL_FIELDS}
    d["id"] = g.id
    d["deadline"] = g.deadline.isoformat() if g.deadline else None
    return d


def milestone_dict(m: Milestone) -> dict:
    return {"id": m.id, "goal_id": m.goal_id, "kind": m.kind, "title": m.title,
            "at": m.at.isoformat(timespec="minutes"), "notes": m.notes}


def snapshot(db: Session, week: date, source: str, note: str = "") -> GoalVersion:
    version = GoalVersion(
        week_start=week, source=source, note=note,
        snapshot={
            "goals": [goal_dict(g) for g in db.query(Goal).order_by(Goal.id).all()],
            "milestones": [milestone_dict(m) for m in db.query(Milestone).order_by(Milestone.at).all()],
        },
    )
    db.add(version)
    db.commit()
    return version


# Import


class GoalIn(BaseModel):
    id: int | None = None
    area: str = "life"
    title: str
    metric: str = ""
    target: str = ""
    deadline: date | None = None
    notes: str = ""
    todoist_label: str = ""
    todoist_project_id: str = ""
    weekly_hours: float = 0
    active: bool = True

    @field_validator("area")
    @classmethod
    def _area(cls, v: str) -> str:
        if v not in AREAS:
            raise ValueError(f"area must be one of {', '.join(AREAS)}")
        return v


class MilestoneIn(BaseModel):
    id: int | None = None
    goal_id: int | None = None
    kind: str = "deadline"
    title: str
    at: datetime
    notes: str = ""


class GoalUpdate(BaseModel):
    note: str = ""
    upsert_goals: list[GoalIn] = Field(default_factory=list)
    archive_goal_ids: list[int] = Field(default_factory=list)
    upsert_milestones: list[MilestoneIn] = Field(default_factory=list)
    delete_milestone_ids: list[int] = Field(default_factory=list)


class ImportError_(ValueError):
    pass


def parse_update(text: str) -> GoalUpdate:
    """Accept raw JSON or a chat reply with a ```json block in it."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ImportError_("No JSON object found in the pasted text.")
    try:
        return GoalUpdate.model_validate(json.loads(text[start:end + 1]))
    except json.JSONDecodeError as exc:
        raise ImportError_(f"Invalid JSON: {exc}") from exc
    except ValidationError as exc:
        raise ImportError_(f"JSON does not match the expected shape: {exc}") from exc


def preview(db: Session, update: GoalUpdate) -> list[dict]:
    """Human readable list of changes. Raises ImportError_ on references to unknown ids."""
    changes: list[dict] = []
    for g in update.upsert_goals:
        if g.id is None:
            changes.append({"action": "Add goal", "title": g.title, "fields": {}})
            continue
        current = db.get(Goal, g.id)
        if current is None:
            raise ImportError_(f"Goal id {g.id} does not exist. Omit the id to add a new goal.")
        new = g.model_dump()
        old = goal_dict(current)
        new["deadline"] = g.deadline.isoformat() if g.deadline else None
        diff = {f: [old[f], new[f]] for f in GOAL_FIELDS if old[f] != new[f]}
        if diff:
            changes.append({"action": "Update goal", "title": current.title, "fields": diff})
    for gid in update.archive_goal_ids:
        current = db.get(Goal, gid)
        if current is None:
            raise ImportError_(f"Goal id {gid} does not exist.")
        changes.append({"action": "Archive goal", "title": current.title, "fields": {}})
    for m in update.upsert_milestones:
        if m.id is not None and db.get(Milestone, m.id) is None:
            raise ImportError_(f"Milestone id {m.id} does not exist.")
        if m.goal_id is not None and db.get(Goal, m.goal_id) is None:
            raise ImportError_(f"Milestone '{m.title}' refers to unknown goal id {m.goal_id}.")
        changes.append({"action": "Update milestone" if m.id else "Add milestone",
                        "title": f"{m.title} ({m.at:%a %d %b %H:%M})", "fields": {}})
    for mid in update.delete_milestone_ids:
        current = db.get(Milestone, mid)
        if current is None:
            raise ImportError_(f"Milestone id {mid} does not exist.")
        changes.append({"action": "Delete milestone", "title": current.title, "fields": {}})
    return changes


def apply(db: Session, update: GoalUpdate, week: date) -> GoalVersion:
    preview(db, update)  # validates references
    for g in update.upsert_goals:
        row = db.get(Goal, g.id) if g.id else None
        if row is None:
            row = Goal()
            db.add(row)
        for f in GOAL_FIELDS:
            setattr(row, f, getattr(g, f))
    for gid in update.archive_goal_ids:
        db.get(Goal, gid).active = False
    for m in update.upsert_milestones:
        row = db.get(Milestone, m.id) if m.id else None
        if row is None:
            row = Milestone()
            db.add(row)
        row.goal_id, row.kind, row.title, row.at, row.notes = m.goal_id, m.kind, m.title, m.at.replace(tzinfo=None), m.notes
    for mid in update.delete_milestone_ids:
        db.delete(db.get(Milestone, mid))
    db.commit()
    return snapshot(db, week, "import", update.note)


# Export

EXAMPLE = {
    "note": "Why these changes were made",
    "upsert_goals": [
        {"id": 1, "area": "school", "title": "ST2131 A grade", "metric": "Chapters revised",
         "target": "All 10 by week 12", "deadline": "2026-11-28", "notes": "Weak on hypothesis testing",
         "todoist_label": "st2131", "todoist_project_id": "", "weekly_hours": 6, "active": True},
        {"area": "fitness", "title": "New goal without an id is added", "weekly_hours": 4},
    ],
    "archive_goal_ids": [],
    "upsert_milestones": [{"goal_id": 1, "kind": "exam", "title": "ST2131 Midterm", "at": "2026-10-20T09:00"}],
    "delete_milestone_ids": [],
}


def export_context(db: Session, week: date, review: dict | None = None) -> str:
    goals = db.query(Goal).filter(Goal.active.is_(True)).order_by(Goal.area, Goal.id).all()
    milestones = db.query(Milestone).filter(Milestone.at >= datetime.combine(week, datetime.min.time())) \
        .order_by(Milestone.at).limit(20).all()
    retros = db.query(Retro).order_by(Retro.week_start.desc()).limit(3).all()

    lines = [
        f"# Weekly planner context (week of {week:%d %b %Y})",
        "",
        "You are helping me review my goals for the coming sprint (one week).",
        "Discuss with me first. When we agree, reply with ONE JSON object in a ```json block,",
        "using the schema at the end. Only include goals and milestones that change.",
        "",
        "## Active goals",
    ]
    if not goals:
        lines.append("(none yet)")
    for g in goals:
        lines.append(f"- [id {g.id}] ({g.area}) **{g.title}**: metric '{g.metric}', target '{g.target}', "
                     f"deadline {g.deadline or 'none'}, {g.weekly_hours:g} h/week planned"
                     + (f", Todoist label @{g.todoist_label}" if g.todoist_label else ""))
        if g.notes:
            lines.append(f"  - Notes: {g.notes}")

    lines += ["", "## Upcoming milestones"]
    lines += [f"- [id {m.id}] {m.at:%a %d %b %H:%M} {m.kind}: {m.title}" + (f" (goal {m.goal_id})" if m.goal_id else "")
              for m in milestones] or ["(none)"]

    if review:
        lines += ["", "## This week (from Todoist)",
                  f"- Planned tasks completed: {review['planned_done']} of {review['planned_total']}"]
        for row in review["goals"]:
            lines.append(f"- {row['title']}: {row['done']} tasks done" +
                         (f" ({', '.join(row['examples'][:5])})" if row["examples"] else ""))
        if review["unmatched"]:
            lines.append(f"- Other completed tasks: {', '.join(t['content'] for t in review['unmatched'][:10])}")

    lines += ["", "## Recent retrospectives"]
    for r in retros:
        lines.append(f"### Week of {r.week_start:%d %b}" + (f" (energy {r.energy}/5)" if r.energy else ""))
        lines += [f"- Went well: {r.went_well or '-'}", f"- Went badly: {r.went_badly or '-'}",
                  f"- Change: {r.change or '-'}"]
    if not retros:
        lines.append("(none yet)")

    lines += ["", "## Reply schema", "", "```json", json.dumps(EXAMPLE, indent=2), "```",
              "", f"Allowed areas: {', '.join(AREAS)}. Dates are ISO 8601 in my local time."]
    return "\n".join(lines)
