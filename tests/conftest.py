"""Test bootstrap.

`app.py` reads settings and calls `database.init()` at import time, and `database`
resolves its SQLite paths at module level. So both have to be pointed at a temp
directory *before* `app` is imported -- hence the import-order dance below.
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Settings must exist before `config.get_settings()` runs at import time. Populated
# here rather than read from a developer's .env so the suite is self-contained.
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("MAIL_SERVER", "localhost")
os.environ.setdefault("URL", "http://testserver")

_tmpdir = tempfile.mkdtemp(prefix="mundra-tests-")

import database

database.db = os.path.join(_tmpdir, "main.db")
database.backup_db = os.path.join(_tmpdir, "backup.db")
database.mm_db = os.path.join(_tmpdir, "mm.db")
database.mm_backup_db = os.path.join(_tmpdir, "mm_backup.db")
database.db_zip = os.path.join(_tmpdir, "backup_db.zip")

# Imported last: `app` runs database.init() at import time, so the paths above
# must already point at the temp directory.
import app as app_module
import auth
import models

ADMIN_EMAIL = "admin@munsocietympstme.com"
ADMIN_PASSWORD = "admin-password-123"


@pytest.fixture(scope="session")
def client():
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture(scope="session")
def admin_token(client):
    """Seed an admin directly, then log in as them.

    Doubles as the check that bcrypt 5 still verifies a hash produced by the same
    code path the old stack used.
    """
    with sqlite3.connect(database.db) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO admins (email, password) VALUES (?, ?)",
            (ADMIN_EMAIL, auth.hash_password(ADMIN_PASSWORD)),
        )
        conn.commit()

    res = client.post(
        "/login",
        data={"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["user_type"] == "admin"
    return body["access_token"]


@pytest.fixture(scope="session")
def mm_delegate():
    """An MM delegate seeded straight into the DB, for the /food template route."""
    delegate = models.MMDelegate(
        id="testdelegate001",
        firstname="Test",
        lastname="Delegate",
        email="delegate@example.com",
    )
    database.add_delegate(
        models.Delegate(
            id=delegate.id,
            firstname=delegate.firstname,
            lastname=delegate.lastname,
            email=delegate.email,
            verified=True,
        )
    )
    database.add_mm_delegate(delegate)
    return delegate
