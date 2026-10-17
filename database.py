"""Query functions. Table classes and the engine live in db.py; request/response
shapes live in models.py. Every function opens its own short-lived session and
returns Pydantic models, never ORM rows."""

import argparse
import asyncio
import os
import secrets
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import contains_eager, selectinload

import config
import db
import models
import permissions

BACKUP_DIR = os.path.join(os.path.dirname(__file__), "backups")


####################
# ROW <-> MODEL MAPPING
####################


def _experience_rows(experiences: list[models.MunExperience]) -> list[db.MunExperienceRow]:
    return [
        db.MunExperienceRow(
            position=position,
            name=mun.name,
            committee=mun.committee,
            delegation=mun.delegation,
            year=mun.year,
            award=mun.award,
        )
        for position, mun in enumerate(experiences)
    ]


def _delegate_fields(row: db.DelegateRow) -> dict:
    """Needs row.experiences loaded."""
    return dict(
        id=row.id,
        firstname=row.firstname,
        lastname=row.lastname,
        email=row.email,
        backup_email=row.backup_email,
        contact=row.contact,
        dateofbirth=row.dateofbirth,
        gender=row.gender,
        pastmuns=[
            models.MunExperience(
                name=e.name,
                committee=e.committee,
                delegation=e.delegation,
                year=e.year,
                award=e.award,
            )
            for e in row.experiences
        ],
        verified=row.verified,
    )


def _to_delegate(row: db.DelegateRow) -> models.Delegate:
    return models.Delegate(**_delegate_fields(row))


def _to_mm_delegate(row: db.DelegateRow) -> models.MMDelegate:
    """Needs row.experiences and row.mm loaded."""
    mm = row.mm
    return models.MMDelegate(
        **_delegate_fields(row),
        country=mm.country,
        committee=mm.committee,
        food_preference=mm.food_preference,
        food_notes=mm.food_notes,
    )


def _apply_delegate(row: db.DelegateRow, delegate: models.Delegate) -> None:
    row.firstname = delegate.firstname
    row.lastname = delegate.lastname
    row.email = delegate.email
    row.backup_email = delegate.backup_email
    row.contact = delegate.contact
    row.dateofbirth = delegate.dateofbirth
    row.gender = delegate.gender
    row.verified = delegate.verified


_WITH_EXPERIENCES = selectinload(db.DelegateRow.experiences)


####################
# USERS
####################


async def add_user(user: models.User) -> models.User:
    async with db.SessionLocal() as session, session.begin():
        session.add(db.UserRow(email=user.email, password=user.password))
    return user


async def get_user_by_email(email: str) -> models.User | None:
    async with db.SessionLocal() as session:
        result = await session.execute(
            select(db.UserRow.password, db.DelegateRow)
            .join(db.DelegateRow, db.DelegateRow.email == db.UserRow.email)
            .where(db.UserRow.email == email)
            .options(selectinload(db.DelegateRow.experiences))
        )
        found = result.first()
        if found is None:
            return None
        password, delegate = found
        return models.User(
            firstname=delegate.firstname,
            lastname=delegate.lastname,
            email=delegate.email,
            password=password,
        )


async def get_role(email: str) -> models.Role | None:
    async with db.SessionLocal() as session:
        return await session.scalar(select(db.UserRow.role).where(db.UserRow.email == email))


async def get_auth_user(email: str) -> models.AuthUser | None:
    """The delegate profile and role of the user with this email, or None if there is
    no such user. Called on every authenticated request."""
    async with db.SessionLocal() as session:
        result = await session.execute(
            select(db.DelegateRow, db.UserRow.role)
            .join(db.UserRow, db.UserRow.email == db.DelegateRow.email)
            .where(db.DelegateRow.email == email)
            .options(_WITH_EXPERIENCES)
        )
        found = result.first()
        if found is None:
            return None
        delegate, role = found
        return models.AuthUser(**_delegate_fields(delegate), role=role)


async def change_user_pass(email: models.EmailStr, password: str) -> None:
    async with db.SessionLocal() as session, session.begin():
        await session.execute(
            update(db.UserRow).where(db.UserRow.email == email).values(password=password)
        )


async def delete_user(email: models.EmailStr) -> None:
    async with db.SessionLocal() as session, session.begin():
        user = await session.get(db.UserRow, email)
        if user is not None:
            await session.delete(user)


####################
# ROLES
####################


async def set_role(actor_email: str, target_email: str, new_role: str) -> str:
    """Change target's role and record it in the audit table. Returns the old role.

    Raises ValueError (unknown role), PermissionError (actor is not an admin, is
    changing their own role, or this would remove the last admin) or LookupError
    (no such user).
    """
    if new_role not in db.ROLES:
        raise ValueError(f"Unknown role: {new_role}")
    if actor_email == target_email:
        raise PermissionError("You cannot change your own role")

    async with db.SessionLocal() as session, session.begin():
        # Lock every admin row (in a fixed order, so concurrent calls cannot deadlock)
        # and re-check the actor inside the transaction: two admins demoting each
        # other at the same moment must not leave the system with zero admins.
        admins = (
            await session.scalars(
                select(db.UserRow.email)
                .where(db.UserRow.role == "admin")
                .order_by(db.UserRow.email)
                .with_for_update()
            )
        ).all()
        if actor_email not in admins:
            raise PermissionError("Only admins can change roles")

        target = await session.get(db.UserRow, target_email, with_for_update=True)
        if target is None:
            raise LookupError("User not found")
        if target.role == "admin" and new_role != "admin" and len(admins) <= 1:
            raise PermissionError("You cannot demote the last admin")

        old_role = target.role
        if old_role != new_role:
            target.role = new_role
            session.add(
                db.AdminAuditRow(
                    actor_email=actor_email,
                    target_email=target_email,
                    old_role=old_role,
                    new_role=new_role,
                )
            )
        return old_role


