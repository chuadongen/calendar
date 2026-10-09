import os
import tempfile

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="planner-test-"))
os.environ["TODOIST_API_TOKEN"] = ""

import pytest
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine

from app.db import Base, init_db


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as session:
        yield session


class FakeCalendar:
    def __init__(self, events=None):
        self.events_by_cal = events or {}
        self.upserts, self.deleted = [], []
        self.next_id = 0

    def calendars(self):
        return [{"id": "primary", "summary": "Me", "primary": True}]

    def ensure_planner_calendar(self, existing_id):
        return existing_id or "planner-cal"

    def events(self, calendar_id, start, end):
        return self.events_by_cal.get(calendar_id, [])

    def upsert_event(self, calendar_id, event_id, body):
        self.upserts.append((calendar_id, event_id, body))
        if event_id:
            return event_id
        self.next_id += 1
        return f"ev{self.next_id}"

    def delete_event(self, calendar_id, event_id):
        self.deleted.append(event_id)


class FakeTodoist:
    configured = True

    def __init__(self, open_tasks=None, completed=None):
        self._open = open_tasks or []
        self._completed = completed or []
        self.created, self.updated, self.deleted = [], [], []

    def open_tasks(self, project_id=None):
        return self._open

    def completed_between(self, since, until):
        return self._completed

    def create_task(self, payload):
        self.created.append(payload)
        return {"id": f"t{len(self.created)}"}

    def update_task(self, task_id, payload):
        self.updated.append((task_id, payload))
        return {"id": task_id}

    def delete_task(self, task_id):
        self.deleted.append(task_id)


@pytest.fixture
def fake_cal():
    return FakeCalendar()


@pytest.fixture
def fake_todo():
    return FakeTodoist()
