"""Food: preference, meal scanning, plate count and the flagged list (docs/adr/0003)."""

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest

import app as app_module
import config
import database
import db
import models
import permissions
import utils
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
    """An OC-role user on the food team (holds food.manage_entitlement): the combination
    that may scan."""
    email = make(role="oc")
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


# --- the badge QR: id and diet in the code, day and meal from the clock -----------


def test_the_badge_code_carries_the_id_and_diet(client):
    email, delegate_id = make_mm(food_preference="jain")
    res = client.get("/mumbaimun/badge", headers=auth_header(email))
    assert res.status_code == 200
    assert res.json()["qr"] == f"DELEGO1:{delegate_id}:J"
    assert utils.parse_badge(res.json()["qr"]) == (delegate_id, "jain")

    png = client.get("/mumbaimun/badge/qr", headers=auth_header(email))
    assert png.status_code == 200
    assert png.headers["content-type"] == "image/png"
    assert png.content.startswith(b"\x89PNG")


def test_a_badge_with_no_diet_and_an_old_plain_id_both_parse():
    assert utils.badge_payload("abc", None) == "DELEGO1:abc:-"
    assert utils.parse_badge("DELEGO1:abc:-") == ("abc", None)
    assert utils.parse_badge(" abc ") == ("abc", None)


def test_the_meal_comes_from_the_local_time():
    on = lambda h, m: datetime(2026, 10, 30, h, m, tzinfo=IST)  # noqa: E731
    assert database.current_meal(on(8, 30)) == "breakfast"
    assert database.current_meal(on(10, 59)) == "breakfast"
    assert database.current_meal(on(11, 0)) == "lunch"
    assert database.current_meal(on(15, 29)) == "lunch"
    assert database.current_meal(on(15, 30)) == "hitea"
    # 06:00 UTC is 11:30 IST: lunch, not breakfast
    assert database.current_meal(datetime(2026, 10, 30, 6, 0, tzinfo=timezone.utc)) == "lunch"


