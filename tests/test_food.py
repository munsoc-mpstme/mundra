"""Food: preference, meal scanning, plate count and the flagged list (docs/adr/0003)."""

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

import app as app_module
import database
import db
import models
import permissions
from helpers import auth_header
from test_teams import add_membership, make, make_event, make_team


IST = timezone(timedelta(hours=5, minutes=30))


def make_dated_event(name="Food Event", starts_at=None, ends_at=None):
    """An event whose date range covers today (unless dates are given), so
    resolve_current_event_day finds it. Module-scoped in tests via the food_event fixture:
    it must be the first event covering today so the resolution is unambiguous (the other
    tests' events are left date-less or dated far from today)."""
    async def go():
        async with db.SessionLocal() as session, session.begin():
            event = db.EventRow(
                name=name,
                starts_at=starts_at or datetime.now(timezone.utc) - timedelta(days=1),
                ends_at=ends_at or datetime.now(timezone.utc) + timedelta(days=1),
            )
            session.add(event)
            await session.flush()
            return event.id

    return asyncio.run(go())


def make_mm(food_preference=None):
    """A verified MM delegate with a login. Returns (email, id)."""
    email = make()

    async def go():
        delegate = await database.get_delegate_by_email(email)
        await database.add_mm_delegate(
            models.MMDelegate(**delegate.model_dump(), food_preference=food_preference)
        )
        return delegate.id

    return email, asyncio.run(go())


def hospitality_member():
    """A user who holds food.manage_entitlement."""
    email = make()
    event = make_event()
    add_membership(
        email,
        event,
        make_team(event, "Hospitality", perms=[permissions.FOOD_MANAGE_ENTITLEMENT]),
    )
    return email


@pytest.fixture(scope="module")
def food_event():
    return make_dated_event()


# --- scanning ---------------------------------------------------------------------


def test_a_scan_serves_once_then_flags_the_second(client, food_event):
    hospi = hospitality_member()
    _, delegate_id = make_mm()

    first = client.post(
        "/food/scans",
        data={"delegate_id": delegate_id, "meal": "lunch"},
        headers=auth_header(hospi),
    )
    assert first.status_code == 200
    assert first.json()["result"] == "served"

    second = client.post(
        "/food/scans",
        data={"delegate_id": delegate_id, "meal": "lunch"},
        headers=auth_header(hospi),
    )
    assert second.status_code == 200
    assert second.json()["result"] == "duplicate"

    flags = client.get("/food/flags", headers=auth_header(hospi)).json()
    assert any(f["delegate_id"] == delegate_id and f["meal"] == "lunch" for f in flags)


def test_the_same_delegate_may_collect_a_different_meal(client, food_event):
    hospi = hospitality_member()
    _, delegate_id = make_mm()

    for meal in ("breakfast", "lunch"):
        res = client.post(
            "/food/scans",
            data={"delegate_id": delegate_id, "meal": meal},
            headers=auth_header(hospi),
        )
        assert res.json()["result"] == "served"


def test_scanning_needs_the_food_permission(client, food_event):
    outsider = make()
    _, delegate_id = make_mm()
    res = client.post(
        "/food/scans",
        data={"delegate_id": delegate_id, "meal": "breakfast"},
        headers=auth_header(outsider),
    )
    assert res.status_code == 403


def test_scanning_a_non_mm_delegate_is_404(client, food_event):
    hospi = hospitality_member()
    plain = make()  # registered, but never registered for Mumbai MUN
    delegate_id = asyncio.run(database.get_delegate_by_email(plain)).id
    res = client.post(
        "/food/scans",
        data={"delegate_id": delegate_id, "meal": "breakfast"},
        headers=auth_header(hospi),
    )
    assert res.status_code == 404


def test_an_admin_can_scan(client, food_event):
    admin = make(role="admin")
    _, delegate_id = make_mm()
    res = client.post(
        "/food/scans",
        data={"delegate_id": delegate_id, "meal": "hitea"},
        headers=auth_header(admin),
    )
    assert res.status_code == 200
    assert res.json()["result"] == "served"


# --- plate count ------------------------------------------------------------------


def test_plate_count_breaks_down_by_diet():
    """Tested at the DB layer against its own event, so the counts are isolated."""
    event = make_dated_event("Count Event")
    veg = make_mm(food_preference="veg")[1]
    jain = make_mm(food_preference="jain")[1]
    plain = make_mm()[1]  # no preference

    for delegate_id in (veg, jain, plain):
        asyncio.run(
            database.record_meal_scan(
                event_id=event, day=1, meal="breakfast", delegate_id=delegate_id, scanned_by="oc@x"
            )
        )

    count = asyncio.run(database.get_plate_count(event, day=1, meal="breakfast"))
    assert (count.total, count.veg, count.jain, count.non_veg, count.unspecified) == (3, 1, 1, 0, 1)


