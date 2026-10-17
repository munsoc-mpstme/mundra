"""OC administration: event dates, team CRUD, rosters and heads (docs/adr/0003)."""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

import database
import db
import models
import permissions
from helpers import auth_header, create_user, unique_email
from test_teams import add_head, add_membership, make, make_event, make_team


def audit_rows(target_email):
    async def fetch():
        async with db.SessionLocal() as session:
            rows = await session.scalars(
                select(db.MembershipAuditRow)
                .where(db.MembershipAuditRow.target_email == target_email)
                .order_by(db.MembershipAuditRow.id)
            )
            return [(r.actor_email, r.team_name, r.action) for r in rows]

    return asyncio.run(fetch())


# --- event dates ------------------------------------------------------------------


def test_a_head_sets_event_dates(client):
    head = make()
    event = make_event()
    add_head(head, event)

    res = client.patch(
        f"/events/{event}",
        json={"starts_at": "2026-11-01T00:00:00Z", "ends_at": "2026-11-03T00:00:00Z"},
        headers=auth_header(head),
    )
    assert res.status_code == 200
    assert res.json()["starts_at"].startswith("2026-11-01")


def test_event_dates_need_an_offset_and_the_right_order(client):
    head = make()
    event = make_event()
    add_head(head, event)

    def patch(starts_at, ends_at):
        return client.patch(
            f"/events/{event}",
            json={"starts_at": starts_at, "ends_at": ends_at},
            headers=auth_header(head),
        )

    assert patch("2098-11-01T00:00:00", "2098-11-03T00:00:00").status_code == 422
    assert patch("2098-11-03T00:00:00+05:30", "2098-11-01T00:00:00+05:30").status_code == 422
    assert patch("2098-11-01T00:00:00+05:30", "2098-11-03T00:00:00+05:30").status_code == 200


def membership_ends_at(email, event_id):
    async def fetch():
        async with db.SessionLocal() as session:
            return await session.scalar(
                select(db.MembershipRow.ends_at).where(
                    db.MembershipRow.user_email == email,
                    db.MembershipRow.event_id == event_id,
                )
            )

    return asyncio.run(fetch())


IST = timezone(timedelta(hours=5, minutes=30))


def test_setting_event_dates_moves_team_access_to_the_end_of_the_last_day(client):
    """A member added before the dates were set had no expiry. Setting the dates gives
    them one: the midnight (IST) that closes the event's last day."""
    head, member = make(), make()
    event = make_event()
    add_head(head, event)
    add_membership(
        member, event, make_team(event, "Hospitality", perms=[permissions.FOOD_MANAGE_ENTITLEMENT])
    )

    def set_dates(starts_at, ends_at):
        res = client.patch(
            f"/events/{event}",
            json={"starts_at": starts_at, "ends_at": ends_at},
            headers=auth_header(head),
        )
        assert res.status_code == 200

    def perms():
        return asyncio.run(database.get_effective_access(member))[1]

    assert membership_ends_at(member, event) is None

    # The end is given as the start of the last day; access still covers that whole day.
    set_dates("2098-10-30T00:00:00+05:30", "2098-11-01T00:00:00+05:30")
    assert membership_ends_at(member, event) == datetime(2098, 11, 2, tzinfo=IST)
    assert perms() == {permissions.FOOD_MANAGE_ENTITLEMENT}

    # Dates in the past lapse the access; correcting them brings it back.
    set_dates("2025-12-30T00:00:00+05:30", "2026-01-01T23:59:59+05:30")
    assert perms() == set()
    set_dates("2098-10-30T00:00:00+05:30", "2098-11-01T23:59:59+05:30")
    assert perms() == {permissions.FOOD_MANAGE_ENTITLEMENT}


def test_a_new_member_keeps_access_through_the_events_last_day():
    lead, member = make(), make()
    event = make_event(ends_at=datetime(2098, 11, 1, 9, 0, tzinfo=IST))
    team = make_team(event, "Hospitality", perms=[permissions.FOOD_MANAGE_ENTITLEMENT])

    asyncio.run(database.add_to_roster(lead, team, models.RosterAdd(email=member)))

    assert membership_ends_at(member, event) == datetime(2098, 11, 2, tzinfo=IST)


def test_a_plain_member_cannot_set_event_dates(client):
    member = make()
    event = make_event()
    add_membership(member, event, make_team(event, "T", perms=[]))
    res = client.patch(
        f"/events/{event}",
        json={"starts_at": "2026-11-01T00:00:00Z", "ends_at": "2026-11-03T00:00:00Z"},
        headers=auth_header(member),
    )
    assert res.status_code == 403


# --- team CRUD --------------------------------------------------------------------


def test_a_head_creates_a_team_with_permissions(client):
    head = make()
    event = make_event()
    add_head(head, event)

    res = client.post(
        f"/events/{event}/teams",
        json={"name": "Hospitality", "permissions": [permissions.FOOD_MANAGE_ENTITLEMENT]},
        headers=auth_header(head),
    )
    assert res.status_code == 201
    assert res.json()["permissions"] == [permissions.FOOD_MANAGE_ENTITLEMENT]


def test_creating_a_team_rejects_an_unknown_permission(client):
    admin = make(role="admin")
    event = make_event()
    res = client.post(
        f"/events/{event}/teams",
        json={"name": "Bad", "permissions": ["food.eat_everything"]},
        headers=auth_header(admin),
    )
    assert res.status_code == 422


def test_duplicate_team_name_in_an_event_is_409(client):
    admin = make(role="admin")
    event = make_event()
    body = {"name": "Security", "permissions": []}
    assert client.post(f"/events/{event}/teams", json=body, headers=auth_header(admin)).status_code == 201
    assert client.post(f"/events/{event}/teams", json=body, headers=auth_header(admin)).status_code == 409


