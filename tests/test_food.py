"""Food: preference, meal scanning, plate count and the flagged list (docs/adr/0003)."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import database
import db
import models
import permissions
from helpers import auth_header
from test_teams import add_membership, make, make_event, make_team


def make_dated_event(name="Food Event"):
    """An event whose date range covers today, so resolve_current_event_day finds it.
    Module-scoped in tests via the food_event fixture: it must be the only dated event so
    the resolution is unambiguous (the other tests' events are left date-less)."""
    async def go():
        async with db.SessionLocal() as session, session.begin():
            event = db.EventRow(
                name=name,
                starts_at=datetime.now(timezone.utc) - timedelta(days=1),
                ends_at=datetime.now(timezone.utc) + timedelta(days=1),
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
