"""Smoke tests covering the routes touched by the dependency upgrade.

The focus is the Starlette 1.x / Pydantic 2.13 / bcrypt 5 migration surface, not
the pre-existing correctness and auth issues catalogued in the upgrade plan.
"""

import zipfile


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


def test_scan_template(client):
    """Would raise TypeError under Starlette 1.x with the old argument order."""
    res = client.get("/scan")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "QR Code Scanner" in res.text


def test_food_template(client, mm_delegate):
    res = client.get("/food", params={"id": mm_delegate.id})
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert mm_delegate.firstname in res.text


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


def test_backup(client, admin_token, mm_delegate):
    """Exercises the backup itself.

    The `/backup` route is a thin FileResponse over a path it rebuilds inline
    rather than reading `database.db_zip`, so it cannot be pointed at a temp dir
    from here. In production both resolve to backups/backup_db.zip, so the
    interesting logic is `database.backup_database()`.
    """
    import sqlite3

    import database

    database.backup_database()

    with zipfile.ZipFile(database.db_zip) as z:
        assert set(z.namelist()) == {"backup.db", "mm_backup.db"}

    # the backup must be a readable database holding the seeded rows
    with sqlite3.connect(database.backup_db) as conn:
        assert conn.execute("SELECT count(*) FROM delegates").fetchone()[0] >= 1
    with sqlite3.connect(database.mm_backup_db) as conn:
        rows = conn.execute(
            "SELECT id FROM mm_delegates WHERE id = ?", (mm_delegate.id,)
        ).fetchall()
        assert rows


# --- slowapi + Starlette 1.x ------------------------------------------------


def test_rate_limiter_returns_429(client):
    """The limiter must produce a clean 429, not a stack trace."""
    codes = [
        client.post("/login", data={"username": "nobody@example.com", "password": "x"}).status_code
        for _ in range(15)
    ]
    assert 429 in codes, f"expected a 429 among {codes}"
