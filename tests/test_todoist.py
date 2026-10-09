from datetime import datetime

import httpx

from app.integrations import todoist


def test_task_start_handles_floating_and_utc_times():
    assert todoist.task_start({"due": {"date": "2026-10-13T09:00:00"}}) == datetime(2026, 10, 13, 9)
    assert todoist.task_start({"due": {"date": "2026-10-13T01:00:00Z"}}) == datetime(2026, 10, 13, 9)
    assert todoist.task_start({"due": {"datetime": "2026-10-13T01:00:00Z", "date": "2026-10-13"}}) == datetime(2026, 10, 13, 9)
    assert todoist.task_start({"due": {"date": "2026-10-13"}}) is None
    assert todoist.task_start({"due": None}) is None


def test_duration_and_priority_conversion():
    assert todoist.task_duration({"duration": {"amount": 2, "unit": "day"}}) == 2880
    assert todoist.task_duration({}, default=45) == 45
    assert todoist.ui_priority({"priority": 4}) == 1


def test_client_follows_cursor_pagination():
    calls = []

    def handler(request: httpx.Request):
        calls.append(dict(request.url.params))
        assert request.headers["Authorization"] == "Bearer tok"
        if "cursor" not in request.url.params:
            return httpx.Response(200, json={"results": [{"id": "1"}], "next_cursor": "abc"})
        return httpx.Response(200, json={"results": [{"id": "2"}], "next_cursor": None})

    client = todoist.Todoist(token="tok", transport=httpx.MockTransport(handler))
    assert [t["id"] for t in client.open_tasks()] == ["1", "2"]
    assert calls[1]["cursor"] == "abc"


def test_client_raises_on_error():
    client = todoist.Todoist(token="tok", transport=httpx.MockTransport(lambda r: httpx.Response(401, text="bad")))
    try:
        client.projects()
    except todoist.TodoistError as exc:
        assert "401" in str(exc)
    else:
        raise AssertionError("expected TodoistError")
