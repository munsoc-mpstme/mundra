"""Smoke tests covering the routes touched by the dependency upgrade.

The focus is the Starlette 1.x / Pydantic 2.13 / bcrypt 5 migration surface, not
the pre-existing correctness and auth issues catalogued in the upgrade plan.
"""

import shutil

import pytest

from helpers import auth_header, create_user


def test_status(client):
    res = client.get("/")
    assert res.status_code == 200
    assert res.json() == {"message": "Server is up and running"}


def test_rooms(client):
    res = client.get("/rooms")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list) and body
    assert {"id", "committee_name", "room_code", "floor"} <= body[0].keys()


def test_schedule(client):
    res = client.get("/schedule")
    assert res.status_code == 200
    body = res.json()
    assert body["conference_days"] and body["events"]


def test_static_mount(client):
    res = client.get("/static/logo.jpg")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"


def test_openapi_and_docs(client):
    assert client.get("/openapi.json").status_code == 200
    # redoc_url defaults to /docs, docs_url comes from DOCS_URL if set
    assert client.get("/docs").status_code == 200


# --- Starlette 1.x TemplateResponse migration -------------------------------


def test_reset_template(client):
    """The one remaining TemplateResponse route: would raise TypeError under Starlette
    1.x with the old argument order. /reset needs a valid token for a verified user."""
    import asyncio

    import auth
    from helpers import create_user

    email = asyncio.run(create_user())
    token = auth.create_access_token({"sub": email})
    res = client.get("/reset", params={"token": token})
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Reset Your Password" in res.text


# --- bcrypt 5 + auth round-trip ---------------------------------------------


def test_hash_and_verify_roundtrip(client):
    """bcrypt 5 must still produce and verify hashes the same way."""
    import auth

    hashed = auth.hash_password("some-password")
    assert hashed.startswith("$2b$")
    assert auth.verify_password("some-password", hashed)
    assert not auth.verify_password("wrong-password", hashed)


def test_login_rejects_bad_password(client):
    res = client.post(
        "/login",
        data={"username": "admin@munsocietympstme.com", "password": "wrong"},
    )
    assert res.status_code == 401


def test_admin_delegates_json(client, admin_token, mm_delegate):
    res = client.get("/delegates", params={"token": admin_token})
    assert res.status_code == 200
    assert any(d["id"] == mm_delegate.id for d in res.json())


def test_admin_delegates_csv(client, admin_token, mm_delegate):
    res = client.get("/delegates", params={"token": admin_token, "format": "csv"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "firstname" in res.text


def test_mm_delegates_requires_auth(client, admin_token, mm_delegate):
    res = client.get(
        "/mumbaimun/delegates", headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert res.status_code == 200
    assert any(d["id"] == mm_delegate.id for d in res.json())


# --- QR + backup ------------------------------------------------------------


def test_qr_generation(client, tmp_path, monkeypatch):
    """qrcode 8.2 + Pillow 12: mode-1 image must still save as JPEG."""
    import utils

    monkeypatch.setattr(utils, "qr_folder", str(tmp_path))
    monkeypatch.setattr("app.utils.qr_folder", str(tmp_path))
    res = client.get("/qr", params={"id": "test123"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"


needs_pg_dump = pytest.mark.skipif(
    shutil.which("pg_dump") is None, reason="pg_dump is not installed"
)


@needs_pg_dump
def test_backup_requires_admin(client, tmp_path, monkeypatch):
    import asyncio

    import database

    monkeypatch.setattr(database, "BACKUP_DIR", str(tmp_path))
    email = asyncio.run(create_user())
    res = client.get("/backup", headers=auth_header(email))
    assert res.status_code == 403
    assert not list(tmp_path.iterdir())


@needs_pg_dump
def test_backup_returns_a_pg_dump(client, admin_token, mm_delegate, tmp_path, monkeypatch):
    """The endpoint runs pg_dump and returns the custom-format dump ("PGDMP" magic)."""
    import database

    monkeypatch.setattr(database, "BACKUP_DIR", str(tmp_path))
    res = client.get("/backup", headers={"Authorization": f"Bearer {admin_token}"})
    assert res.status_code == 200
    assert res.content.startswith(b"PGDMP")
    assert len(list(tmp_path.glob("mundra-ondemand-*.dump"))) == 1


# --- slowapi + Starlette 1.x ------------------------------------------------


def test_rate_limiter_returns_429(client):
    """The limiter must produce a clean 429, not a stack trace."""
    codes = [
        client.post("/login", data={"username": "nobody@example.com", "password": "x"}).status_code
        for _ in range(15)
    ]
    assert 429 in codes, f"expected a 429 among {codes}"
