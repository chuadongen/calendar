# Sprint Planner

A personal web app for a weekly scrum ritual: **review** the week, write a **retrospective**, update **goals**, then **plan** next week by dragging blocks onto a calendar. When you commit a plan, events go to a dedicated Google Calendar and tasks go to Todoist with a start time and duration, so they show up on your calendar too.

It runs on a home server and is reached over Tailscale. There is no login screen, because only devices on your tailnet can reach it.

## How the pieces fit

| Where | Holds |
|---|---|
| This app (SQLite in `data/`) | Goals (versioned), exams and deadlines, retrospectives, weekly plans, scheduling rules |
| Todoist | Completion of concrete tasks; the review page reads it |
| Google Calendar | Existing commitments (read as busy time) and plan events (written to the "Planner" calendar) |
| Any LLM chat | Discussion only. Export context, talk, paste the JSON reply back, approve the changes |

## The Sunday flow

1. **Review**: completed Todoist tasks for the week, grouped by goal. A task counts towards a goal when it has the goal's label (or is in the goal's project).
2. **Retrospective**: what went well, what did not, and one change. Each week's entry is saved.
3. **Goals**: *Copy context* gives a markdown snapshot (goals, milestones, this week's results, recent retros, and the reply schema). Paste it into Claude or Gemini and discuss. Then paste the chat's JSON reply into *Preview changes*, check the changes, and approve. Each approval saves a new version you can look back at.
4. **Plan**:
   * *From goals* adds backlog blocks that cover each goal's weekly hours.
   * *Import Todoist* pulls open tasks that have no time yet.
   * Drag blocks onto the week, or click and drag on empty space to create one. *Auto-fill* places the rest without clashes.
   * Clashes (overlaps, buffers, travel time) are outlined in red and listed in the sidebar.
   * *Commit* pushes everything. Committing again updates the same events and tasks instead of creating duplicates. Blocks you unschedule or delete are removed from Google Calendar and Todoist.

**Task or event?** A **task** is something you tick off (homework, a revision chapter). It becomes a timed Todoist task, which is where completion is tracked. An **event** is time that is simply blocked (lecture, gym, travel). It becomes a Google Calendar event, colour coded by category.

Tasks imported from Todoist are never deleted by the app. Unscheduling one only clears its date.

## Setup on the home server

### 1. Todoist

Copy your API token from Todoist (Settings → Integrations → Developer) into `.env` as `TODOIST_API_TOKEN`.

### 2. Tailscale HTTPS

```bash
sudo tailscale serve --bg 8000
tailscale serve status   # shows https://<machine>.<tailnet>.ts.net
```

Put that URL in `.env` as `BASE_URL`.

### 3. Google Calendar

1. In Google Cloud Console, create a project and enable the **Google Calendar API**.
2. Set up the OAuth consent screen (External), add yourself as a user, then **publish the app to "In production"**. In "Testing" mode, Google expires your refresh token every 7 days. Google will warn that the app is unverified; for personal use you just click through the warning.
3. Create an OAuth client of type **Web application**, with the redirect URI `BASE_URL/auth/google/callback`. The Settings page shows the exact value.
4. Download the JSON to `data/google_client_secret.json`.
5. Open **Settings → Connect**.

### 4. Run

```bash
cp .env.example .env    # then fill it in
docker compose up -d --build
```

Then, in Settings:

* Tick the calendars that count as busy.
* Tick the **Todoist sync calendar as ignored**. The app reads timed Todoist tasks directly from Todoist, so reading them from the calendar too would show them twice.
* Set your day hours, buffer, preferred hours per category, and travel times (`home | smu = 50`). A block with a location gets that much travel time kept around it.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload     # http://localhost:8000
.venv/bin/pytest
```

With `BASE_URL=http://localhost:8000`, Google OAuth also works locally. Register that redirect URI on the OAuth client as well.

The calendar uses the browser's timezone, so open the app on a device set to the same timezone as `TZ_NAME`.

## Known gaps

* The Todoist client targets the unified API v1 (`/api/v1`). It handles both paginated (`results` / `next_cursor`) and plain list responses, but it has not yet been run against a live account. If anything looks off, check `app/integrations/todoist.py` first.
* There is no in-app LLM call. The export and import flow works with any chat.
* MCP server (stretch goal): let a Claude revision chat update goal notes and topic confidence directly.