async def make_admin(email: str) -> str:
    """Bootstrap path for the first admin, used by the command line below. Returns the
    old role. Raises LookupError if there is no user with this email."""
    async with db.SessionLocal() as session, session.begin():
        user = await session.get(db.UserRow, email, with_for_update=True)
        if user is None:
            raise LookupError(f"No user with email {email}. Register the account first.")
        old_role = user.role
        if old_role != "admin":
            user.role = "admin"
            session.add(
                db.AdminAuditRow(
                    actor_email="system:cli",
                    target_email=email,
                    old_role=old_role,
                    new_role="admin",
                )
            )
        return old_role


####################
# DELEGATES
####################


async def ensure_bootstrap_admin(
    email: str, password_hash: str | None = None
) -> str:
    """Make `email` an admin (and verified), creating the account if it is missing and a
    password hash was given. Used at startup for ADMIN_EMAIL / ADMIN_PASSWORD, so the first
    admin needs no shell on the host. Never changes an existing account's password.

    Returns "created", "promoted" (role or verification changed), "unchanged" (already a
    verified admin) or "missing" (no such account and no password to create one with).
    """
    async with db.SessionLocal() as session, session.begin():
        delegate = await session.scalar(
            select(db.DelegateRow).where(db.DelegateRow.email == email)
        )
        user = await session.get(db.UserRow, email, with_for_update=True)
        if user is None and password_hash is None:
            return "missing"  # nothing to promote and no password to create one with
        outcome = "unchanged"

        if delegate is None:
            delegate = db.DelegateRow(
                id=secrets.token_hex(16),
                firstname="Admin",
                lastname="User",
                email=email,
                verified=True,
            )
            session.add(delegate)
            await session.flush()  # the user row references the delegate's email
            outcome = "created"
        elif not delegate.verified:
            delegate.verified = True
            outcome = "promoted"

        if user is None:
            user = db.UserRow(email=email, password=password_hash, role="delegate")
            session.add(user)
            outcome = "created" if outcome == "created" else "promoted"

        if user.role != "admin":
            session.add(
                db.AdminAuditRow(
                    actor_email="system:startup",
                    target_email=email,
                    old_role=user.role,
                    new_role="admin",
                )
            )
            user.role = "admin"
            if outcome == "unchanged":
                outcome = "promoted"
        return outcome


async def add_delegate(delegate: models.Delegate) -> models.Delegate:
    row = db.DelegateRow(
        id=delegate.id,
        firstname=delegate.firstname,
        lastname=delegate.lastname,
        email=delegate.email,
        backup_email=delegate.backup_email,
        contact=delegate.contact,
        dateofbirth=delegate.dateofbirth,
        gender=delegate.gender,
        verified=delegate.verified,
        experiences=_experience_rows(delegate.pastmuns),
    )
    async with db.SessionLocal() as session, session.begin():
        session.add(row)
    return delegate


async def get_delegates() -> list[models.Delegate]:
    async with db.SessionLocal() as session:
        rows = await session.scalars(
            select(db.DelegateRow)
            .options(_WITH_EXPERIENCES)
            .order_by(db.DelegateRow.created_at, db.DelegateRow.id)
        )
        return [_to_delegate(row) for row in rows]


async def get_delegate_by_id(id: str) -> models.Delegate | None:
    async with db.SessionLocal() as session:
        row = await session.scalar(
            select(db.DelegateRow).where(db.DelegateRow.id == id).options(_WITH_EXPERIENCES)
        )
        return _to_delegate(row) if row else None


async def get_delegate_by_email(email: models.EmailStr) -> models.Delegate | None:
    async with db.SessionLocal() as session:
        row = await session.scalar(
            select(db.DelegateRow)
            .where(db.DelegateRow.email == email)
            .options(_WITH_EXPERIENCES)
        )
        return _to_delegate(row) if row else None


async def update_delegate_by_id(id: str, delegate: models.Delegate) -> models.Delegate:
    async with db.SessionLocal() as session, session.begin():
        row = await session.scalar(
            select(db.DelegateRow).where(db.DelegateRow.id == id).options(_WITH_EXPERIENCES)
        )
        if row is not None:
            _apply_delegate(row, delegate)
            row.experiences = _experience_rows(delegate.pastmuns)
    return delegate


async def verify_delegate_email(email: models.EmailStr) -> None:
    async with db.SessionLocal() as session, session.begin():
        await session.execute(
            update(db.DelegateRow).where(db.DelegateRow.email == email).values(verified=True)
        )


