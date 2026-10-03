"""OC teams, permissions, memberships and token expiry (docs/adr/0003)."""

import asyncio
from datetime import datetime, timedelta, timezone

import auth
import database
import db
import permissions
from helpers import auth_header, create_user, unique_email


def make(**kwargs):
    return asyncio.run(create_user(**kwargs))


def make_event(name="Test Event", ends_at=None):
    async def go():
        async with db.SessionLocal() as session, session.begin():
            event = db.EventRow(name=name, ends_at=ends_at)
            session.add(event)
            await session.flush()
            return event.id

    return asyncio.run(go())


def make_team(event_id, name, perms=()):
    async def go():
        async with db.SessionLocal() as session, session.begin():
            team = db.TeamRow(event_id=event_id, name=name)
            team.permissions = [db.TeamPermissionRow(permission=p) for p in perms]
            session.add(team)
            await session.flush()
            return team.id

    return asyncio.run(go())


def add_membership(email, event_id, team_id, committee=None, level="member", ends_at=None):
    async def go():
        async with db.SessionLocal() as session, session.begin():
            session.add(
                db.MembershipRow(
                    event_id=event_id,
                    user_email=email,
                    team_id=team_id,
                    committee=committee,
                    level=level,
                    ends_at=ends_at,
                )
            )

    return asyncio.run(go())


def add_head(email, event_id, granted_by="system:test"):
    async def go():
        async with db.SessionLocal() as session, session.begin():
            session.add(
                db.EventHeadRow(event_id=event_id, user_email=email, granted_by=granted_by)
            )

    return asyncio.run(go())


def add_invite(email, event_id, team_id, committee=None, level="member"):
    async def go():
        async with db.SessionLocal() as session, session.begin():
            session.add(
                db.TeamInviteRow(
                    email=email,
                    event_id=event_id,
                    team_id=team_id,
                    committee=committee,
                    level=level,
                )
            )

    return asyncio.run(go())


# --- effective access -------------------------------------------------------------


def test_a_members_permissions_come_from_their_team():
    email = make()
    event = make_event()
    team = make_team(event, "Hospitality", perms=[permissions.FOOD_MANAGE_ENTITLEMENT])
    add_membership(email, event, team)

    is_head, perms, memberships = asyncio.run(database.get_effective_access(email))

    assert is_head is False
    assert perms == {permissions.FOOD_MANAGE_ENTITLEMENT}
    assert [m.team for m in memberships] == ["Hospitality"]
    assert memberships[0].permissions == [permissions.FOOD_MANAGE_ENTITLEMENT]


def test_a_head_holds_every_permission_without_a_membership():
    email = make()
    event = make_event()
    add_head(email, event)

    is_head, perms, memberships = asyncio.run(database.get_effective_access(email))

    assert is_head is True
    assert perms == set(permissions.ALL_PERMISSIONS)
    assert memberships == []


def test_a_lapsed_membership_grants_nothing():
    email = make()
    event = make_event()
    team = make_team(event, "Hospitality", perms=[permissions.FOOD_MANAGE_ENTITLEMENT])
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    add_membership(email, event, team, ends_at=yesterday)

    _, perms, memberships = asyncio.run(database.get_effective_access(email))

    assert perms == set()
    assert memberships == []


def test_permissions_union_across_two_teams():
    email = make()
    event = make_event()
    add_membership(email, event, make_team(event, "A", perms=[permissions.CHAT_POST]))
    add_membership(email, event, make_team(event, "B", perms=[permissions.CHAT_VIEW]))

    _, perms, _ = asyncio.run(database.get_effective_access(email))

    assert perms == {permissions.CHAT_POST, permissions.CHAT_VIEW}


# --- roster invites ---------------------------------------------------------------


