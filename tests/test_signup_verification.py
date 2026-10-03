"""Signing up must not let anyone in until the emailed code is entered."""

import asyncio
from datetime import datetime, timedelta, timezone

import database
import mails
from helpers import auth_header, create_user, unique_email

PASSWORD = "Sup3r-secret-pw"


def sign_up(client, email=None):
    email = email or unique_email()
    res = client.post(
        "/mumbaimun/register",
        json={"firstname": "New", "lastname": "Person", "email": email, "password": PASSWORD},
    )
    return email, res


def login(client, email, password=PASSWORD):
    return client.post("/login", data={"username": email, "password": password})


def set_code(email, code="123456"):
    expires = datetime.now(timezone.utc) + timedelta(minutes=15)
    asyncio.run(database.set_verification_code(email, code, expires))


def verified(email):
    return asyncio.run(database.get_delegate_by_email(email)).verified


def test_sign_up_creates_an_unverified_account_and_sends_a_code(client, monkeypatch):
    sent = []

    async def fake_send(delegate, code):
        sent.append((delegate.email, code))

    monkeypatch.setattr(mails, "send_verification_email", fake_send)

    email, res = sign_up(client)

    assert res.status_code == 201, res.text
    assert res.json()["verified"] is False and res.json()["email_sent"] is True
    assert verified(email) is False
    assert [e for e, _ in sent] == [email] and len(sent[0][1]) == 6


def test_cannot_log_in_before_verifying_and_no_token_is_issued(client, monkeypatch):
    async def fake_send(delegate, code):
        pass

    monkeypatch.setattr(mails, "send_verification_email", fake_send)
    email, _ = sign_up(client)

    res = login(client, email)

    assert res.status_code == 403
    assert res.json()["detail"] == "Please verify your email!"
    assert "access_token" not in res.json()


def test_wrong_password_is_still_a_plain_401_not_a_hint_about_verification(client, monkeypatch):
    async def fake_send(delegate, code):
        pass

    monkeypatch.setattr(mails, "send_verification_email", fake_send)
    email, _ = sign_up(client)
    assert login(client, email, "not-the-password").status_code == 401


def test_entering_the_code_verifies_and_then_login_works(client, monkeypatch):
    async def fake_send(delegate, code):
        pass

    monkeypatch.setattr(mails, "send_verification_email", fake_send)
    email, _ = sign_up(client)
    set_code(email, "654321")

    wrong = client.post("/verify_email", json={"email": email, "code": "000000"})
    assert wrong.status_code == 400 and verified(email) is False
    assert login(client, email).status_code == 403

    ok = client.post("/verify_email", json={"email": email, "code": "654321"})
    assert ok.status_code == 200 and verified(email) is True

    res = login(client, email)
    assert res.status_code == 200 and res.json()["access_token"]
    assert client.get("/delegates/me", headers=auth_header(email)).status_code == 200


def test_signing_up_again_does_not_verify_the_account(client, monkeypatch):
    async def fake_send(delegate, code):
        pass

    monkeypatch.setattr(mails, "send_verification_email", fake_send)
    email, _ = sign_up(client)

    again = client.post(
        "/mumbaimun/register",
        json={"firstname": "x", "lastname": "y", "email": email, "password": "another-pass-1"},
    )

    assert again.status_code == 409  # already registered for Mumbai MUN
    assert verified(email) is False
    assert login(client, email).status_code == 403


def test_an_existing_unverified_account_is_not_verified_by_registering_for_mun(client, monkeypatch):
    async def fake_send(delegate, code):
        pass

    monkeypatch.setattr(mails, "send_verification_email", fake_send)
    email = asyncio.run(create_user(verified=False))  # signed up the general way, not verified

    res = client.post(
        "/mumbaimun/register",
        json={"firstname": "x", "lastname": "y", "email": email, "password": PASSWORD},
    )

    assert res.status_code == 201
    assert res.json()["verified"] is False
    assert verified(email) is False


def test_resend_gives_a_fresh_code(client, monkeypatch):
    sent = []

    async def fake_send(delegate, code):
        sent.append(code)

    monkeypatch.setattr(mails, "send_verification_email", fake_send)
    email, _ = sign_up(client)

    res = client.get("/resend_verification", params={"email": email})

    assert res.status_code == 200
    assert len(sent) == 2  # one at sign-up, one on resend
    assert client.post(
        "/verify_email", json={"email": email, "code": sent[-1]}
    ).status_code == 200


def test_if_the_email_cannot_be_sent_the_account_is_still_created(client, monkeypatch):
    async def broken(delegate, code):
        raise RuntimeError("mail server down")

    monkeypatch.setattr(mails, "send_verification_email", broken)

    email, res = sign_up(client)

    assert res.status_code == 201
    assert res.json()["email_sent"] is False and res.json()["verified"] is False
    assert verified(email) is False
    assert login(client, email).status_code == 403  # they can use "Resend code" in the app


def test_verified_accounts_and_admins_still_log_in(client):
    email = asyncio.run(create_user(password=PASSWORD))  # verified by default
    assert login(client, email).status_code == 200
