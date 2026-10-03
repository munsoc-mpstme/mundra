"""ADMIN_EMAIL / ADMIN_PASSWORD: the first admin without a shell on the host."""

import asyncio

import app as app_module
import auth
import database
from helpers import auth_header, create_user, unique_email

PASSWORD = "Bootstrap-pass-9"


def run(coro):
    return asyncio.run(coro)


def role(email):
    return run(database.get_role(email))


def delegate(email):
    return run(database.get_delegate_by_email(email))


def test_creates_a_verified_admin_who_can_log_in(client):
    email = unique_email()

    outcome = run(database.ensure_bootstrap_admin(email, auth.hash_password(PASSWORD)))

    assert outcome == "created"
    assert role(email) == "admin" and delegate(email).verified is True
    res = client.post("/login", data={"username": email, "password": PASSWORD})
    assert res.status_code == 200 and res.json()["user_type"] == "admin"
    assert client.get("/delegates/me", headers=auth_header(email)).json()["role"] == "admin"


def test_promotes_an_existing_account_without_touching_its_password(client):
    email = run(create_user(password="My-own-password-1", verified=False))
    assert role(email) == "delegate"

    # No password given: just promote, and verify so they can log in.
    assert run(database.ensure_bootstrap_admin(email)) == "promoted"
    assert role(email) == "admin" and delegate(email).verified is True

    # Even with a password supplied, an existing account keeps the one it has.
    run(database.ensure_bootstrap_admin(email, auth.hash_password("Something-else-77")))
    assert client.post(
        "/login", data={"username": email, "password": "My-own-password-1"}
    ).status_code == 200
    assert client.post(
        "/login", data={"username": email, "password": "Something-else-77"}
    ).status_code == 401


def test_running_it_again_changes_nothing(client):
    email = unique_email()
    run(database.ensure_bootstrap_admin(email, auth.hash_password(PASSWORD)))
    assert run(database.ensure_bootstrap_admin(email, auth.hash_password(PASSWORD))) == "unchanged"


def test_an_unknown_email_without_a_password_is_left_alone(client):
    email = unique_email()
    assert run(database.ensure_bootstrap_admin(email)) == "missing"
    assert delegate(email) is None


def test_the_change_is_audited(client):
    from sqlalchemy import select

    import db

    email = run(create_user())
    run(database.ensure_bootstrap_admin(email))

    async def rows():
        async with db.SessionLocal() as session:
            result = await session.scalars(
                select(db.AdminAuditRow).where(db.AdminAuditRow.target_email == email)
            )
            return [(r.actor_email, r.old_role, r.new_role) for r in result]

    assert run(rows()) == [("system:startup", "delegate", "admin")]


def test_startup_hook_uses_the_settings_and_never_blocks_startup(client, monkeypatch, caplog):
    email = unique_email()
    monkeypatch.setattr(app_module.settings, "admin_email", email)
    monkeypatch.setattr(app_module.settings, "admin_password", PASSWORD)
    run(app_module.bootstrap_admin())
    assert role(email) == "admin"
    assert PASSWORD not in caplog.text  # the password is never logged

    # A too-short password is ignored with an error, and nothing is created.
    other = unique_email()
    monkeypatch.setattr(app_module.settings, "admin_email", other)
    monkeypatch.setattr(app_module.settings, "admin_password", "short")
    run(app_module.bootstrap_admin())
    assert delegate(other) is None

    # A broken database call must not raise out of the hook.
    async def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(app_module.database, "ensure_bootstrap_admin", boom)
    run(app_module.bootstrap_admin())

    # Nothing set -> does nothing.
    monkeypatch.setattr(app_module.settings, "admin_email", None)
    run(app_module.bootstrap_admin())