# --- day resolution ---------------------------------------------------------------


def test_resolve_uses_the_event_that_covers_today(food_event):
    event_id, day = asyncio.run(database.resolve_current_event_day())
    assert event_id == food_event
    assert day == 2  # the fixture event started yesterday


def test_resolve_raises_when_no_event_runs_today():
    far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(LookupError):
        asyncio.run(database.resolve_current_event_day(now=far_future))


def test_days_are_counted_in_the_conference_timezone():
    """Dates given in IST: the first IST day is day 1, although it starts on the UTC day
    before. The year is far off so no other test's event covers these dates."""
    event = make_dated_event(
        "IST Event",
        starts_at=datetime(2098, 10, 30, 0, 0, tzinfo=IST),
        ends_at=datetime(2098, 11, 1, 23, 59, 59, tzinfo=IST),
    )

    def resolve(*when):
        return asyncio.run(database.resolve_current_event_day(now=datetime(*when, tzinfo=IST)))

    assert resolve(2098, 10, 30, 0, 5) == (event, 1)  # 18:35 UTC on the 29th
    assert resolve(2098, 10, 30, 8, 0) == (event, 1)
    assert resolve(2098, 11, 1, 0, 30) == (event, 3)  # still 31 Oct in UTC
    assert resolve(2098, 11, 1, 23, 30) == (event, 3)
    for outside in ((2098, 10, 29, 23, 30), (2098, 11, 2, 0, 30)):
        with pytest.raises(LookupError):
            resolve(*outside)


def test_the_fallback_day_key_is_the_local_calendar_date():
    """With no event running, 00:30 IST files under that IST date, not the UTC one."""
    _, day = asyncio.run(database.resolve_scan_day(now=datetime(2097, 5, 2, 0, 30, tzinfo=IST)))
    assert day == date(2097, 5, 2).toordinal()


# --- the phone's scan time (offline scans uploaded later) --------------------------


def test_a_scan_saved_yesterday_counts_for_yesterday(client, food_event):
    """An offline scan uploaded the next day is filed under the day it was made, so it
    does not use up today's meal."""
    hospi = hospitality_member()
    _, delegate_id = make_mm()
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)

    late = client.post(
        "/food/scans",
        data={"delegate_id": delegate_id, "meal": "lunch", "scanned_at": yesterday.isoformat()},
        headers=auth_header(hospi),
    )
    today = client.post(
        "/food/scans",
        data={"delegate_id": delegate_id, "meal": "lunch"},
        headers=auth_header(hospi),
    )

    assert (late.json()["result"], late.json()["day"]) == ("served", 1)
    assert (today.json()["result"], today.json()["day"]) == ("served", 2)


def test_an_unusable_scan_time_falls_back_to_the_server_clock():
    now = datetime.now(timezone.utc)
    parse = app_module._parse_scanned_at

    assert parse(now.isoformat().replace("+00:00", "Z")) is not None  # as the app sends it
    assert parse((now - timedelta(hours=71)).isoformat()) is not None
    for unusable in (
        "",
        "not a date",
        now.replace(tzinfo=None).isoformat(),  # no offset
        (now + timedelta(hours=1)).isoformat(),  # phone clock ahead
        (now - timedelta(hours=73)).isoformat(),  # older than the app keeps scans
    ):
        assert parse(unusable) is None


# --- preference -------------------------------------------------------------------


def test_a_delegate_sets_their_own_preference(client):
    email, delegate_id = make_mm()
    res = client.patch(
        f"/mumbaimun/delegates/{delegate_id}/food_preference",
        json={"food_preference": "jain", "food_notes": "no nuts"},
        headers=auth_header(email),
    )
    assert res.status_code == 200
    assert res.json()["food_preference"] == "jain"
    assert res.json()["food_notes"] == "no nuts"


def test_hospitality_can_override_a_preference(client):
    _, delegate_id = make_mm()
    hospi = hospitality_member()
    res = client.patch(
        f"/mumbaimun/delegates/{delegate_id}/food_preference",
        json={"food_preference": "veg"},
        headers=auth_header(hospi),
    )
    assert res.status_code == 200
    assert res.json()["food_preference"] == "veg"


def test_an_outsider_cannot_set_someone_elses_preference(client):
    _, delegate_id = make_mm()
    outsider = make()
    res = client.patch(
        f"/mumbaimun/delegates/{delegate_id}/food_preference",
        json={"food_preference": "veg"},
        headers=auth_header(outsider),
    )
    assert res.status_code == 403


def test_my_mm_delegate_returns_name_and_preference(client):
    email, delegate_id = make_mm(food_preference="veg")
    res = client.get("/mumbaimun/delegates/me", headers=auth_header(email))
    assert res.status_code == 200
    assert res.json()["id"] == delegate_id
    assert res.json()["food_preference"] == "veg"
