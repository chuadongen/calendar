"""Thin client for the Todoist API v1 (https://developer.todoist.com/api/v1/)."""

from datetime import datetime, timezone

import httpx

from app.config import settings
from app.weeks import to_local_naive

BASE = "https://api.todoist.com/api/v1"


class TodoistError(RuntimeError):
    pass


class Todoist:
    def __init__(self, token: str | None = None, transport: httpx.BaseTransport | None = None):
        self.token = token if token is not None else settings.todoist_token
        self._client = httpx.Client(
            base_url=BASE,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=20,
            transport=transport,
        )

    @property
    def configured(self) -> bool:
        return bool(self.token)

    def _request(self, method: str, path: str, **kwargs):
        if not self.configured:
            raise TodoistError("TODOIST_API_TOKEN is not set")
        resp = self._client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            raise TodoistError(f"Todoist {method} {path} failed: {resp.status_code} {resp.text[:200]}")
        return resp.json() if resp.content else None

    def _paginate(self, path: str, params: dict | None = None, key: str = "results") -> list[dict]:
        params = dict(params or {})
        out: list[dict] = []
        while True:
            data = self._request("GET", path, params=params)
            if isinstance(data, list):  # older un-paginated shape
                return out + data
            out.extend(data.get(key) or data.get("items") or [])
            cursor = data.get("next_cursor")
            if not cursor:
                return out
            params["cursor"] = cursor

    # Reads

    def projects(self) -> list[dict]:
        return self._paginate("/projects")

    def labels(self) -> list[dict]:
        return self._paginate("/labels")

    def open_tasks(self, project_id: str | None = None) -> list[dict]:
        return self._paginate("/tasks", {"project_id": project_id} if project_id else None)

    def completed_between(self, since: datetime, until: datetime) -> list[dict]:
        params = {"since": _utc(since), "until": _utc(until), "limit": 200}
        return self._paginate("/tasks/completed/by_completion_date", params, key="items")

    # Writes

    def create_task(self, payload: dict) -> dict:
        return self._request("POST", "/tasks", json=payload)

    def update_task(self, task_id: str, payload: dict) -> dict:
        return self._request("POST", f"/tasks/{task_id}", json=payload)

    def delete_task(self, task_id: str) -> None:
        self._request("DELETE", f"/tasks/{task_id}")


def _utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=settings.tz)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def task_payload(title: str, start: datetime, duration_min: int, priority: int,
                 project_id: str = "", labels: list[str] | None = None, description: str = "") -> dict:
    payload = {
        "content": title,
        "description": description,
        "due_datetime": _utc(start),
        "duration": duration_min,
        "duration_unit": "minute",
        # Todoist API priority is inverted: 4 is p1 (urgent), 1 is p4.
        "priority": 5 - priority,
    }
    if project_id:
        payload["project_id"] = project_id
    if labels:
        payload["labels"] = labels
    return payload


def task_start(task: dict) -> datetime | None:
    """Start time of a timed task as naive local time, or None for date-only tasks."""
    due = task.get("due") or {}
    raw = due.get("datetime") or due.get("date") or ""
    if "T" not in raw:
        return None
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt  # floating time is already local
    return to_local_naive(dt)


def task_duration(task: dict, default: int = 30) -> int:
    d = task.get("duration") or {}
    amount = d.get("amount")
    if not amount:
        return default
    return int(amount) * (1440 if d.get("unit") == "day" else 1)


def ui_priority(task: dict) -> int:
    """Convert the API priority (4 = urgent) to the UI convention (p1 = urgent)."""
    return 5 - int(task.get("priority") or 1)