####################
# EMAIL VERIFICATION CODES
####################


async def set_verification_code(email: str, code: str, expires_at: datetime) -> None:
    """Store (or replace) the pending 6-digit code for an email, resetting attempts."""
    async with db.SessionLocal() as session, session.begin():
        row = await session.get(db.EmailVerificationRow, email)
        if row is None:
            session.add(
                db.EmailVerificationRow(email=email, code=code, expires_at=expires_at)
            )
        else:
            row.code = code
            row.expires_at = expires_at
            row.attempts = 0


async def check_verification_code(email: str, code: str, max_attempts: int) -> str:
    """Check a submitted code. Returns one of: 'ok' (and consumes the code), 'invalid'
    (wrong code, attempt counted), 'expired', 'too_many', or 'none' (no code pending)."""
    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session, session.begin():
        row = await session.get(db.EmailVerificationRow, email, with_for_update=True)
        if row is None:
            return "none"
        if row.expires_at <= now:
            await session.delete(row)
            return "expired"
        if row.attempts >= max_attempts:
            return "too_many"
        if secrets.compare_digest(row.code, code):
            await session.delete(row)
            return "ok"
        row.attempts += 1
        return "invalid"


####################
# MM DELEGATES
####################
# An MM delegate is a delegate (same id) plus a mm_delegates row. The profile fields
# live only in delegates; the mm_delegates row holds country, committee and meals.


def _mm_query():
    return (
        select(db.DelegateRow)
        .join(db.DelegateRow.mm)
        .options(contains_eager(db.DelegateRow.mm), _WITH_EXPERIENCES)
    )


async def add_mm_delegate(mm_delegate: models.MMDelegate) -> models.MMDelegate:
    """The delegate with this id must already exist."""
    async with db.SessionLocal() as session, session.begin():
        session.add(
            db.MMDelegateRow(
                delegate_id=mm_delegate.id,
                country=mm_delegate.country,
                committee=mm_delegate.committee,
                food_preference=mm_delegate.food_preference,
                food_notes=mm_delegate.food_notes,
            )
        )
    return mm_delegate


async def get_mm_delegates() -> list[models.MMDelegate]:
    async with db.SessionLocal() as session:
        rows = await session.scalars(
            _mm_query().order_by(db.DelegateRow.created_at, db.DelegateRow.id)
        )
        return [_to_mm_delegate(row) for row in rows]


async def get_mm_delegate_by_id(id: str) -> models.MMDelegate | None:
    async with db.SessionLocal() as session:
        row = await session.scalar(_mm_query().where(db.DelegateRow.id == id))
        return _to_mm_delegate(row) if row else None


async def get_mm_delegate_by_email(email: str) -> models.MMDelegate | None:
    async with db.SessionLocal() as session:
        row = await session.scalar(_mm_query().where(db.DelegateRow.email == email))
        return _to_mm_delegate(row) if row else None


async def update_mm_delegate(id: str, mm_delegate: models.MMDelegate) -> models.MMDelegate:
    """Updates only the Mumbai MUN fields (country, committee, food). Profile fields
    are changed through update_delegate_by_id."""
    async with db.SessionLocal() as session, session.begin():
        row = await session.get(db.MMDelegateRow, id)
        if row is not None:
            row.country = mm_delegate.country
            row.committee = mm_delegate.committee
            row.food_preference = mm_delegate.food_preference
            row.food_notes = mm_delegate.food_notes
    return mm_delegate


async def delete_mm_delegate(id: str) -> None:
    """Removes the Mumbai MUN registration; the delegate profile stays."""
    async with db.SessionLocal() as session, session.begin():
        row = await session.get(db.MMDelegateRow, id)
        if row is not None:
            await session.delete(row)


####################
# EVENT-LOCAL TIME
####################


def _event_tz() -> timezone:
    """The conference's timezone (EVENT_UTC_OFFSET_MINUTES, IST by default)."""
    return timezone(timedelta(minutes=config.get_settings().event_utc_offset_minutes))


def _local_date(moment: datetime) -> date:
    """The calendar date of a moment where the conference is held. Days are counted here,
    not in UTC: midnight IST is 18:30 UTC of the day before."""
    return moment.astimezone(_event_tz()).date()


def _end_of_local_day(moment: datetime | None) -> datetime | None:
    """The midnight that closes the local day containing this moment. Team access ends
    here, so it lasts through the event's last day whatever time the end was given as."""
    if moment is None:
        return None
    return datetime.combine(_local_date(moment) + timedelta(days=1), time.min, _event_tz())


####################
# ORGANIZING COMMITTEE ACCESS (docs/adr/0003)
####################


