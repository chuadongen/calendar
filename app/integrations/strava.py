"""Strava activities via OAuth (https://developers.strava.com/docs/reference/).

Create an API application at https://www.strava.com/settings/api and set its
"Authorization Callback Domain" to the host part of BASE_URL.
"""

import json
import time
from datetime import datetime
from urllib.parse import urlencode

import httpx

from app.config import settings

AUTHORIZE = "https://www.strava.com/oauth/authorize"
TOKEN = "https://www.strava.com/oauth/token"
API = "https://www.strava.com/api/v3"


class StravaError(RuntimeError):
    pass


def configured() -> bool:
    return bool(settings.strava_client_id and settings.strava_client_secret)


def connected() -> bool:
    return settings.strava_token_path.exists()


def redirect_uri() -> str:
    return f"{settings.base_url}/auth/strava/callback"


def auth_url(state: str) -> str:
    return AUTHORIZE + "?" + urlencode({
        "client_id": settings.strava_client_id, "redirect_uri": redirect_uri(), "response_type": "code",
        "approval_prompt": "auto", "scope": "activity:read_all", "state": state,
    })


def _save(data: dict) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    keep = {k: data[k] for k in ("access_token", "refresh_token", "expires_at")}
    settings.strava_token_path.write_text(json.dumps(keep))


def _token_request(payload: dict, transport=None) -> dict:
    with httpx.Client(timeout=20, transport=transport) as c:
        resp = c.post(TOKEN, data={"client_id": settings.strava_client_id,
                                   "client_secret": settings.strava_client_secret, **payload})
    if resp.status_code >= 400:
        raise StravaError(f"Strava token request failed: {resp.status_code} {resp.text[:200]}")
    return resp.json()


def finish_auth(code: str, transport=None) -> None:
    _save(_token_request({"code": code, "grant_type": "authorization_code"}, transport))


def _access_token(transport=None) -> str:
    if not connected():
        raise StravaError("Strava is not connected")
    data = json.loads(settings.strava_token_path.read_text())
    if data["expires_at"] < time.time() + 60:
        data = _token_request({"grant_type": "refresh_token", "refresh_token": data["refresh_token"]}, transport)
        _save(data)
    return data["access_token"]


def activities(begin: datetime, end: datetime, transport=None) -> list[dict]:
    """Activities started in [begin, end), newest last, simplified for display."""
    token = _access_token(transport)
    out: list[dict] = []
    page = 1
    with httpx.Client(base_url=API, timeout=20, transport=transport,
                      headers={"Authorization": f"Bearer {token}"}) as c:
        while True:
            resp = c.get("/athlete/activities", params={
                "after": int(_aware(begin).timestamp()), "before": int(_aware(end).timestamp()),
                "per_page": 100, "page": page})
            if resp.status_code >= 400:
                raise StravaError(f"Strava activities failed: {resp.status_code} {resp.text[:200]}")
            batch = resp.json()
            out.extend(simplify(a) for a in batch)
            if len(batch) < 100:
                break
            page += 1
    return sorted(out, key=lambda a: a["start"])


def simplify(a: dict) -> dict:
    # start_date_local is wall-clock time even though it carries a Z suffix.
    start = datetime.fromisoformat(a["start_date_local"].replace("Z", ""))
    return {
        "name": a.get("name", ""),
        "type": a.get("sport_type") or a.get("type", ""),
        "start": start,
        "minutes": round((a.get("moving_time") or 0) / 60),
        "km": round((a.get("distance") or 0) / 1000, 1),
        "source": "strava",
    }


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=settings.tz) if dt.tzinfo is None else dt
