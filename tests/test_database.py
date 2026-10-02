"""Query-layer tests against the real schema."""

import shutil
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

import database
import models
from helpers import create_user, unique_email


def _delegate(**overrides) -> models.Delegate:
    fields = dict(
        id=uuid.uuid4().hex,
        firstname="Ada",
        lastname="Lovelace",
        email=unique_email(),
    )
    return models.Delegate(**{**fields, **overrides})


async def test_pastmuns_round_trip_with_delimiters_in_names():
    """The old comma/semicolon string encoding corrupted names containing , or ;."""
    experiences = [
        models.MunExperience(
            name="Model UN, Mumbai; 2nd edition",
            committee="UNSC",
            delegation="France",
            year=2024,
            award="Best Delegate, Overall",
        ),
        models.MunExperience(name="Second", year=2023),
    ]
    delegate = await database.add_delegate(_delegate(pastmuns=experiences))

    loaded = await database.get_delegate_by_id(delegate.id)
    assert loaded.pastmuns == experiences


async def test_update_delegate_replaces_experiences_and_keeps_order():
    delegate = await database.add_delegate(
        _delegate(pastmuns=[models.MunExperience(name="Old", year=2020)])
    )
    delegate.firstname = "Grace"
    delegate.pastmuns = [
        models.MunExperience(name="B", year=2022),
        models.MunExperience(name="A", year=2021),
    ]
    await database.update_delegate_by_id(delegate.id, delegate)

    loaded = await database.get_delegate_by_email(delegate.email)
    assert loaded.firstname == "Grace"
    assert [m.name for m in loaded.pastmuns] == ["B", "A"]


async def test_delegate_email_is_unique():
    delegate = await database.add_delegate(_delegate())
    with pytest.raises(IntegrityError):
        await database.add_delegate(_delegate(email=delegate.email))


async def test_get_user_joins_the_delegate_profile():
    email = await create_user(password="password-123")
    user = await database.get_user_by_email(email)
    assert (user.firstname, user.lastname, user.email) == ("Test", "User", email)
    assert user.password != "password-123"  # stored hashed
    assert await database.get_user_by_email(unique_email()) is None


async def test_deleting_a_user_keeps_the_delegate_but_blocks_auth():
    email = await create_user()
    assert await database.get_auth_user(email) is not None

    await database.delete_user(email)

    assert await database.get_delegate_by_email(email) is not None
    assert await database.get_auth_user(email) is None


async def test_changing_a_delegates_email_follows_through_to_the_user():
    """users.email references delegates.email ON UPDATE CASCADE."""
    email = await create_user()
    delegate = await database.get_delegate_by_email(email)
    new_email = unique_email()
    delegate.email = new_email
    await database.update_delegate_by_id(delegate.id, delegate)

    assert await database.get_user_by_email(new_email) is not None
    assert await database.get_user_by_email(email) is None


# --- Mumbai MUN -------------------------------------------------------------


async def _mm_delegate(**overrides) -> models.MMDelegate:
    delegate = await database.add_delegate(
        _delegate(pastmuns=[models.MunExperience(name="Prior", year=2022)])
    )
    mm = models.MMDelegate(**delegate.model_dump(), **overrides)
    return await database.add_mm_delegate(mm)


async def test_mm_delegate_combines_profile_and_mm_fields():
    mm = await _mm_delegate(country="France", committee="UNSC")

    loaded = await database.get_mm_delegate_by_id(mm.id)
    assert (loaded.country, loaded.committee) == ("France", "UNSC")
    assert loaded.food_preference is None and loaded.food_notes == ""  # column defaults
    assert loaded.pastmuns[0].name == "Prior"
    assert (await database.get_mm_delegate_by_email(mm.email)).id == mm.id


async def test_plain_delegate_is_not_an_mm_delegate():
    delegate = await database.add_delegate(_delegate())
    assert await database.get_mm_delegate_by_id(delegate.id) is None
    assert delegate.id not in {m.id for m in await database.get_mm_delegates()}


async def test_update_mm_delegate_changes_mm_fields_only():
    mm = await _mm_delegate()
    mm.food_preference = "jain"
    mm.country = "India"
    mm.firstname = "Ignored"  # profile fields are not this function's business
    await database.update_mm_delegate(mm.id, mm)

    loaded = await database.get_mm_delegate_by_id(mm.id)
    assert loaded.food_preference == "jain" and loaded.country == "India"
    assert loaded.firstname == "Ada"


async def test_delete_mm_delegate_keeps_the_profile():
    mm = await _mm_delegate()
    await database.delete_mm_delegate(mm.id)
    assert await database.get_mm_delegate_by_id(mm.id) is None
    assert await database.get_delegate_by_id(mm.id) is not None


async def test_add_mm_delegate_requires_an_existing_delegate():
    orphan = models.MMDelegate(**_delegate().model_dump())
    with pytest.raises(IntegrityError):
        await database.add_mm_delegate(orphan)


# --- backup -----------------------------------------------------------------


@pytest.mark.skipif(shutil.which("pg_dump") is None, reason="pg_dump is not installed")
async def test_backup_fails_loudly_when_pg_dump_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "BACKUP_DIR", str(tmp_path))
    import config

    settings = config.get_settings()
    monkeypatch.setattr(settings, "postgres_port", 1)  # nothing listens here
    with pytest.raises(RuntimeError, match="pg_dump failed"):
        await database.backup_database()
