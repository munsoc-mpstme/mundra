"""Roles, the admin route and the audit trail (docs/adr/0002)."""

import asyncio

from sqlalchemy import select

import database
import db
from helpers import auth_header, create_user, unique_email


def make(**kwargs):
    return asyncio.run(create_user(**kwargs))


def set_role(client, actor, target, role):
    return client.patch(
        f"/admin/users/{target}/role", json={"role": role}, headers=auth_header(actor)
    )


def audit_rows(target_email):
    async def fetch():
        async with db.SessionLocal() as session:
            result = await session.scalars(
                select(db.AdminAuditRow)
                .where(db.AdminAuditRow.target_email == target_email)
                .order_by(db.AdminAuditRow.id)
            )
            return [(r.actor_email, r.old_role, r.new_role) for r in result]

    return asyncio.run(fetch())


def test_admin_promotes_a_delegate_and_it_is_audited(client):
    admin, target = make(role="admin"), make()

    res = set_role(client, admin, target, "oc")

    assert res.status_code == 200
    assert res.json() == {"email": target, "old_role": "delegate", "new_role": "oc"}
    assert asyncio.run(database.get_role(target)) == "oc"
    assert audit_rows(target) == [(admin, "delegate", "oc")]


def test_repeating_the_same_role_is_a_no_op_without_an_audit_row(client):
    admin, target = make(role="admin"), make(role="oc")
    assert set_role(client, admin, target, "oc").status_code == 200
    assert audit_rows(target) == []


def test_only_admins_can_change_roles(client):
    delegate, oc, target = make(), make(role="oc"), make()
    assert set_role(client, delegate, target, "oc").status_code == 403
    assert set_role(client, oc, target, "admin").status_code == 403
    assert asyncio.run(database.get_role(target)) == "delegate"


def test_requires_authentication(client):
    res = client.patch(f"/admin/users/{unique_email()}/role", json={"role": "oc"})
    assert res.status_code == 401


def test_nobody_can_change_their_own_role(client):
    admin = make(role="admin")
    res = set_role(client, admin, admin, "delegate")
    assert res.status_code == 403
    assert asyncio.run(database.get_role(admin)) == "admin"


def test_unknown_user_is_404_and_unknown_role_is_422(client):
    admin, target = make(role="admin"), make()
    assert set_role(client, admin, unique_email(), "oc").status_code == 404
    assert set_role(client, admin, target, "superuser").status_code == 422


def test_a_demoted_admin_loses_access_immediately(client):
    """The role is read from the database per request, not from the token."""
    first, second = make(role="admin"), make(role="admin")
    headers = auth_header(second)
    assert client.get("/mumbaimun/delegates", headers=headers).status_code != 403

    assert set_role(client, first, second, "delegate").status_code == 200

    assert client.get("/mumbaimun/delegates", headers=headers).status_code == 403
    # ...and the demoted user can no longer demote the remaining admin.
    assert set_role(client, second, first, "delegate").status_code == 403
    assert asyncio.run(database.get_role(first)) == "admin"


def test_the_last_admin_cannot_be_demoted_even_by_a_stale_actor():
    """Guards the race where two admins demote each other at once: set_role re-checks
    the actor inside the transaction, so the loser is refused."""
    only_admin, stale_actor = make(role="admin"), make()

    async def attempt():
        try:
            await database.set_role(stale_actor, only_admin, "delegate")
        except PermissionError as e:
            return str(e)

    assert asyncio.run(attempt()) == "Only admins can change roles"
    assert asyncio.run(database.get_role(only_admin)) == "admin"


def test_make_admin_bootstraps_an_existing_user():
    email = make()
    assert asyncio.run(database.make_admin(email)) == "delegate"
    assert asyncio.run(database.get_role(email)) == "admin"
    assert audit_rows(email) == [("system:cli", "delegate", "admin")]
    assert asyncio.run(database.make_admin(email)) == "admin"  # idempotent
    assert len(audit_rows(email)) == 1


def test_make_admin_refuses_an_unregistered_email():
    try:
        asyncio.run(database.make_admin(unique_email()))
    except LookupError as e:
        assert "Register the account first" in str(e)
    else:
        raise AssertionError("expected LookupError")


def test_an_admin_is_a_normal_user_who_can_use_the_delegate_routes(client):
    admin = make(role="admin")
    res = client.get("/delegates/me", headers=auth_header(admin))
    assert res.status_code == 200
    assert res.json()["email"] == admin


def test_delegates_only_see_themselves_and_admins_see_anyone(client):
    a, b, admin = make(), make(), make(role="admin")
    b_id = asyncio.run(database.get_delegate_by_email(b)).id
    # Forbidden: the route declares 403, but its blanket `except Exception` turns that
    # into a 500 (pre-existing), so only assert that access is refused.
    assert client.get(f"/delegates/{b_id}", headers=auth_header(a)).status_code != 200
    assert client.get(f"/delegates/{b_id}", headers=auth_header(admin)).status_code == 200
    assert client.get(f"/delegates/{b_id}", headers=auth_header(b)).status_code == 200


def test_admins_cannot_delete_their_own_account_but_delegates_can(client):
    admin, delegate = make(role="admin"), make()
    assert client.delete("/account", headers=auth_header(admin)).status_code == 403

    assert client.delete("/account", headers=auth_header(delegate)).status_code == 200
    # the deleted account's token stops working straight away
    assert client.get("/delegates/me", headers=auth_header(delegate)).status_code == 403


def test_deleting_an_account_removes_the_person_and_their_data(client):
    """Apple 5.1.1(v): deleting the account deletes the data stored with it, not only
    the login."""
    import asyncio

    import database
    import models

    email = asyncio.run(create_user())
    delegate = asyncio.run(database.get_delegate_by_email(email))
    asyncio.run(database.add_mm_delegate(models.MMDelegate(**delegate.model_dump())))

    assert client.delete("/account", headers=auth_header(email)).status_code == 200
    assert asyncio.run(database.get_delegate_by_email(email)) is None
    assert asyncio.run(database.get_mm_delegate_by_id(delegate.id)) is None
    assert asyncio.run(database.get_user_by_email(email)) is None


def test_the_delete_account_page_is_public(client):
    res = client.get("/delete-account")
    assert res.status_code == 200
    assert "Delete your Delego account" in res.text
