"""Test bootstrap.

The tests run against a database called `mundra_test` on the Postgres server that the
POSTGRES_* settings (from .env or the environment) point at. It is dropped and rebuilt
from the Alembic migrations at the start of every run, so the real database is never
touched and the tests exercise the real schema. Start Postgres first:

    docker compose up -d db

`app.py` reads settings at import time, and `db.py` builds its engine at import time,
so the database name has to be fixed *before* either is imported, hence the
import-order dance below.
"""

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Settings must exist before `config.get_settings()` runs. Populated here rather than
# read from a developer's .env so the suite does not depend on it. POSTGRES_PASSWORD is
# deliberately not defaulted: it has to match the real server, so it comes from .env.
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("MAIL_SERVER", "localhost")
os.environ.setdefault("URL", "http://testserver")

# Forced rather than defaulted: this run DROPs the database with this name, so it must
# never be the real one.
TEST_DB = "mundra_test"
os.environ["POSTGRES_DB"] = TEST_DB

import config

settings = config.get_settings()


async def _recreate_test_database():
    connection = await asyncpg.connect(
        user=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database="postgres",
    )
    try:
        await connection.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        await connection.execute(f"CREATE DATABASE {TEST_DB}")
    finally:
        await connection.close()


asyncio.run(_recreate_test_database())

from alembic import command
from alembic.config import Config

command.upgrade(Config(str(ROOT / "alembic.ini")), "head")

import db
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

# The TestClient runs the app on its own event loop and the async tests run on
# another, and a pooled connection cannot move between loops. So don't pool.
db.engine = create_async_engine(settings.database_url, poolclass=NullPool)
db.SessionLocal.configure(bind=db.engine)

# Imported last so it sees the test database.
import app as app_module
import database
import models
from helpers import create_user

ADMIN_EMAIL = "admin@munsocietympstme.com"
ADMIN_PASSWORD = "admin-password-123"


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Each test starts with fresh rate limits. Every request comes from the same test
    client address, so /login's 10/minute would otherwise trip across unrelated tests."""
    app_module.limiter.reset()


@pytest.fixture(scope="session")
def client():
    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture(scope="session")
def admin_token(client):
    """Create an admin the way production does (a registered user promoted with
    `make-admin`), then log in as them. This is the one real login the suite does,
    since /login is rate limited."""
    asyncio.run(create_user(ADMIN_EMAIL, ADMIN_PASSWORD, role="admin"))

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

    async def seed():
        await database.add_delegate(
            models.Delegate(
                id=delegate.id,
                firstname=delegate.firstname,
                lastname=delegate.lastname,
                email=delegate.email,
                verified=True,
            )
        )
        await database.add_mm_delegate(delegate)

    asyncio.run(seed())
    return delegate