def test_a_pending_invite_becomes_a_membership_on_verification():
    email = make()
    event = make_event()
    team = make_team(event, "Rapporteur", perms=[permissions.CHAT_POST])
    add_invite(email, event, team, committee="UNSC")

    applied = asyncio.run(database.apply_pending_invites(email))

    assert applied == 1
    _, perms, memberships = asyncio.run(database.get_effective_access(email))
    assert perms == {permissions.CHAT_POST}
    assert memberships[0].committee == "UNSC"
    # Idempotent: applying again does nothing and creates no duplicate.
    assert asyncio.run(database.apply_pending_invites(email)) == 0


def test_an_invite_ends_at_follows_the_event_end():
    email = make()
    end = datetime(2026, 1, 1, tzinfo=timezone.utc)  # already past
    event = make_event(ends_at=end)
    team = make_team(event, "Hospitality", perms=[permissions.FOOD_MANAGE_ENTITLEMENT])
    add_invite(email, event, team)

    asyncio.run(database.apply_pending_invites(email))

    # The membership exists but has already lapsed, so it grants nothing.
    _, perms, memberships = asyncio.run(database.get_effective_access(email))
    assert perms == set()
    assert memberships == []


# --- route gating -----------------------------------------------------------------


def test_manual_verify_requires_oc_standing(client):
    outsider, oc = make(), make()
    event = make_event()
    add_membership(oc, event, make_team(event, "Registration", perms=[]))
    target = make(verified=False)

    assert (
        client.post(f"/manual_verify?email={target}", headers=auth_header(outsider)).status_code
        == 403
    )
    assert (
        client.post(f"/manual_verify?email={target}", headers=auth_header(oc)).status_code == 201
    )
    assert asyncio.run(database.get_delegate_by_email(target)).verified is True


def test_manual_verify_is_no_longer_open_to_the_world(client):
    assert client.post(f"/manual_verify?email={unique_email()}").status_code == 401


# --- /delegates/me ----------------------------------------------------------------


def test_me_exposes_teams_and_permissions(client):
    email = make()
    event = make_event()
    team = make_team(event, "Hospitality", perms=[permissions.FOOD_MANAGE_ENTITLEMENT])
    add_membership(email, event, team, level="lead")

    res = client.get("/delegates/me", headers=auth_header(email))

    assert res.status_code == 200
    body = res.json()
    assert body["is_head"] is False
    # The team's own permission, plus the strings the Delego app gates screens on: this
    # delegate-role member gets the delegate screens and, from the team, the scanner.
    assert body["permissions"] == sorted(
        {permissions.FOOD_MANAGE_ENTITLEMENT, permissions.APP_FOOD_SCAN}
        | permissions.APP_ROLE_PERMISSIONS["delegate"]
    )
    assert body["teams"] == [
        {
            "team": "Hospitality",
            "committee": None,
            "level": "lead",
            "permissions": [permissions.FOOD_MANAGE_ENTITLEMENT],
        }
    ]


def test_me_still_works_for_a_plain_delegate(client):
    email = make()
    res = client.get("/delegates/me", headers=auth_header(email))
    assert res.status_code == 200
    body = res.json()
    assert body["email"] == email
    assert body["is_head"] is False
    # No team permissions; only the app's delegate screens (study guides, QR badge).
    assert body["permissions"] == sorted(permissions.APP_ROLE_PERMISSIONS["delegate"])
    assert body["teams"] == []


# --- token expiry -----------------------------------------------------------------


def test_an_expired_token_is_rejected_with_401(client):
    email = make()
    expired = auth.create_access_token({"sub": email}, timedelta(minutes=-1))
    res = client.get("/delegates/me", headers={"Authorization": f"Bearer {expired}"})
    assert res.status_code == 401
    assert res.json()["detail"] == "Session expired, please log in again"


def test_a_token_without_an_exp_still_works(client):
    """helpers.auth_header mints tokens with no exp; those must keep working."""
    email = make()
    assert client.get("/delegates/me", headers=auth_header(email)).status_code == 200
