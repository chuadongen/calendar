"""Database tables.

Naive datetimes are wall-clock times in the configured timezone (settings.tz).
Completion of concrete work lives in Todoist; this database holds goals,
milestones, retrospectives and the weekly plan.
"""

from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.db import Base

AREAS = ("school", "work", "fitness", "life")


def _now() -> datetime:
    return datetime.now(settings.tz).replace(tzinfo=None, microsecond=0)


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    area: Mapped[str] = mapped_column(String(20), default="life")
    title: Mapped[str] = mapped_column(String(200))
    metric: Mapped[str] = mapped_column(String(200), default="")
    target: Mapped[str] = mapped_column(String(200), default="")
    deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Todoist tasks carrying this label (or in this project) count towards the goal.
    todoist_label: Mapped[str] = mapped_column(String(100), default="")
    todoist_project_id: Mapped[str] = mapped_column(String(50), default="")
    weekly_hours: Mapped[float] = mapped_column(default=0.0)
    active: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Milestone(Base):
    """Exams, deadlines and other dated checkpoints."""

    __tablename__ = "milestones"

    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="deadline")  # exam | deadline | event
    title: Mapped[str] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(DateTime)
    notes: Mapped[str] = mapped_column(Text, default="")


class GoalVersion(Base):
    """Snapshot of every goal and milestone, written each time goals are changed in bulk."""

    __tablename__ = "goal_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    week_start: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual | import
    note: Mapped[str] = mapped_column(Text, default="")
    snapshot: Mapped[dict] = mapped_column(JSON)


class Retro(Base):
    __tablename__ = "retros"

    id: Mapped[int] = mapped_column(primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, unique=True)
    went_well: Mapped[str] = mapped_column(Text, default="")
    went_badly: Mapped[str] = mapped_column(Text, default="")
    change: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    energy: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1 to 5
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class PlanItem(Base):
    """A block in the weekly plan.

    kind "event" is pushed to the Planner Google Calendar; kind "task" is pushed
    to Todoist with a due time and duration (and reaches the calendar through
    Todoist's own calendar sync).
    """

    __tablename__ = "plan_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(10), default="task")  # task | event
    category: Mapped[str] = mapped_column(String(20), default="school")
    duration_min: Mapped[int] = mapped_column(Integer, default=60)
    start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    location: Mapped[str] = mapped_column(String(100), default="")
    priority: Mapped[int] = mapped_column(Integer, default=4)  # Todoist style: 1 highest, 4 lowest
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("goals.id"), nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    # "app" items were created here; "todoist" items were imported from an existing task,
    # so the app never deletes their Todoist task, it only reschedules it.
    origin: Mapped[str] = mapped_column(String(10), default="app")
    todoist_task_id: Mapped[str] = mapped_column(String(50), default="")
    todoist_project_id: Mapped[str] = mapped_column(String(50), default="")
    gcal_event_id: Mapped[str] = mapped_column(String(200), default="")
    committed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # True when edited since the last commit.
    dirty: Mapped[bool] = mapped_column(default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    @property
    def scheduled(self) -> bool:
        return self.start is not None


class Setting(Base):
    """Key/value store for preferences (constraints, calendar selection, travel table)."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict | list | str | int | float | None] = mapped_column(JSON)
