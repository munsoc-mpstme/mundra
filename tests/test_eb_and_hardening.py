"""The eb role, and the routes that used to be open: /hash_password and /qr."""

import asyncio
import json
from pathlib import Path

import database
import db
import permissions
from helpers import auth_header, create_user, unique_email
from sqlalchemy import select


def make(**kwargs):
    return asyncio.run(create_user(**kwargs))


def delegate_id(email):
    return asyncio.run(database.get_delegate_by_email(email)).id


# ---- eb role -------------------------------------------------------------------------


def test_admin_can_make_someone_eb_and_it_is_audited(client):
    admin, target = make(role="admin"), make()

    res = client.patch(
        f"/admin/users/{target}/role", json={"role": "eb"}, headers=auth_header(admin)
    )

    assert res.status_code == 200
    assert res.json() == {"email": target, "old_role": "delegate", "new_role": "eb"}
    assert asyncio.run(database.get_role(target)) == "eb"

    async def audit():
        async with db.SessionLocal() as session:
            rows = await session.scalars(
                select(db.AdminAuditRow).where(db.AdminAuditRow.target_email == target)
            )
            return [(r.old_role, r.new_role) for r in rows]

    assert asyncio.run(audit()) == [("delegate", "eb")]


def test_me_reports_the_eb_role_with_no_oc_access(client):
    eb = make(role="eb")

    me = client.get("/delegates/me", headers=auth_header(eb)).json()

    assert me["role"] == "eb"
    assert me["is_head"] is False
    assert me["permissions"] == sorted(permissions.APP_ROLE_PERMISSIONS["eb"])


def test_an_eb_cannot_use_admin_or_oc_routes(client):
    eb, other = make(role="eb"), make()

    assert client.patch(
        f"/admin/users/{other}/role", json={"role": "oc"}, headers=auth_header(eb)
    ).status_code == 403
    assert client.post(
        f"/manual_verify?email={other}", headers=auth_header(eb)
    ).status_code == 403


def test_an_unknown_role_is_still_rejected(client):
    admin, target = make(role="admin"), make()
    res = client.patch(
        f"/admin/users/{target}/role", json={"role": "chair"}, headers=auth_header(admin)
    )
    assert res.status_code == 422


# ---- routes that were open -----------------------------------------------------------


def test_hash_password_is_gone(client):
    assert client.get("/hash_password", params={"password": "x"}).status_code == 404


def test_qr_for_a_real_delegate_is_an_image(client):
    email = make()
    res = client.get("/qr", params={"id": delegate_id(email)})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("image/")


def test_qr_for_an_unknown_id_is_404(client):
    assert client.get("/qr", params={"id": "no-such-delegate"}).status_code == 404


def test_qr_cannot_be_pointed_at_other_files(client):
    for sneaky in ("../data/schedule", "..%2Fdata%2Fschedule", "../../etc/passwd", "a/b"):
        res = client.get(f"/qr?id={sneaky}")
        assert res.status_code == 404, sneaky


# ---- schedule data -------------------------------------------------------------------


def test_schedule_days_are_the_2026_conference_dates():
    path = Path(__file__).resolve().parent.parent / "data" / "schedule.json"
    days = json.loads(path.read_text(encoding="utf-8"))["conference_days"]
    assert [d["display_date"] for d in days] == [
        "October 30, 2026",
        "October 31, 2026",
        "November 1, 2026",
    ]
