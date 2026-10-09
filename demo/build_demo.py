"""Build a single-file, backend-free demo of the app with sample data.

The real templates are rendered with sample data, the pages are stitched into one
HTML file, and demo-api.js answers the planning API inside the browser.

    .venv/bin/python -m demo.build_demo   # writes demo/dist/sprint-demo.html
"""

import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="sprint-demo-")
os.environ["TODOIST_API_TOKEN"] = ""

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app import main, planning, review  # noqa: E402
from app.categories import CATEGORIES  # noqa: E402
from app.db import get_session, init_db  # noqa: E402
from app.models import Goal, Milestone, PlanItem, Retro  # noqa: E402
from app.prefs import get_prefs  # noqa: E402
from app.weeks import current_week, planning_week, to_aware  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = Path(__file__).resolve().parent / "dist" / "sprint-demo.html"
FULLCALENDAR = "https://cdn.jsdelivr.net/npm/fullcalendar@6.1.15/index.global.min.js"

THIS = current_week()
NEXT = planning_week()


def at(week: date, day: int, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime.combine(week + timedelta(days=day), time(h, m))


GOALS = [
    dict(title="Raise GPA this semester", area="school", metric="Module grades", target="A- average, keep scholarship",
         deadline=date(2026, 12, 5), notes="Algorithms is the weakest module. Revise right after lectures.",
         todoist_label="school", weekly_hours=8),
    dict(title="Cut to 75 kg at 14% body fat", area="fitness", metric="InBody scan", target="75 kg, 14%",
         deadline=date(2026, 12, 20), notes="Gym 3x a week, track protein.", todoist_label="fitness", weekly_hours=4),
    dict(title="Lock in career direction", area="work", metric="Coffee chats held", target="2 per week",
         notes="Founders, VCs, senior engineers. Write notes after each chat.", todoist_label="career", weekly_hours=3),
    dict(title="Weekly reset routine", area="life", metric="Sundays completed", target="Every Sunday",
         todoist_label="life", weekly_hours=1),
]

BUSY = [  # existing commitments in the planned week (Google Calendar and timed Todoist tasks)
    ("Algorithms lecture", 0, "12:00", "15:15", "smu", "gcal"),
    ("Algorithms lecture", 2, "12:00", "15:15", "smu", "gcal"),
    ("Databases seminar", 1, "08:15", "11:30", "smu", "gcal"),
    ("Databases seminar", 3, "08:15", "11:30", "smu", "gcal"),
    ("Project group meeting", 3, "15:30", "17:00", "smu", "gcal"),
    ("Family dinner", 6, "18:00", "20:00", "home", "gcal"),
    ("Submit Databases lab 4", 4, "21:00", "21:30", "", "todoist"),
]

BACKLOG = [
    dict(title="Algorithms problem set 5", kind="task", category="school", duration_min=120, priority=1, goal=0),
    dict(title="Revise Algorithms week 6: graphs", kind="event", category="revision", duration_min=90, priority=2, goal=0),
    dict(title="Gym: push day", kind="event", category="fitness", duration_min=75, priority=3, goal=1, location="gym"),
    dict(title="Gym: pull day", kind="event", category="fitness", duration_min=75, priority=3, goal=1, location="gym"),
    dict(title="Coffee chat with a founder", kind="event", category="work", duration_min=60, priority=2, goal=2, location="smu"),
    dict(title="Sunday review and planning", kind="task", category="life", duration_min=60, priority=2, goal=3),
]
SCHEDULED = [
    dict(title="Revise Databases normalisation", kind="event", category="revision", duration_min=90, priority=2, goal=0,
         start=(1, "13:00")),
    dict(title="Gym: leg day", kind="event", category="fitness", duration_min=75, priority=3, goal=1, location="gym",
         start=(0, "15:20")),  # clashes with travel from the lecture at SMU, to show the warning
]
TODOIST_POOL = [
    dict(title="Email prof about consultation", kind="task", category="school", duration_min=15, priority=3, goal_id=1,
         location="", notes=""),
    dict(title="Meal prep for the week", kind="task", category="fitness", duration_min=90, priority=4, goal_id=2,
         location="home", notes=""),
]
COMPLETED = [
    ("Algorithms problem set 4", "school", 4), ("Revise Algorithms week 5", "school", 3),
    ("Databases lab 3", "school", 4), ("Gym: push day", "fitness", 2), ("Gym: legs", "fitness", 2),
    ("Coffee chat: senior engineer at a fintech", "career", 3), ("Update LinkedIn headline", "career", 1),
    ("Laundry", "", 1),
]
RETROS = [
    (THIS - timedelta(days=14), "Mornings were productive when I slept before 1am.", "Skipped two gym sessions.",
     "Gym right after the last class of the day.", 3),
    (THIS - timedelta(days=7), "Finished problem set early. Two good coffee chats.", "Phone in bed again, slept late.",
     "Phone charges outside the bedroom.", 4),
]


class FakeTodoist:
    configured = True

    def completed_between(self, since, until):
        return [{"id": str(n), "task_id": str(n), "content": c, "labels": [l] if l else [], "priority": p,
                 "completed_at": (THIS + timedelta(days=n % 6)).isoformat() + "T10:00:00Z"}
                for n, (c, l, p) in enumerate(COMPLETED)]

    def projects(self):
        return [{"id": "inbox", "name": "Inbox"}, {"id": "school", "name": "School"}]


def seed(db) -> list[Goal]:
    goals = [Goal(**g) for g in GOALS]
    db.add_all(goals)
    db.commit()
    db.add_all([
        Milestone(title="Databases lab 4 due", kind="deadline", at=at(NEXT, 4, "23:59"), goal_id=goals[0].id),
        Milestone(title="Algorithms midterm", kind="exam", at=at(NEXT, 9, "09:00"), goal_id=goals[0].id),
        Milestone(title="InBody scan", kind="event", at=at(NEXT, 12, "10:00"), goal_id=goals[1].id),
    ])
    for week, good, bad, change, energy in RETROS:
        db.add(Retro(week_start=week, went_well=good, went_badly=bad, change=change, energy=energy))
    for spec in BACKLOG + SCHEDULED:
        spec = dict(spec)
        goal = goals[spec.pop("goal")]
        start = spec.pop("start", None)
        db.add(PlanItem(week_start=NEXT, goal_id=goal.id, start=at(NEXT, *start) if start else None, **spec))
    db.commit()
    from app import goals as goals_svc

    goals_svc.snapshot(db, THIS, "import", "Set goals for the semester")
    return goals


def busy() -> planning.Busy:
    b = planning.Busy()
    for title, day, s, e, loc, source in BUSY:
        start, end = at(NEXT, day, s), at(NEXT, day, e)
        b.events.append({"title": title, "start": start, "end": end, "allDay": False, "source": source,
                         "location": loc})
        b.slots.append(planning.scheduler.Slot(start, end, loc, title))
    return b


def extract(html: str, pattern: str) -> str:
    m = re.search(pattern, html, re.S)
    if not m:
        sys.exit(f"pattern not found: {pattern[:60]}")
    return m.group(1)


def build() -> None:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    init_db(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    goals = seed(db)
    sample_busy = busy()

    main.app.dependency_overrides[get_session] = lambda: db
    planning.gather_busy = lambda *a, **k: sample_busy
    review.todoist.Todoist = FakeTodoist
    main.todoist.Todoist = FakeTodoist

    pages = {}
    with TestClient(main.app) as client:
        for name, path in [("review", f"/review?week={THIS}"), ("retro", f"/retro?week={THIS}"),
                           ("goals", "/goals"), ("plan", f"/plan?week={NEXT}"), ("settings", "/settings")]:
            resp = client.get(path)
            resp.raise_for_status()
            pages[name] = resp.text
        export_text = client.get(f"/goals/export?week={THIS}").text
        api_items = client.get(f"/api/plan?week={NEXT}").json()["items"]

    sidebar = extract(pages["plan"], r'(<aside class="sidebar">.*?</aside>)')
    sections = []
    for name, html in pages.items():
        main_tag = extract(html, r'(<main class="main[^"]*">)')
        body = extract(html, r'<main class="main[^"]*">(.*?)</main>')
        body = re.sub(r'<div class="toast" id="toast" hidden></div>', "", body)
        sections.append(main_tag.replace("<main ", f'<main data-page="{name}" hidden ') + body + "</main>")

    goals_script = extract(pages["goals"], r'(<script>\s*document\.getElementById\("copy-context"\).*?</script>)')
    plan_config = extract(pages["plan"], r"(<script>\s*window\.PLAN = .*?</script>)")

    items = [{k: i[k] for k in ("id", "title", "kind", "category", "duration_min", "start", "location",
                                 "priority", "goal_id", "notes", "origin")} for i in api_items]
    seed_data = {
        "seedVersion": f"{NEXT}-1",
        "week": NEXT.isoformat(),
        "prefs": get_prefs(db),
        "categories": CATEGORIES,
        "goals": [{"id": g.id, "title": g.title, "area": g.area, "weekly_hours": g.weekly_hours} for g in goals],
        "items": items,
        "busy": [{**e, "start": to_aware(e["start"]).isoformat(), "end": to_aware(e["end"]).isoformat()}
                 for e in sample_busy.events],
        "todoist_pool": TODOIST_POOL,
        "export_text": export_text,
    }

    css = (ROOT / "app/static/css/app.css").read_text()
    shell_js = (Path(__file__).parent / "demo-shell.js").read_text()
    api_js = (Path(__file__).parent / "demo-api.js").read_text()
    plan_js = (ROOT / "app/static/js/plan.js").read_text()
    def safe(s: str) -> str:
        return s.replace("</script", "<\\/script")
    seed_json = safe(json.dumps(seed_data, default=str))

    doc = f"""<title>Sprint Planner</title>
<style>
{css}
html, body {{ height: 100%; }}
.main.full {{ height: 100%; }}
.shell {{ min-height: 100%; height: 100%; }}
.sidebar {{ height: 100%; }}
.demo-reset {{ background: none; border: 0; padding: 0; color: var(--accent); cursor: pointer; font: inherit; text-align: left; }}
</style>
<div class="shell">
{sidebar}
{"".join(sections)}
</div>
<div class="toast" id="toast" hidden></div>
<dialog id="export-dialog"><form method="dialog"><div class="head"><h2>Context for your chat</h2><button class="btn ghost icon" value="close">✕</button></div><pre class="code" id="export-text"></pre></form></dialog>
<script src="{FULLCALENDAR}"></script>
<script>window.DEMO_SEED = {seed_json};</script>
<script>{safe(api_js)}</script>
<script>{safe(shell_js)}</script>
{plan_config}
<script>{safe(plan_js)}</script>
{goals_script}
"""
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(doc)
    print(f"wrote {OUT} ({len(doc) // 1024} KB)")


if __name__ == "__main__":
    build()
