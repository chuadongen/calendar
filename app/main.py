from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import goals as goals_svc
from app import planning, review
from app.categories import CATEGORIES, PRIORITY_COLORS
from app.config import settings
from app.db import get_session, init_db
from app.integrations import gcal, todoist
from app.models import AREAS, Goal, GoalVersion, Milestone, Retro
from app.prefs import get_prefs, set_pref
from app.routers import api

ROOT = Path(__file__).parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Sprint Planner", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
app.include_router(api.router)
templates = Jinja2Templates(directory=ROOT / "templates")
templates.env.globals.update(categories=CATEGORIES, priority_colors=PRIORITY_COLORS, areas=AREAS,
                             delta=lambda days: timedelta(days=days))

# OAuth state between redirect and callback (single user, in memory is fine).
_oauth: dict[str, str | None] = {}


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    from app.weeks import current_week, planning_week

    ctx.setdefault("nav", name.split(".")[0])
    ctx.setdefault("this_week", current_week())
    ctx.setdefault("next_week", planning_week())
    return templates.TemplateResponse(request, name, ctx)


def _week(value: str | None, default: date) -> date:
    from app.weeks import parse_week

    try:
        return parse_week(value, default)
    except ValueError:
        raise HTTPException(400, "Invalid week date")


@app.get("/")
def home():
    return RedirectResponse("/review")


# Review


@app.get("/review")
def review_page(request: Request, week: str | None = None, db: Session = Depends(get_session)):
    from app.weeks import current_week

    wk = _week(week, current_week())
    data = review.week_review(db, wk)
    upcoming = db.query(Milestone).filter(Milestone.at >= datetime.combine(wk, datetime.min.time())) \
        .order_by(Milestone.at).limit(6).all()
    return render(request, "review.html", week=wk, data=data, upcoming=upcoming,
                  prev_week=wk - timedelta(days=7), following_week=wk + timedelta(days=7))


# Retrospective


@app.get("/retro")
def retro_page(request: Request, week: str | None = None, db: Session = Depends(get_session)):
    from app.weeks import current_week

    wk = _week(week, current_week())
    retro = db.query(Retro).filter(Retro.week_start == wk).first()
    history = db.query(Retro).order_by(Retro.week_start.desc()).limit(12).all()
    return render(request, "retro.html", week=wk, retro=retro, history=history,
                  prev_week=wk - timedelta(days=7), following_week=wk + timedelta(days=7))


@app.post("/retro")
def save_retro(week: str = Form(...), went_well: str = Form(""), went_badly: str = Form(""),
               change: str = Form(""), notes: str = Form(""), energy: str = Form(""),
               db: Session = Depends(get_session)):
    from app.weeks import current_week

    wk = _week(week, current_week())
    retro = db.query(Retro).filter(Retro.week_start == wk).first() or Retro(week_start=wk)
    retro.went_well, retro.went_badly, retro.change, retro.notes = went_well, went_badly, change, notes
    retro.energy = int(energy) if energy.isdigit() else None
    db.add(retro)
    db.commit()
    return RedirectResponse(f"/retro?week={wk}&saved=1", status_code=303)


# Planning


@app.get("/plan")
def plan_page(request: Request, week: str | None = None, db: Session = Depends(get_session)):
    from app.weeks import planning_week

    wk = _week(week, planning_week())
    goal_rows = db.query(Goal).filter(Goal.active.is_(True)).order_by(Goal.area, Goal.title).all()
    prefs = get_prefs(db)
    return render(request, "plan.html", week=wk, goals=goal_rows, prefs=prefs,
                  prev_week=wk - timedelta(days=7), following_week=wk + timedelta(days=7))


# Goals


@app.get("/goals")
def goals_page(request: Request, db: Session = Depends(get_session)):
    active = db.query(Goal).filter(Goal.active.is_(True)).order_by(Goal.area, Goal.id).all()
    archived = db.query(Goal).filter(Goal.active.is_(False)).order_by(Goal.id).all()
    milestones = db.query(Milestone).order_by(Milestone.at).all()
    versions = db.query(GoalVersion).order_by(GoalVersion.id.desc()).limit(20).all()
    return render(request, "goals.html", goals=active, archived=archived, milestones=milestones,
                  versions=versions, goal_names={g.id: g.title for g in active + archived})


@app.post("/goals")
def save_goal(goal_id: str = Form(""), area: str = Form("life"), title: str = Form(...),
              metric: str = Form(""), target: str = Form(""), deadline: str = Form(""),
              notes: str = Form(""), todoist_label: str = Form(""), todoist_project_id: str = Form(""),
              weekly_hours: str = Form("0"), db: Session = Depends(get_session)):
    from app.weeks import current_week

    goal = db.get(Goal, int(goal_id)) if goal_id else None
    if goal is None:
        goal = Goal()
        db.add(goal)
    goal.area = area if area in AREAS else "life"
    goal.title, goal.metric, goal.target, goal.notes = title.strip(), metric, target, notes
    goal.deadline = date.fromisoformat(deadline) if deadline else None
    goal.todoist_label = todoist_label.strip().lstrip("@")
    goal.todoist_project_id = todoist_project_id.strip()
    goal.weekly_hours = float(weekly_hours or 0)
    db.commit()
    goals_svc.snapshot(db, current_week(), "manual", f"Edited goal: {goal.title}")
    return RedirectResponse("/goals", status_code=303)


