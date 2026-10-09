"""Google Calendar access via OAuth (web flow, single user).

The OAuth client secret JSON comes from a Google Cloud project with the Calendar
API enabled. Its redirect URI must be BASE_URL + /auth/google/callback.
Tokens are stored in data/google_token.json.
"""

import json
import os
from datetime import datetime

from app.config import settings
from app.weeks import to_aware, to_local_naive

SCOPES = ["https://www.googleapis.com/auth/calendar"]
PLANNER_KEY = "planner_item_id"


class GcalError(RuntimeError):
    pass


def redirect_uri() -> str:
    return f"{settings.base_url}/auth/google/callback"


def secrets_present() -> bool:
    from pathlib import Path

    return Path(settings.google_client_secrets).exists()


def _flow(state: str | None = None):
    from google_auth_oauthlib.flow import Flow

    if not secrets_present():
        raise GcalError(f"Google client secret not found at {settings.google_client_secrets}")
    flow = Flow.from_client_secrets_file(settings.google_client_secrets, scopes=SCOPES, state=state)
    flow.redirect_uri = redirect_uri()
    return flow


def auth_url() -> tuple[str, str, str | None]:
    flow = _flow()
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    return url, state, getattr(flow, "code_verifier", None)


def finish_auth(authorization_response: str, state: str, code_verifier: str | None) -> None:
    # Google may return previously granted scopes too; that is fine.
    os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
    if settings.base_url.startswith("http://localhost") or settings.base_url.startswith("http://127.0.0.1"):
        os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    flow = _flow(state)
    if code_verifier:
        flow.code_verifier = code_verifier
    flow.fetch_token(authorization_response=authorization_response)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.google_token_path.write_text(flow.credentials.to_json())


def _credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = settings.google_token_path
    if not path.exists():
        return None
    creds = Credentials.from_authorized_user_info(json.loads(path.read_text()), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json())
    return creds


def connected() -> bool:
    try:
        return _credentials() is not None
    except Exception:
        return False


class Calendar:
    def __init__(self, service=None):
        if service is None:
            from googleapiclient.discovery import build

            creds = _credentials()
            if creds is None:
                raise GcalError("Google Calendar is not connected")
            service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        self.svc = service

    def calendars(self) -> list[dict]:
        return self.svc.calendarList().list().execute().get("items", [])

    def ensure_planner_calendar(self, existing_id: str) -> str:
        if existing_id:
            return existing_id
        for cal in self.calendars():
            if cal.get("summary") == settings.planner_calendar_name:
                return cal["id"]
        created = self.svc.calendars().insert(
            body={"summary": settings.planner_calendar_name, "timeZone": settings.timezone}
        ).execute()
        return created["id"]

    def events(self, calendar_id: str, start: datetime, end: datetime) -> list[dict]:
        items: list[dict] = []
        token = None
        while True:
            resp = self.svc.events().list(
                calendarId=calendar_id,
                timeMin=to_aware(start).isoformat(),
                timeMax=to_aware(end).isoformat(),
                singleEvents=True,
                orderBy="startTime",
                pageToken=token,
                maxResults=250,
            ).execute()
            items.extend(resp.get("items", []))
            token = resp.get("nextPageToken")
            if not token:
                return items

    def upsert_event(self, calendar_id: str, event_id: str, body: dict) -> str:
        if event_id:
            try:
                return self.svc.events().patch(calendarId=calendar_id, eventId=event_id, body=body).execute()["id"]
            except Exception as exc:  # deleted on the Google side: recreate
                if "404" not in str(exc) and "410" not in str(exc):
                    raise
        return self.svc.events().insert(calendarId=calendar_id, body=body).execute()["id"]

    def delete_event(self, calendar_id: str, event_id: str) -> None:
        try:
            self.svc.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        except Exception as exc:
            if "404" not in str(exc) and "410" not in str(exc):
                raise


def event_body(item_id: int, title: str, start: datetime, end: datetime, color_id: str,
               location: str = "", description: str = "") -> dict:
    return {
        "summary": title,
        "location": location,
        "description": description,
        "start": {"dateTime": to_aware(start).isoformat(), "timeZone": settings.timezone},
        "end": {"dateTime": to_aware(end).isoformat(), "timeZone": settings.timezone},
        "colorId": color_id,
        "extendedProperties": {"private": {PLANNER_KEY: str(item_id)}},
    }


def event_span(event: dict) -> tuple[datetime, datetime, bool]:
    """(start, end, all_day) in naive local time."""
    s, e = event.get("start", {}), event.get("end", {})
    if "dateTime" in s:
        return (to_local_naive(datetime.fromisoformat(s["dateTime"])),
                to_local_naive(datetime.fromisoformat(e["dateTime"])), False)
    return datetime.fromisoformat(s["date"]), datetime.fromisoformat(e["date"]), True


def is_planner_event(event: dict) -> bool:
    return PLANNER_KEY in (event.get("extendedProperties", {}).get("private") or {})