async def get_effective_access(
    email: str,
) -> tuple[bool, set[str], list[models.Membership]]:
    """The caller's OC access, read fresh on every request (like the role in ADR 0002):
    whether they are a head, the union of permissions their active memberships grant, and
    those memberships. A head holds every permission. A membership is active while its
    ends_at is null or still in the future.
    """
    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session:
        is_head = (
            await session.scalar(
                select(db.EventHeadRow.id).where(db.EventHeadRow.user_email == email).limit(1)
            )
        ) is not None

        rows = (
            await session.execute(
                select(db.MembershipRow, db.TeamRow.name)
                .join(db.TeamRow, db.TeamRow.id == db.MembershipRow.team_id)
                .where(db.MembershipRow.user_email == email)
                .where(
                    or_(
                        db.MembershipRow.ends_at.is_(None),
                        db.MembershipRow.ends_at > now,
                    )
                )
                .order_by(db.MembershipRow.created_at, db.MembershipRow.id)
            )
        ).all()

        # One query for the permissions of every team involved, then group in Python.
        team_ids = {row.MembershipRow.team_id for row in rows}
        perms_by_team: dict[int, list[str]] = {tid: [] for tid in team_ids}
        if team_ids:
            for team_id, permission in (
                await session.execute(
                    select(db.TeamPermissionRow.team_id, db.TeamPermissionRow.permission)
                    .where(db.TeamPermissionRow.team_id.in_(team_ids))
                )
            ).all():
                perms_by_team[team_id].append(permission)

    memberships = [
        models.Membership(
            team=name,
            committee=m.committee,
            level=m.level,
            permissions=sorted(perms_by_team.get(m.team_id, [])),
        )
        for m, name in ((row.MembershipRow, row.name) for row in rows)
    ]

    if is_head:
        effective = set(permissions.ALL_PERMISSIONS)
    else:
        effective = {p for team in memberships for p in team.permissions}
    return is_head, effective, memberships


async def apply_pending_invites(email: str) -> int:
    """Turn any team_invites for this email into real memberships and remove the invites.
    Called when an email becomes verified, so a rostered person is a member the moment
    they finish signing up. Returns how many invites were applied. Idempotent: an invite
    for a team the user already belongs to is dropped without a duplicate membership."""
    async with db.SessionLocal() as session, session.begin():
        invites = (
            await session.scalars(
                select(db.TeamInviteRow).where(db.TeamInviteRow.email == email)
            )
        ).all()
        if not invites:
            return 0

        existing = set(
            (
                await session.scalars(
                    select(db.MembershipRow.team_id).where(
                        db.MembershipRow.user_email == email
                    )
                )
            ).all()
        )
        # ends_at follows each invite's event end (the close of its last local day), so
        # applied access still lapses on its own.
        event_ends = dict(
            (
                await session.execute(
                    select(db.EventRow.id, db.EventRow.ends_at).where(
                        db.EventRow.id.in_({i.event_id for i in invites})
                    )
                )
            ).all()
        )

        applied = 0
        for invite in invites:
            if invite.team_id not in existing:
                session.add(
                    db.MembershipRow(
                        event_id=invite.event_id,
                        user_email=email,
                        team_id=invite.team_id,
                        committee=invite.committee,
                        level=invite.level,
                        ends_at=_end_of_local_day(event_ends.get(invite.event_id)),
                    )
                )
                existing.add(invite.team_id)
                applied += 1
            await session.delete(invite)
        return applied


####################
# OC ADMINISTRATION: events, teams, rosters, heads (docs/adr/0003)
####################


def _to_team(row: db.TeamRow) -> models.Team:
    """Needs row.permissions loaded."""
    return models.Team(
        id=row.id,
        event_id=row.event_id,
        name=row.name,
        description=row.description,
        permissions=sorted(p.permission for p in row.permissions),
    )


async def get_event(event_id: int) -> models.Event | None:
    async with db.SessionLocal() as session:
        row = await session.get(db.EventRow, event_id)
        return (
            models.Event(id=row.id, name=row.name, starts_at=row.starts_at, ends_at=row.ends_at)
            if row
            else None
        )


async def list_events() -> list[models.Event]:
    """Every event, oldest first. The Delego app uses the first one to manage its teams."""
    async with db.SessionLocal() as session:
        rows = await session.scalars(select(db.EventRow).order_by(db.EventRow.id))
        return [
            models.Event(id=r.id, name=r.name, starts_at=r.starts_at, ends_at=r.ends_at)
            for r in rows
        ]


async def set_event_dates(event_id: int, dates: models.EventDates) -> models.Event | None:
    """Set an event's dates. Its memberships follow the new end, so members added before
    the dates were known (or before they were corrected) lapse with the event too."""
    async with db.SessionLocal() as session, session.begin():
        row = await session.get(db.EventRow, event_id)
        if row is None:
            return None
        row.starts_at = dates.starts_at
        row.ends_at = dates.ends_at
        await session.execute(
            update(db.MembershipRow)
            .where(db.MembershipRow.event_id == event_id)
            .values(ends_at=_end_of_local_day(dates.ends_at))
        )
    return await get_event(event_id)


async def list_teams(event_id: int) -> list[models.Team]:
    async with db.SessionLocal() as session:
        rows = await session.scalars(
            select(db.TeamRow)
            .where(db.TeamRow.event_id == event_id)
            .options(selectinload(db.TeamRow.permissions))
            .order_by(db.TeamRow.name)
        )
        return [_to_team(row) for row in rows]