def test_a_plain_member_cannot_create_a_team(client):
    member = make()
    event = make_event()
    add_membership(member, event, make_team(event, "T", perms=[]))
    res = client.post(
        f"/events/{event}/teams",
        json={"name": "Nope", "permissions": []},
        headers=auth_header(member),
    )
    assert res.status_code == 403


def test_team_permissions_can_be_replaced(client):
    admin = make(role="admin")
    event = make_event()
    team = make_team(event, "Rapporteur", perms=[permissions.CHAT_POST])

    res = client.patch(
        f"/teams/{team}/permissions",
        json={"permissions": [permissions.CHAT_POST, permissions.CHAT_VIEW]},
        headers=auth_header(admin),
    )
    assert res.status_code == 200
    assert sorted(res.json()["permissions"]) == sorted(
        [permissions.CHAT_POST, permissions.CHAT_VIEW]
    )


# --- rosters ----------------------------------------------------------------------


def test_a_lead_adds_a_registered_member_and_it_is_audited(client):
    lead = make()
    event = make_event()
    team = make_team(event, "Hospitality", perms=[permissions.FOOD_MANAGE_ENTITLEMENT])
    add_membership(lead, event, team, level="lead")
    newbie = make()

    res = client.post(
        f"/teams/{team}/members",
        json={"email": newbie},
        headers=auth_header(lead),
    )
    assert res.status_code == 201
    assert res.json()["status"] == "member"
    # the new member now holds the team's permission
    _, perms, _ = asyncio.run(database.get_effective_access(newbie))
    assert permissions.FOOD_MANAGE_ENTITLEMENT in perms
    assert (lead, "Hospitality", "grant") in audit_rows(newbie)


def test_adding_an_unregistered_email_creates_an_invite(client):
    admin = make(role="admin")
    event = make_event()
    team = make_team(event, "Hospitality", perms=[permissions.FOOD_MANAGE_ENTITLEMENT])
    email = unique_email()

    res = client.post(f"/teams/{team}/members", json={"email": email}, headers=auth_header(admin))
    assert res.status_code == 201
    assert res.json()["status"] == "invited"

    # once that email registers and verifies, the invite becomes a membership
    asyncio.run(create_user(email))
    assert asyncio.run(database.apply_pending_invites(email)) == 1
    _, perms, _ = asyncio.run(database.get_effective_access(email))
    assert permissions.FOOD_MANAGE_ENTITLEMENT in perms


def test_a_non_lead_member_cannot_edit_the_roster(client):
    member = make()
    event = make_event()
    team = make_team(event, "Hospitality", perms=[permissions.FOOD_MANAGE_ENTITLEMENT])
    add_membership(member, event, team, level="member")

    res = client.post(
        f"/teams/{team}/members", json={"email": unique_email()}, headers=auth_header(member)
    )
    assert res.status_code == 403


def test_removing_a_member_revokes_access(client):
    lead, target = make(), make()
    event = make_event()
    team = make_team(event, "Hospitality", perms=[permissions.FOOD_MANAGE_ENTITLEMENT])
    add_membership(lead, event, team, level="lead")
    add_membership(target, event, team)

    res = client.delete(f"/teams/{team}/members/{target}", headers=auth_header(lead))
    assert res.status_code == 200
    _, perms, _ = asyncio.run(database.get_effective_access(target))
    assert perms == set()
    assert (lead, "Hospitality", "revoke") in audit_rows(target)


def test_roster_lists_members_and_invites(client):
    admin = make(role="admin")
    event = make_event()
    team = make_team(event, "Hospitality", perms=[])
    member = make()
    add_membership(member, event, team)
    invited = unique_email()
    client.post(f"/teams/{team}/members", json={"email": invited}, headers=auth_header(admin))

    rows = client.get(f"/teams/{team}/members", headers=auth_header(admin)).json()
    by_email = {r["email"]: r["status"] for r in rows}
    assert by_email.get(member) == "member"
    assert by_email.get(invited) == "invited"


# --- heads ------------------------------------------------------------------------


def test_only_an_admin_grants_a_head(client):
    admin, head, target = make(role="admin"), make(), make()
    event = make_event()
    add_head(head, event)  # an existing head...

    # ...cannot grant another head
    assert (
        client.post(f"/events/{event}/heads", json={"email": target}, headers=auth_header(head)).status_code
        == 403
    )
    # an admin can
    res = client.post(f"/events/{event}/heads", json={"email": target}, headers=auth_header(admin))
    assert res.status_code == 201
    is_head, perms, _ = asyncio.run(database.get_effective_access(target))
    assert is_head and perms == set(permissions.ALL_PERMISSIONS)


def test_granting_a_head_twice_is_409(client):
    admin, target = make(role="admin"), make()
    event = make_event()
    body = {"email": target}
    assert client.post(f"/events/{event}/heads", json=body, headers=auth_header(admin)).status_code == 201
    assert client.post(f"/events/{event}/heads", json=body, headers=auth_header(admin)).status_code == 409


def test_granting_a_head_to_an_unknown_user_is_404(client):
    admin = make(role="admin")
    event = make_event()
    res = client.post(
        f"/events/{event}/heads", json={"email": unique_email()}, headers=auth_header(admin)
    )
    assert res.status_code == 404


def test_a_head_can_be_removed(client):
    admin, target = make(role="admin"), make()
    event = make_event()
    add_head(target, event)
    assert client.delete(f"/events/{event}/heads/{target}", headers=auth_header(admin)).status_code == 200
    is_head, _, _ = asyncio.run(database.get_effective_access(target))
    assert is_head is False
