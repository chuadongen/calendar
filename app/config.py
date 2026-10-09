"""Runtime configuration, read from environment variables (see .env.example)."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "data")))
    timezone: str = os.getenv("TZ_NAME", "Asia/Singapore")
    base_url: str = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")
    todoist_token: str = os.getenv("TODOIST_API_TOKEN", "")
    google_client_secrets: str = os.getenv("GOOGLE_CLIENT_SECRETS", "data/google_client_secret.json")
    planner_calendar_name: str = os.getenv("PLANNER_CALENDAR_NAME", "Planner")
    strava_client_id: str = os.getenv("STRAVA_CLIENT_ID", "")
    strava_client_secret: str = os.getenv("STRAVA_CLIENT_SECRET", "")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "planner.db"

    @property
    def strava_token_path(self) -> Path:
        return self.data_dir / "strava_token.json"

    @property
    def google_token_path(self) -> Path:
        return self.data_dir / "google_token.json"


settings = Settings()