async def create_team(event_id: int, new_team: models.NewTeam) -> models.Team:
    """Create a team with its permissions. Raises ValueError for an unknown permission or
    a missing event, and IntegrityError for a duplicate team name in the event."""
    unknown = set(new_team.permissions) - permissions.ALL_PERMISSIONS
    if unknown:
        raise ValueError(f"Unknown permission(s): {', '.join(sorted(unknown))}")
    async with db.SessionLocal() as session, session.begin():
        if await session.get(db.EventRow, event_id) is None:
            raise ValueError("Unknown event")
        team = db.TeamRow(
            event_id=event_id, name=new_team.name, description=new_team.description
        )
        team.permissions = [
            db.TeamPermissionRow(permission=p) for p in dict.fromkeys(new_team.permissions)
        ]
        session.add(team)
        await session.flush()
        team_id = team.id
    return (await _get_team(team_id))


async def _get_team(team_id: int) -> models.Team | None:
    async with db.SessionLocal() as session:
        row = await session.scalar(
            select(db.TeamRow)
            .where(db.TeamRow.id == team_id)
            .options(selectinload(db.TeamRow.permissions))
        )
        return _to_team(row) if row else None


async def set_team_permissions(
    team_id: int, change: models.TeamPermissionsChange
) -> models.Team | None:
    """Replace a team's permissions wholesale. Raises ValueError for an unknown verb."""
    unknown = set(change.permissions) - permissions.ALL_PERMISSIONS
    if unknown:
        raise ValueError(f"Unknown permission(s): {', '.join(sorted(unknown))}")
    async with db.SessionLocal() as session, session.begin():
        team = await session.scalar(
            select(db.TeamRow)
            .where(db.TeamRow.id == team_id)
            .options(selectinload(db.TeamRow.permissions))
        )
        if team is None:
            return None
        # Delete the old rows before inserting the new ones, so re-granting a permission
        # the team already had does not collide on the (team_id, permission) unique index
        # mid-flush.
        team.permissions.clear()
        await session.flush()
        team.permissions = [
            db.TeamPermissionRow(permission=p) for p in dict.fromkeys(change.permissions)
        ]
    return await _get_team(team_id)


async def is_team_lead(email: str, team_id: int) -> bool:
    """True if this user is an active lead of this team (used to authorise roster edits)."""
    now = datetime.now(timezone.utc)
    async with db.SessionLocal() as session:
        return (
            await session.scalar(
                select(db.MembershipRow.id)
                .where(
                    db.MembershipRow.user_email == email,
                    db.MembershipRow.team_id == team_id,
                    db.MembershipRow.level == "lead",
                    or_(
                        db.MembershipRow.ends_at.is_(None),
                        db.MembershipRow.ends_at > now,
                    ),
                )
                .limit(1)
            )
        ) is not None


async def add_to_roster(
    actor_email: str, team_id: int, add: models.RosterAdd
) -> str:
    """Add someone to a team: a membership if they already have an account, otherwise an
    invite that becomes a membership when they verify. Upserts the level/committee if they
    are already on it. Audited. Returns 'member' or 'invited'. Raises LookupError if the
    team is gone."""
    async with db.SessionLocal() as session, session.begin():
        team = await session.get(db.TeamRow, team_id)
        if team is None:
            raise LookupError("Team not found")
        event = await session.get(db.EventRow, team.event_id)

        registered = (
            await session.scalar(
                select(db.UserRow.email).where(db.UserRow.email == add.email)
            )
        ) is not None

        action = None
        if registered:
            existing = await session.scalar(
                select(db.MembershipRow).where(
                    db.MembershipRow.event_id == team.event_id,
                    db.MembershipRow.user_email == add.email,
                    db.MembershipRow.team_id == team_id,
                )
            )
            if existing is None:
                session.add(
                    db.MembershipRow(
                        event_id=team.event_id,
                        user_email=add.email,
                        team_id=team_id,
                        committee=add.committee,
                        level=add.level,
                        ends_at=_end_of_local_day(event.ends_at) if event else None,
                    )
                )
                action = "grant"
            else:
                if existing.level != add.level:
                    action = "level_change"
                existing.level = add.level
                existing.committee = add.committee
            status = "member"
        else:
            existing = await session.scalar(
                select(db.TeamInviteRow).where(
                    db.TeamInviteRow.email == add.email,
                    db.TeamInviteRow.team_id == team_id,
                )
            )
            if existing is None:
                session.add(
                    db.TeamInviteRow(
                        email=add.email,
                        event_id=team.event_id,
                        team_id=team_id,
                        committee=add.committee,
                        level=add.level,
                    )
                )
                action = "grant"
            else:
                existing.level = add.level
                existing.committee = add.committee
            status = "invited"

        if action:
            session.add(
                db.MembershipAuditRow(
                    actor_email=actor_email,
                    target_email=add.email,
                    team_name=team.name,
                    action=action,
                )
            )
        return status


