from datetime import date

import pytest

from app import goals
from app.models import Goal, GoalVersion, Milestone, Retro

WEEK = date(2026, 10, 12)


def test_parse_accepts_chat_reply_with_code_fence():
    text = 'Sure, here it is:\n```json\n{"note": "x", "upsert_goals": [{"title": "Run 5k", "area": "fitness"}]}\n```\nGood luck!'
    update = goals.parse_update(text)
    assert update.upsert_goals[0].title == "Run 5k"


@pytest.mark.parametrize("text, msg", [
    ("no json here", "No JSON"),
    ('{"upsert_goals": [{"title": "x", "area": "hobby"}]}', "area must be"),
    ('{"upsert_goals": [{"title": "x",}]}', "Invalid JSON"),
])
def test_parse_rejects_bad_input(text, msg):
    with pytest.raises(goals.ImportError_, match=msg):
        goals.parse_update(text)


def test_preview_lists_changes_and_rejects_unknown_ids(db):
    g = Goal(title="ST2131", area="school", target="B+")
    db.add(g)
    db.commit()
    update = goals.parse_update('{"upsert_goals": [{"id": %d, "title": "ST2131", "area": "school", "target": "A"},'
                                ' {"title": "New", "area": "life"}]}' % g.id)
    changes = goals.preview(db, update)
    assert changes[0]["action"] == "Update goal" and changes[0]["fields"]["target"] == ["B+", "A"]
    assert changes[1]["action"] == "Add goal"
    with pytest.raises(goals.ImportError_, match="does not exist"):
        goals.preview(db, goals.parse_update('{"archive_goal_ids": [999]}'))


def test_apply_writes_changes_and_a_version(db):
    g = Goal(title="Old", area="work")
    db.add(g)
    db.commit()
    update = goals.parse_update(
        '{"note": "Sprint 3", "upsert_goals": [{"title": "Gym 3x", "area": "fitness", "weekly_hours": 4}],'
        ' "archive_goal_ids": [%d],'
        ' "upsert_milestones": [{"title": "Midterm", "kind": "exam", "at": "2026-10-20T09:00"}]}' % g.id)
    goals.apply(db, update, WEEK)
    assert not db.get(Goal, g.id).active
    assert db.query(Goal).filter(Goal.title == "Gym 3x").one().weekly_hours == 4
    assert db.query(Milestone).one().title == "Midterm"
    version = db.query(GoalVersion).one()
    assert version.source == "import" and version.note == "Sprint 3"
    assert {x["title"] for x in version.snapshot["goals"]} == {"Old", "Gym 3x"}


def test_export_context_contains_goals_retros_and_schema(db):
    db.add(Goal(title="ST2131", area="school", todoist_label="st2131", weekly_hours=6))
    db.add(Retro(week_start=WEEK, went_well="Mornings", change="Sleep by 12"))
    db.commit()
    text = goals.export_context(db, WEEK, {"planned_done": 3, "planned_total": 5,
                                           "goals": [{"title": "ST2131", "done": 3, "examples": ["Ch1"]}],
                                           "unmatched": []})
    assert "[id 1] (school) **ST2131**" in text
    assert "Planned tasks completed: 3 of 5" in text
    assert "Sleep by 12" in text
    assert '"upsert_goals"' in text