@app.post("/goals/{goal_id}/archive")
def archive_goal(goal_id: int, db: Session = Depends(get_session)):
    from app.weeks import current_week

    goal = db.get(Goal, goal_id)
    if goal:
        goal.active = not goal.active
        db.commit()
        goals_svc.snapshot(db, current_week(), "manual", f"{'Restored' if goal.active else 'Archived'}: {goal.title}")
    return RedirectResponse("/goals", status_code=303)


@app.post("/milestones")
def save_milestone(milestone_id: str = Form(""), title: str = Form(...), kind: str = Form("deadline"),
                   at: str = Form(...), goal_id: str = Form(""), notes: str = Form(""),
                   db: Session = Depends(get_session)):
    m = db.get(Milestone, int(milestone_id)) if milestone_id else None
    if m is None:
        m = Milestone()
        db.add(m)
    m.title, m.kind, m.notes = title.strip(), kind, notes
    m.at = datetime.fromisoformat(at)
    m.goal_id = int(goal_id) if goal_id else None
    db.commit()
    return RedirectResponse("/goals#milestones", status_code=303)


@app.post("/milestones/{milestone_id}/delete")
def delete_milestone(milestone_id: int, db: Session = Depends(get_session)):
    m = db.get(Milestone, milestone_id)
    if m:
        db.delete(m)
        db.commit()
    return RedirectResponse("/goals#milestones", status_code=303)


@app.get("/goals/export", response_class=PlainTextResponse)
def export_context(week: str | None = None, include_review: bool = True, db: Session = Depends(get_session)):
    from app.weeks import current_week

    wk = _week(week, current_week())
    data = review.week_review(db, wk) if include_review else None
    if data and data["error"]:
        data = None
    return goals_svc.export_context(db, wk, data)


@app.post("/goals/import")
def import_preview(request: Request, payload: str = Form(...), db: Session = Depends(get_session)):
    error, changes = "", []
    try:
        update = goals_svc.parse_update(payload)
        changes = goals_svc.preview(db, update)
    except goals_svc.ImportError_ as exc:
        error = str(exc)
    return render(request, "import_preview.html", nav="goals", payload=payload, changes=changes, error=error)


@app.post("/goals/import/apply")
def import_apply(payload: str = Form(...), db: Session = Depends(get_session)):
    from app.weeks import current_week

    try:
        goals_svc.apply(db, goals_svc.parse_update(payload), current_week())
    except goals_svc.ImportError_ as exc:
        raise HTTPException(400, str(exc))
    return RedirectResponse("/goals?imported=1", status_code=303)


@app.get("/goals/versions/{version_id}")
def goal_version(version_id: int, db: Session = Depends(get_session)):
    v = db.get(GoalVersion, version_id)
    if v is None:
        raise HTTPException(404)
    return JSONResponse({"id": v.id, "created_at": v.created_at.isoformat(), "note": v.note,
                         "source": v.source, **v.snapshot})


# Settings and Google OAuth


@app.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_session)):
    prefs = get_prefs(db)
    calendars, projects, errors = [], [], []
    if gcal.connected():
        try:
            calendars = gcal.Calendar().calendars()
        except Exception as exc:
            errors.append(f"Google Calendar: {exc}")
    td = todoist.Todoist()
    if td.configured:
        try:
            projects = td.projects()
        except Exception as exc:
            errors.append(f"Todoist: {exc}")
    return render(request, "settings.html", prefs=prefs, calendars=calendars, projects=projects,
                  errors=errors, google_connected=gcal.connected(), google_secrets=gcal.secrets_present(),
                  todoist_configured=td.configured, redirect_uri=gcal.redirect_uri(), cfg=settings)


@app.post("/settings")
async def save_settings(request: Request, db: Session = Depends(get_session)):
    form = await request.form()
    set_pref(db, "day_start", form.get("day_start", "08:00"))
    set_pref(db, "day_end", form.get("day_end", "23:00"))
    set_pref(db, "buffer_min", int(form.get("buffer_min") or 0))
    set_pref(db, "busy_calendar_ids", form.getlist("busy_calendar_ids"))
    set_pref(db, "ignored_calendar_ids", form.getlist("ignored_calendar_ids"))
    set_pref(db, "todoist_default_project_id", form.get("todoist_default_project_id", ""))
    windows = {}
    for key in CATEGORIES:
        a, b = form.get(f"win_{key}_start"), form.get(f"win_{key}_end")
        if a and b:
            windows[key] = [a, b]
    set_pref(db, "preferred_windows", windows)
    travel = {}
    for line in str(form.get("travel", "")).splitlines():
        # Format per line: "home | smu = 50"
        if "=" in line and "|" in line:
            pair, minutes = line.split("=", 1)
            a, b = (p.strip().lower() for p in pair.split("|", 1))
            if a and b and minutes.strip().isdigit():
                travel[f"{a}|{b}"] = int(minutes.strip())
    set_pref(db, "travel_minutes", travel)
    api.clear_busy_cache()
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.get("/auth/google")
def google_auth():
    try:
        url, state, verifier = gcal.auth_url()
    except gcal.GcalError as exc:
        raise HTTPException(400, str(exc))
    _oauth["state"], _oauth["verifier"] = state, verifier
    return RedirectResponse(url)


@app.get("/auth/google/callback")
def google_callback(request: Request):
    state = _oauth.get("state")
    if not state or request.query_params.get("state") != state:
        raise HTTPException(400, "OAuth state mismatch, start again from Settings.")
    url = f"{settings.base_url}{request.url.path}?{request.url.query}"
    gcal.finish_auth(url, state, _oauth.get("verifier"))
    _oauth.clear()
    api.clear_busy_cache()
    return RedirectResponse("/settings?google=connected", status_code=303)


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return "ok"