async def remove_from_roster(actor_email: str, team_id: int, email: str) -> bool:
    """Remove someone from a team (membership and/or pending invite). Audited. Returns
    False if they were not on the roster. Raises LookupError if the team is gone."""
    async with db.SessionLocal() as session, session.begin():
        team = await session.get(db.TeamRow, team_id)
        if team is None:
            raise LookupError("Team not found")

        removed = False
        membership = await session.scalar(
            select(db.MembershipRow).where(
                db.MembershipRow.user_email == email,
                db.MembershipRow.team_id == team_id,
            )
        )
        if membership is not None:
            await session.delete(membership)
            removed = True
        invite = await session.scalar(
            select(db.TeamInviteRow).where(
                db.TeamInviteRow.email == email, db.TeamInviteRow.team_id == team_id
            )
        )
        if invite is not None:
            await session.delete(invite)
            removed = True

        if removed:
            session.add(
                db.MembershipAuditRow(
                    actor_email=actor_email,
                    target_email=email,
                    team_name=team.name,
                    action="revoke",
                )
            )
        return removed


async def list_team_members(team_id: int) -> list[models.RosterEntry]:
    """The team's current members (with names) and any pending invites."""
    async with db.SessionLocal() as session:
        members = (
            await session.execute(
                select(
                    db.MembershipRow.user_email,
                    db.DelegateRow.firstname,
                    db.DelegateRow.lastname,
                    db.MembershipRow.committee,
                    db.MembershipRow.level,
                )
                .join(db.DelegateRow, db.DelegateRow.email == db.MembershipRow.user_email)
                .where(db.MembershipRow.team_id == team_id)
                .order_by(db.DelegateRow.firstname)
            )
        ).all()
        invites = (
            await session.execute(
                select(
                    db.TeamInviteRow.email,
                    db.TeamInviteRow.committee,
                    db.TeamInviteRow.level,
                )
                .where(db.TeamInviteRow.team_id == team_id)
                .order_by(db.TeamInviteRow.email)
            )
        ).all()

    entries = [
        models.RosterEntry(
            email=email,
            name=f"{firstname} {lastname}",
            committee=committee,
            level=level,
            status="member",
        )
        for email, firstname, lastname, committee, level in members
    ]
    entries += [
        models.RosterEntry(email=email, committee=committee, level=level, status="invited")
        for email, committee, level in invites
    ]
    return entries


async def list_heads(event_id: int) -> list[str]:
    async with db.SessionLocal() as session:
        return list(
            (
                await session.scalars(
                    select(db.EventHeadRow.user_email)
                    .where(db.EventHeadRow.event_id == event_id)
                    .order_by(db.EventHeadRow.user_email)
                )
            ).all()
        )


async def add_head(actor_email: str, event_id: int, email: str) -> bool:
    """Grant an existing user the head role for an event. Returns False if they already
    have it. Raises LookupError if there is no such user or event."""
    async with db.SessionLocal() as session, session.begin():
        if await session.get(db.EventRow, event_id) is None:
            raise LookupError("Event not found")
        if (
            await session.scalar(select(db.UserRow.email).where(db.UserRow.email == email))
        ) is None:
            raise LookupError("No such user; register the account first")
        existing = await session.scalar(
            select(db.EventHeadRow.id).where(
                db.EventHeadRow.event_id == event_id,
                db.EventHeadRow.user_email == email,
            )
        )
        if existing is not None:
            return False
        session.add(
            db.EventHeadRow(event_id=event_id, user_email=email, granted_by=actor_email)
        )
        return True


async def remove_head(event_id: int, email: str) -> bool:
    """Revoke a head grant. Returns False if they were not a head."""
    async with db.SessionLocal() as session, session.begin():
        row = await session.scalar(
            select(db.EventHeadRow).where(
                db.EventHeadRow.event_id == event_id,
                db.EventHeadRow.user_email == email,
            )
        )
        if row is None:
            return False
        await session.delete(row)
        return True


####################
# FOOD: preference and meal collection (docs/adr/0003)
####################


async def set_food_preference(
    id: str, change: models.FoodPreferenceChange
) -> models.MMDelegate | None:
    """Set an MM delegate's diet (a delegate for themselves, or hospitality on the day).
    food_preference is set to the given value, including None to clear it; food_notes is
    left untouched when the change omits it (sends None)."""
    async with db.SessionLocal() as session, session.begin():
        row = await session.get(db.MMDelegateRow, id)
        if row is None:
            return None
        row.food_preference = change.food_preference
        if change.food_notes is not None:
            row.food_notes = change.food_notes
    return await get_mm_delegate_by_id(id)


async def resolve_current_event_day(
    now: datetime | None = None,
) -> tuple[int, int]:
    """The (event_id, 1-based day) whose date range contains today, so the scanner never
    has to be told which day it is. Days are the conference's local days (see _local_date).
    Raises LookupError if no event's dates cover today (dates unset, or scanning outside
    the conference), for a clear message to the operator.
    """
    today = _local_date(now or datetime.now(timezone.utc))
    async with db.SessionLocal() as session:
        rows = (
            await session.execute(
                select(db.EventRow.id, db.EventRow.starts_at, db.EventRow.ends_at)
                .where(db.EventRow.starts_at.is_not(None), db.EventRow.ends_at.is_not(None))
                .order_by(db.EventRow.id)
            )
        ).all()
    for event_id, starts_at, ends_at in rows:
        first_day = _local_date(starts_at)
        if first_day <= today <= _local_date(ends_at):
            return event_id, (today - first_day).days + 1
    raise LookupError("No event is running today; set the event dates first")