def test_a_scan_with_no_meal_uses_the_current_one(client, food_event):
    hospi = hospitality_member()
    _, delegate_id = make_mm(food_preference="veg")
    res = client.post(
        "/food/scans",
        data={"delegate_id": f"DELEGO1:{delegate_id}:V"},
        headers=auth_header(hospi),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["result"] == "served"
    assert body["delegate_id"] == delegate_id
    assert body["meal"] == database.current_meal()
    assert body["diet"] == "veg"


def test_the_context_tells_the_scanner_the_day_and_meal(client, food_event):
    hospi = hospitality_member()
    res = client.get("/food/context", headers=auth_header(hospi))
    assert res.status_code == 200
    body = res.json()
    assert body["event_dates_set"] is True
    assert body["event_id"] == food_event
    assert body["day"] == 2  # the fixture's event started yesterday
    assert body["meal"] == database.current_meal()
    assert body["meal_windows"]["lunch"] == config.get_settings().meal_lunch_from
    assert len(body["event_dates"]) == 3


def test_the_context_needs_the_food_permission(client, food_event):
    assert client.get("/food/context", headers=auth_header(make())).status_code == 403


def test_a_lookup_shows_diet_and_what_was_collected_without_serving(client, food_event):
    hospi = hospitality_member()
    _, delegate_id = make_mm(food_preference="non_veg")
    code = f"DELEGO1:{delegate_id}:N"

    before = client.get(
        "/food/lookup", params={"code": code, "meal": "lunch"}, headers=auth_header(hospi)
    ).json()
    assert before["food_preference"] == "non_veg"
    assert before["already_collected"] is False
    assert before["collected_today"] == []

    client.post(
        "/food/scans", data={"delegate_id": code, "meal": "lunch"}, headers=auth_header(hospi)
    )
    after = client.get(
        "/food/lookup", params={"code": code, "meal": "lunch"}, headers=auth_header(hospi)
    ).json()
    assert after["already_collected"] is True
    assert after["collected_today"] == ["lunch"]


def test_a_lookup_of_an_unknown_badge_is_404(client, food_event):
    res = client.get(
        "/food/lookup", params={"code": "DELEGO1:nope:V"}, headers=auth_header(hospitality_member())
    )
    assert res.status_code == 404


# --- name search, for a delegate with no phone ---------------------------------------


async def rename(delegate_id, firstname, lastname):
    async with db.SessionLocal() as session, session.begin():
        row = await session.get(db.DelegateRow, delegate_id)
        row.firstname, row.lastname = firstname, lastname


def test_hospitality_can_find_a_delegate_by_name(client):
    hospi = hospitality_member()
    email, delegate_id = make_mm(food_preference="veg")
    asyncio.run(rename(delegate_id, "Zubeida", "Qureshi"))
    res = client.get("/food/delegates", params={"q": "zube qur"}, headers=auth_header(hospi))
    assert res.status_code == 200
    match = next(m for m in res.json() if m["delegate_id"] == delegate_id)
    assert match["name"] == "Zubeida Qureshi"
    assert match["food_preference"] == "veg"
    assert match["email_hint"] == email[:2] + "***@example.com"


def test_name_search_needs_the_food_permission_and_two_letters(client):
    assert client.get("/food/delegates", params={"q": "ab"}, headers=auth_header(make())).status_code == 403
    hospi = hospitality_member()
    assert client.get("/food/delegates", params={"q": "a"}, headers=auth_header(hospi)).status_code == 422


def test_name_search_treats_wildcards_literally(client):
    hospi = hospitality_member()
    res = client.get("/food/delegates", params={"q": "%%"}, headers=auth_header(hospi))
    assert res.status_code == 200 and res.json() == []


# --- the admin's Food page (today's plates and who scanned them) ----------------------


def test_food_today_counts_every_meal_by_diet_and_by_scanner():
    """At the DB layer against its own event, so the counts are isolated."""
    event = make_dated_event("Food Page Event")
    riya, aman = make(), make()  # scanners with accounts
    veg = make_mm(food_preference="veg")[1]
    jain = make_mm(food_preference="jain")[1]
    plain = make_mm()[1]

    def scan(delegate_id, meal, by, diet=None, day=1):
        asyncio.run(database.record_meal_scan(
            event_id=event, day=day, meal=meal, delegate_id=delegate_id,
            scanned_by=by, diet=diet,
        ))

    scan(veg, "lunch", riya, "veg")
    scan(jain, "lunch", riya, "jain")
    scan(plain, "lunch", aman, "veg")  # the scanner's pick, not the (empty) registration
    scan(veg, "hitea", riya)  # no diet sent: the registered veg counts
    scan(veg, "lunch", riya, "veg")  # a second lunch: refused, not counted
    scan(jain, "lunch", "gone@x", "jain", day=2)  # another day: not counted
    scan(jain, "hitea", "gone@x", "jain")  # a scanner with no account

    today = date(2026, 10, 31)
    page = asyncio.run(database.get_food_today(event, 1, today))
    assert page.date == today
    assert (page.total, page.veg, page.jain, page.non_veg, page.unspecified) == (5, 3, 2, 0, 0)
    rows = [(s.email, s.count) for s in page.scanners]
    assert rows == [(riya, 3), (aman, 1), ("gone@x", 1)]
    assert page.scanners[0].name != riya  # a real name, not the email
    assert page.scanners[2].name == "gone@x"

    plates = asyncio.run(database.get_plates_scanned_by(event, 1, riya))
    assert [(p.delegate_id, p.meal, p.diet) for p in plates] == [
        (veg, "hitea", "veg"), (jain, "lunch", "jain"), (veg, "lunch", "veg"),
    ]
    assert all(p.name for p in plates)
    assert asyncio.run(database.get_plates_scanned_by(event, 1, "nobody@x")) == []


def test_an_empty_day_is_all_zeros():
    event = make_dated_event("Quiet Event")
    page = asyncio.run(database.get_food_today(event, 1, date(2026, 10, 30)))
    assert (page.total, page.veg, page.jain, page.scanners) == (0, 0, 0, [])


def test_the_food_page_is_for_admins_only(client, food_event):
    _, delegate_id = make_mm(food_preference="veg")
    hospi = hospitality_member()
    client.post(
        "/food/scans",
        data={"delegate_id": delegate_id, "meal": "lunch", "diet": "veg"},
        headers=auth_header(hospi),
    )
    for who in (make(), make(role="oc"), make(role="eb"), hospi):
        assert client.get("/food/today", headers=auth_header(who)).status_code == 403
        assert client.get(
            "/food/today/scans", params={"scanner": hospi}, headers=auth_header(who)
        ).status_code == 403
    assert client.get("/food/today").status_code == 401

    admin = make(role="admin")
    page = client.get("/food/today", headers=auth_header(admin))
    assert page.status_code == 200
    body = page.json()
    assert body["date"] == datetime.now(IST).date().isoformat()
    assert any(s["email"] == hospi and s["count"] >= 1 for s in body["scanners"])

    plates = client.get(
        "/food/today/scans", params={"scanner": hospi}, headers=auth_header(admin)
    ).json()
    assert any(p["delegate_id"] == delegate_id and p["meal"] == "lunch" for p in plates)
