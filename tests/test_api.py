import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from app.routers import api

WEEK = "2026-10-12"


@pytest.fixture
def client(db, monkeypatch):
    from app import planning

    monkeypatch.setattr(planning, "gather_busy", lambda db, week, prefs, **kw: planning.Busy())
    api.clear_busy_cache()
    app.dependency_overrides[get_session] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_pages_render(client):
    for path in ["/review", "/retro", "/goals", f"/plan?week={WEEK}", "/settings", "/goals/export"]:
        assert client.get(path).status_code == 200, path


def test_plan_item_lifecycle(client):
    r = client.post(f"/api/plan/items?week={WEEK}", json={"title": "HW3", "kind": "task", "duration_min": 90, "category": "school"})
    item = r.json()
    assert item["start"] is None and item["color"] == "#8E24AA"

    r = client.patch(f"/api/plan/items/{item['id']}", json={"start": "2026-10-13T01:00:00.000Z"})
    assert r.json()["start"] == "2026-10-13T09:00:00+08:00"
    assert r.json()["end"] == "2026-10-13T10:30:00+08:00"

    other = client.post(f"/api/plan/items?week={WEEK}", json={"title": "Clash", "kind": "event",
                                                              "start": "2026-10-13T01:30:00Z", "duration_min": 60}).json()
    plan = client.get(f"/api/plan?week={WEEK}").json()
    assert {i["id"]: bool(i["issues"]) for i in plan["items"]} == {item["id"]: True, other["id"]: True}

    client.patch(f"/api/plan/items/{other['id']}", json={"unschedule": True})
    plan = client.get(f"/api/plan?week={WEEK}").json()
    assert plan["summary"]["backlog"] == 1 and not any(i["issues"] for i in plan["items"])

    assert client.post(f"/api/plan/autofill?week={WEEK}").json()["placed"] == 1
    assert client.delete(f"/api/plan/items/{other['id']}").json() == {"errors": []}


def test_validation(client):
    assert client.post(f"/api/plan/items?week={WEEK}", json={"title": " "}).status_code == 400
    assert client.post(f"/api/plan/items?week={WEEK}", json={"title": "x", "category": "nope"}).status_code == 400
    assert client.post(f"/api/plan/items?week={WEEK}", json={"title": "x", "duration_min": 2}).status_code == 400
    assert client.get("/api/plan?week=bad").status_code == 400


def test_from_goals_covers_weekly_hours_once(client):
    client.post("/goals", data={"title": "ST2131", "area": "school", "weekly_hours": "4"})
    assert client.post(f"/api/plan/from-goals?week={WEEK}").json()["added"] == 3  # 90 + 90 + 60
    assert client.post(f"/api/plan/from-goals?week={WEEK}").json()["added"] == 0
    items = client.get(f"/api/plan?week={WEEK}").json()["items"]
    assert sorted(i["duration_min"] for i in items) == [60, 90, 90]
    assert {i["category"] for i in items} == {"revision"}


def test_retro_and_goal_import_flow(client):
    r = client.post("/retro", data={"week": WEEK, "went_well": "Good sleep", "energy": "4"}, follow_redirects=False)
    assert r.status_code == 303
    assert "Good sleep" in client.get(f"/retro?week={WEEK}").text

    payload = '{"note": "n", "upsert_goals": [{"title": "Run 5k", "area": "fitness"}]}'
    preview = client.post("/goals/import", data={"payload": payload})
    assert "Add goal" in preview.text and "Run 5k" in preview.text
    client.post("/goals/import/apply", data={"payload": payload})
    assert "Run 5k" in client.get("/goals").text


def test_dashboard_renders_and_task_toggle(client, monkeypatch):
    assert client.get(f"/dashboard?week={WEEK}").status_code == 200
    calls = []
    from app.integrations import todoist

    monkeypatch.setattr(todoist.Todoist, "close_task", lambda self, tid: calls.append(("close", tid)))
    monkeypatch.setattr(todoist.Todoist, "reopen_task", lambda self, tid: calls.append(("reopen", tid)))
    assert client.post("/api/tasks/42/state", json={"done": True}).json() == {"id": "42", "done": True}
    client.post("/api/tasks/42/state", json={"done": False})
    assert calls == [("close", "42"), ("reopen", "42")]