async def resolve_scan_day(now: datetime | None = None) -> tuple[int, int]:
    """The (event_id, day) a meal scan belongs to. Normally the event day that contains
    today. When no event's dates cover today (dates not set yet, or testing before the
    conference) scanning still works: the scan is filed under the first event with the
    calendar date as its day key, so "once per meal per day" holds on any day. Those keys
    are large (a date ordinal), so they can never collide with a real day 1, 2 or 3."""
    try:
        return await resolve_current_event_day(now)
    except LookupError:
        pass
    async with db.SessionLocal() as session:
        event_id = await session.scalar(select(db.EventRow.id).order_by(db.EventRow.id).limit(1))
    if event_id is None:
        raise LookupError("No event exists yet")
    return event_id, _local_date(now or datetime.now(timezone.utc)).toordinal()


async def record_meal_scan(
    event_id: int,
    day: int,
    meal: str,
    delegate_id: str,
    scanned_by: str,
    diet: str | None = None,
) -> models.ScanResult:
    """Record that a delegate collected a meal. Inserts a meal_scans row; if they already
    collected this meal the unique constraint rejects it, and we log the attempt to
    meal_scan_flags and return a `duplicate` result instead. Raises LookupError if the id
    is not a Mumbai MUN delegate."""
    async with db.SessionLocal() as session:
        found = (
            await session.execute(
                select(
                    db.DelegateRow.firstname,
                    db.DelegateRow.lastname,
                    db.MMDelegateRow.food_preference,
                )
                .join(db.MMDelegateRow, db.MMDelegateRow.delegate_id == db.DelegateRow.id)
                .where(db.DelegateRow.id == delegate_id)
            )
        ).first()
        if found is None:
            raise LookupError("Not a Mumbai MUN delegate")
        firstname, lastname, preference = found

        session.add(
            db.MealScanRow(
                event_id=event_id,
                delegate_id=delegate_id,
                day=day,
                meal=meal,
                served_by=scanned_by,
                diet=diet,
            )
        )
        try:
            await session.commit()
            result = "served"
        except IntegrityError:
            await session.rollback()
            session.add(
                db.MealScanFlagRow(
                    event_id=event_id,
                    delegate_id=delegate_id,
                    day=day,
                    meal=meal,
                    scanned_by=scanned_by,
                )
            )
            await session.commit()
            result = "duplicate"

    return models.ScanResult(
        result=result,
        delegate_id=delegate_id,
        name=f"{firstname} {lastname}",
        food_preference=preference,
        day=day,
        meal=meal,
        diet=diet or preference,
    )


async def get_plate_count(event_id: int, day: int, meal: str) -> models.MealCount:
    """The live count of plates served for one meal, broken down by diet."""
    async with db.SessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    func.coalesce(db.MealScanRow.diet, db.MMDelegateRow.food_preference),
                    func.count(),
                )
                .select_from(db.MMDelegateRow)
                .join(
                    db.MealScanRow,
                    db.MealScanRow.delegate_id == db.MMDelegateRow.delegate_id,
                )
                .where(
                    db.MealScanRow.event_id == event_id,
                    db.MealScanRow.day == day,
                    db.MealScanRow.meal == meal,
                )
                .group_by(func.coalesce(db.MealScanRow.diet, db.MMDelegateRow.food_preference))
            )
        ).all()

    by_pref = {preference: count for preference, count in rows}
    return models.MealCount(
        day=day,
        meal=meal,
        total=sum(by_pref.values()),
        veg=by_pref.get("veg", 0),
        non_veg=by_pref.get("non_veg", 0),
        jain=by_pref.get("jain", 0),
        unspecified=by_pref.get(None, 0),
    )


async def get_flagged_scans(event_id: int) -> list[models.FlaggedScan]:
    """Every rejected second-scan for the event, newest first: hospitality's flagged list."""
    async with db.SessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    db.MealScanFlagRow.delegate_id,
                    db.DelegateRow.firstname,
                    db.DelegateRow.lastname,
                    db.MealScanFlagRow.day,
                    db.MealScanFlagRow.meal,
                    db.MealScanFlagRow.scanned_by,
                    db.MealScanFlagRow.created_at,
                )
                .join(db.DelegateRow, db.DelegateRow.id == db.MealScanFlagRow.delegate_id)
                .where(db.MealScanFlagRow.event_id == event_id)
                .order_by(db.MealScanFlagRow.created_at.desc())
            )
        ).all()
    return [
        models.FlaggedScan(
            delegate_id=delegate_id,
            name=f"{firstname} {lastname}",
            day=day,
            meal=meal,
            scanned_by=scanned_by,
            at=created_at,
        )
        for delegate_id, firstname, lastname, day, meal, scanned_by, created_at in rows
    ]


####################
# CHAT: committees and messages (docs/adr/0003)
####################


def _to_committee(row: db.CommitteeRow) -> models.Committee:
    return models.Committee(
        id=row.id, event_id=row.event_id, name=row.name, status=row.status
    )


