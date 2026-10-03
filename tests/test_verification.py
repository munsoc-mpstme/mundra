"""6-digit email verification codes and the backup email."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import config
import database
import mails
import models
import permissions
from helpers import create_user, unique_email
from test_teams import add_invite, make_event, make_team

MAX = config.get_settings().verification_code_max_attempts


def set_code(email, code, minutes=15):
    expires = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    asyncio.run(database.set_verification_code(email, code, expires))


def check(email, code):
    return asyncio.run(database.check_verification_code(email, code, MAX))


# --- the code engine --------------------------------------------------------------


def test_correct_code_verifies_and_is_consumed():
    email = create_user_sync(verified=False)
    set_code(email, "123456")
    assert check(email, "123456") == "ok"
    # consumed: a second check finds nothing pending
    assert check(email, "123456") == "none"


def test_wrong_code_counts_attempts_then_locks():
    email = create_user_sync()
    set_code(email, "111111")
    for _ in range(MAX):
        assert check(email, "222222") == "invalid"
    # once attempts are used up, even the right code is refused until a new one is sent
    assert check(email, "111111") == "too_many"


def test_expired_code():
    email = create_user_sync()
    set_code(email, "123456", minutes=-1)  # already expired
    assert check(email, "123456") == "expired"


def test_no_code_pending():
    assert check(unique_email(), "123456") == "none"


def test_resending_resets_attempts():
    email = create_user_sync()
    set_code(email, "111111")
    assert check(email, "999999") == "invalid"
    set_code(email, "222222")  # resend
    assert check(email, "222222") == "ok"


# --- the endpoint -----------------------------------------------------------------


def test_verify_endpoint_verifies_and_applies_invites(client):
    email = create_user_sync(verified=False)
    event = make_event()
    team = make_team(event, "Hospitality", perms=[permissions.FOOD_MANAGE_ENTITLEMENT])
    add_invite(email, event, team)
    set_code(email, "654321")

    res = client.post("/verify_email", json={"email": email, "code": "654321"})
    assert res.status_code == 200
    assert asyncio.run(database.get_delegate_by_email(email)).verified is True
    # the rostered invite became a membership on verification
    _, perms, _ = asyncio.run(database.get_effective_access(email))
    assert permissions.FOOD_MANAGE_ENTITLEMENT in perms


def test_verify_endpoint_rejects_a_wrong_code(client):
    email = create_user_sync(verified=False)
    set_code(email, "111111")
    res = client.post("/verify_email", json={"email": email, "code": "000000"})
    assert res.status_code == 400
    assert asyncio.run(database.get_delegate_by_email(email)).verified is False


def test_verify_endpoint_404_when_nothing_pending(client):
    res = client.post("/verify_email", json={"email": unique_email(), "code": "123456"})
    assert res.status_code == 404


# --- backup email -----------------------------------------------------------------


def test_backup_email_round_trips():
    email = unique_email()
    asyncio.run(
        database.add_delegate(
            models.Delegate(
                id=uuid.uuid4().hex,
                firstname="A",
                lastname="B",
                email=email,
                backup_email="backup@example.com",
            )
        )
    )
    loaded = asyncio.run(database.get_delegate_by_email(email))
    assert loaded.backup_email == "backup@example.com"


def test_mail_recipients_include_backup():
    d = models.Delegate(
        id="x", firstname="A", lastname="B", email="primary@example.com",
        backup_email="backup@example.com",
    )
    assert mails._recipients(d) == ["primary@example.com", "backup@example.com"]
    # no backup -> just the primary
    d2 = models.Delegate(id="y", firstname="A", lastname="B", email="only@example.com")
    assert mails._recipients(d2) == ["only@example.com"]


# helper that wraps the async create_user
def create_user_sync(**kwargs):
    return asyncio.run(create_user(**kwargs))
