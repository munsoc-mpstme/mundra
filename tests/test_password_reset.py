"""Password-reset tokens: short-lived, single-purpose, single-use."""

import asyncio
from datetime import timedelta

import auth
import database
from helpers import auth_header, create_user


def make():
    email = asyncio.run(create_user())
    user = asyncio.run(database.get_user_by_email(email))
    return email, user.password


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_reset_token_sets_a_new_password_once(client):
    email, pwd = make()
    token = auth.create_reset_token(email, pwd)
    res = client.post("/reset_password", json={"password": "brand-new-pass"}, headers=bearer(token))
    assert res.status_code == 200
    login = client.post("/login", data={"username": email, "password": "brand-new-pass"})
    assert login.status_code == 200
    # the same link cannot be used again
    again = client.post("/reset_password", json={"password": "another-pass-1"}, headers=bearer(token))
    assert again.status_code == 400
    assert client.get("/reset", params={"token": token}).status_code == 400


def test_reset_token_is_not_a_login_token(client):
    email, pwd = make()
    token = auth.create_reset_token(email, pwd)
    assert client.get("/delegates/me", headers=bearer(token)).status_code == 403


def test_login_token_cannot_reset_a_password(client):
    email, _ = make()
    res = client.post(
        "/reset_password", json={"password": "brand-new-pass"}, headers=auth_header(email)
    )
    assert res.status_code == 400


def test_expired_reset_token_is_rejected(client):
    email, pwd = make()
    token = auth.create_access_token(
        {"sub": email, "purpose": auth.RESET_PURPOSE, "pwd": auth._password_fingerprint(pwd)},
        timedelta(minutes=-1),
    )
    res = client.post("/reset_password", json={"password": "brand-new-pass"}, headers=bearer(token))
    assert res.status_code == 400
    page = client.get("/reset", params={"token": token})
    assert page.status_code == 400 and "Link expired" in page.text


def test_short_password_is_rejected(client):
    email, pwd = make()
    token = auth.create_reset_token(email, pwd)
    res = client.post("/reset_password", json={"password": "short"}, headers=bearer(token))
    assert res.status_code == 422