async def create_committee(event_id: int, new: models.NewCommittee) -> models.Committee:
    """Raises ValueError if the event is missing, IntegrityError on a duplicate name."""
    async with db.SessionLocal() as session, session.begin():
        if await session.get(db.EventRow, event_id) is None:
            raise ValueError("Unknown event")
        row = db.CommitteeRow(event_id=event_id, name=new.name)
        session.add(row)
        await session.flush()
        return _to_committee(row)


async def list_all_committees() -> list[models.Committee]:
    """Every committee across events, in the order they were created (the app's order)."""
    async with db.SessionLocal() as session:
        rows = await session.scalars(select(db.CommitteeRow).order_by(db.CommitteeRow.id))
        return [_to_committee(row) for row in rows]


async def list_committees(event_id: int) -> list[models.Committee]:
    async with db.SessionLocal() as session:
        rows = await session.scalars(
            select(db.CommitteeRow)
            .where(db.CommitteeRow.event_id == event_id)
            .order_by(db.CommitteeRow.name)
        )
        return [_to_committee(row) for row in rows]


async def get_committee(committee_id: int) -> models.Committee | None:
    async with db.SessionLocal() as session:
        row = await session.get(db.CommitteeRow, committee_id)
        return _to_committee(row) if row else None


async def set_committee_status(
    committee_id: int, status: str
) -> models.Committee | None:
    async with db.SessionLocal() as session, session.begin():
        row = await session.get(db.CommitteeRow, committee_id)
        if row is None:
            return None
        row.status = status
        return _to_committee(row)


async def add_chat_message(
    committee_id: int,
    sender_email: str,
    kind: str,
    body: str,
    payload: dict | None,
) -> models.ChatMessage:
    """Persist one message and return it with the sender's display name."""
    async with db.SessionLocal() as session, session.begin():
        name = await session.scalar(
            select(db.DelegateRow.firstname + " " + db.DelegateRow.lastname).where(
                db.DelegateRow.email == sender_email
            )
        )
        row = db.ChatMessageRow(
            committee_id=committee_id,
            sender_email=sender_email,
            kind=kind,
            body=body,
            payload=payload,
        )
        session.add(row)
        await session.flush()
        return models.ChatMessage(
            id=row.id,
            committee_id=row.committee_id,
            sender_email=row.sender_email,
            sender_name=name or sender_email,
            kind=row.kind,
            body=row.body,
            payload=row.payload,
            created_at=row.created_at,
        )


async def get_chat_messages(committee_id: int, limit: int = 50) -> list[models.ChatMessage]:
    """The most recent messages for a committee, returned oldest-first for display."""
    async with db.SessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    db.ChatMessageRow,
                    (db.DelegateRow.firstname + " " + db.DelegateRow.lastname).label("name"),
                )
                .join(
                    db.DelegateRow,
                    db.DelegateRow.email == db.ChatMessageRow.sender_email,
                    isouter=True,
                )
                .where(db.ChatMessageRow.committee_id == committee_id)
                .order_by(db.ChatMessageRow.id.desc())
                .limit(limit)
            )
        ).all()
    messages = [
        models.ChatMessage(
            id=m.id,
            committee_id=m.committee_id,
            sender_email=m.sender_email,
            sender_name=name or m.sender_email,
            kind=m.kind,
            body=m.body,
            payload=m.payload,
            created_at=m.created_at,
        )
        for m, name in rows
    ]
    messages.reverse()
    return messages


####################
# BACKUP
####################


async def backup_database() -> str:
    """Runs pg_dump (custom format, already compressed; restore with pg_restore) into
    BACKUP_DIR and returns the file path. Needs pg_dump 16 or newer on PATH."""
    settings = config.get_settings()
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = os.path.join(BACKUP_DIR, f"mundra-ondemand-{stamp}.dump")

    process = await asyncio.create_subprocess_exec(
        "pg_dump",
        "--format=custom",
        "--file", path,
        "--host", settings.postgres_host,
        "--port", str(settings.postgres_port),
        "--username", settings.postgres_user,
        "--dbname", settings.postgres_db,
        # The password goes in the environment, not argv, so it is not visible in `ps`.
        env={**os.environ, "PGPASSWORD": settings.postgres_password},
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError(f"pg_dump failed: {stderr.decode().strip()}")
    return path


####################
# COMMAND LINE
####################


async def _cli(args: argparse.Namespace) -> int:
    try:
        if args.command == "make-admin":
            old_role = await make_admin(args.email)
            if old_role == "admin":
                print(f"{args.email} is already an admin.")
            else:
                print(f"{args.email}: {old_role} -> admin")
        return 0
    except LookupError as e:
        print(e)
        return 1
    finally:
        await db.engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MUNDRA database commands")
    commands = parser.add_subparsers(dest="command", required=True)
    make_admin_parser = commands.add_parser(
        "make-admin", help="Give an existing user the admin role (first-admin bootstrap)"
    )
    make_admin_parser.add_argument("email")
    raise SystemExit(asyncio.run(_cli(parser.parse_args())))
