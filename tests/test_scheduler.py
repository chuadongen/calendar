from datetime import date, datetime

from app.prefs import DEFAULTS
from app.scheduler import Slot, autofill, find_issues

PREFS = {**DEFAULTS, "buffer_min": 10, "travel_minutes": {"home|smu": 50}}
MON = date(2026, 10, 12)


def dt(day, hm):
    h, m = map(int, hm.split(":"))
    return datetime(2026, 10, day, h, m)


def test_overlap_with_busy_is_a_clash():
    item = Slot(dt(12, "10:00"), dt(12, "11:00"), item_id=1)
    busy = [Slot(dt(12, "10:30"), dt(12, "11:30"), title="Lecture")]
    issues = find_issues([item], busy, PREFS)
    assert [(i.item_id, i.level) for i in issues] == [(1, "clash")]
    assert "Lecture" in issues[0].message


def test_buffer_is_enforced():
    item = Slot(dt(12, "11:05"), dt(12, "12:00"), item_id=1)
    busy = [Slot(dt(12, "10:00"), dt(12, "11:00"), title="Lecture")]
    assert find_issues([item], busy, PREFS)[0].message.startswith("Less than 10 min")
    ok = Slot(dt(12, "11:10"), dt(12, "12:00"), item_id=1)
    assert find_issues([ok], busy, PREFS) == []


def test_travel_time_between_locations():
    item = Slot(dt(12, "11:30"), dt(12, "12:30"), location="home", item_id=1)
    busy = [Slot(dt(12, "10:00"), dt(12, "11:00"), location="SMU", title="Lecture")]
    issues = find_issues([item], busy, PREFS)
    assert issues and "50 min travel" in issues[0].message
    later = Slot(dt(12, "11:50"), dt(12, "12:30"), location="home", item_id=1)
    assert find_issues([later], busy, PREFS) == []


def test_clash_between_two_plan_items_flags_both():
    a = Slot(dt(12, "10:00"), dt(12, "11:00"), item_id=1)
    b = Slot(dt(12, "10:30"), dt(12, "11:30"), item_id=2)
    ids = sorted(i.item_id for i in find_issues([a, b], [], PREFS) if i.level == "clash")
    assert ids == [1, 2]


def test_outside_day_hours_is_only_a_warning():
    item = Slot(dt(12, "06:00"), dt(12, "07:00"), item_id=1)
    issues = find_issues([item], [], PREFS)
    assert [i.level for i in issues] == ["warning"]


def test_autofill_avoids_busy_and_uses_preferred_window():
    busy = [Slot(dt(d, "09:00"), dt(d, "11:00"), title="Class") for d in range(12, 19)]
    placed = autofill([(1, 60, "revision", "", 1)], busy, MON, PREFS)
    start = placed[1]
    # Revision prefers 09:00 to 13:00; 09:00 to 11:00 is busy, plus a 10 minute buffer.
    assert start == dt(12, "11:15")
    assert find_issues([Slot(start, dt(12, "12:15"), item_id=1)], busy, PREFS) == []


def test_autofill_spreads_a_category_across_days():
    items = [(i, 90, "revision", "", 3) for i in range(1, 5)]
    placed = autofill(items, [], MON, PREFS)
    assert len({s.date() for s in placed.values()}) == 4


def test_autofill_respects_not_before_and_reports_what_does_not_fit():
    placed = autofill([(1, 60, "work", "", 1), (2, 20 * 60, "work", "", 1)], [], MON, PREFS,
                      not_before=dt(16, "12:00"))
    assert placed[1] >= dt(16, "12:00")
    assert 2 not in placed


def test_autofill_orders_by_priority():
    # Only one free hour in the whole week: the P1 item gets it.
    busy = [Slot(datetime(2026, 10, d, 8), datetime(2026, 10, d, 23), title="x") for d in range(13, 19)]
    busy.append(Slot(dt(12, "09:10"), dt(12, "23:00"), title="x"))
    placed = autofill([(1, 60, "work", "", 4), (2, 60, "work", "", 1)], busy, MON, PREFS)
    assert list(placed) == [2]
