"""Async SQLAlchemy engine, session factory and ORM table classes.

Pydantic request/response shapes live in models.py; query functions live in
database.py. This file only describes the storage.
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

import config
import permissions

ROLES = ("delegate", "oc", "admin")

# The three meals served on each conference day, and the diet a plate is prepared for.
MEALS = ("breakfast", "lunch", "hitea")
FOOD_PREFERENCES = ("veg", "non_veg", "jain")

# A committee is either meeting or broken for a meal; a chat message is free text or a
# structured quick-action (the "we're free" / "running late" buttons).
COMMITTEE_STATUSES = ("in_session", "adjourned")
CHAT_KINDS = ("text", "status")

# Repeated in check constraints.
_LEVEL_CHECK = f"level IN ({', '.join(repr(l) for l in permissions.LEVELS)})"
_MEAL_CHECK = f"meal IN ({', '.join(repr(m) for m in MEALS)})"
_FOOD_PREFERENCE_CHECK = (
    f"food_preference IN ({', '.join(repr(p) for p in FOOD_PREFERENCES)})"
)
_STATUS_CHECK = f"status IN ({', '.join(repr(s) for s in COMMITTEE_STATUSES)})"
_KIND_CHECK = f"kind IN ({', '.join(repr(k) for k in CHAT_KINDS)})"

# Deterministic constraint names, so Alembic can drop and alter them later.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

_settings = config.get_settings()
engine = create_async_engine(
    _settings.database_url,
    pool_size=10,
    max_overflow=10,
    pool_pre_ping=True,
    connect_args=_settings.db_connect_args,
)

# expire_on_commit=False: attributes stay readable after commit, which async
# sessions need because they cannot lazy-load.
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class DelegateRow(Base):
    __tablename__ = "delegates"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    firstname: Mapped[str]
    lastname: Mapped[str]
    email: Mapped[str] = mapped_column(unique=True)
    # Optional secondary email; verification and reset mails are also sent here.
    backup_email: Mapped[str] = mapped_column(server_default="")
    contact: Mapped[str] = mapped_column(server_default="")
    dateofbirth: Mapped[str] = mapped_column(server_default="")
    gender: Mapped[str] = mapped_column(server_default="")
    verified: Mapped[bool] = mapped_column(server_default=text("false"))
    created_at: Mapped[datetime] = _created_at()

    # lazy="raise": async sessions cannot lazy-load, so every query that needs these
    # must ask for them explicitly (selectinload / contains_eager).
    experiences: Mapped[list["MunExperienceRow"]] = relationship(
        order_by="MunExperienceRow.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="raise",
    )
    mm: Mapped["MMDelegateRow | None"] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )


class UserRow(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            f"role IN ({', '.join(repr(r) for r in ROLES)})", name="role_valid"
        ),
    )

    email: Mapped[str] = mapped_column(
        ForeignKey("delegates.email", onupdate="CASCADE", ondelete="CASCADE"),
        primary_key=True,
    )
    password: Mapped[str]
    role: Mapped[str] = mapped_column(server_default="delegate")
    created_at: Mapped[datetime] = _created_at()


class EmailVerificationRow(Base):
    """A pending 6-digit email verification code: one per email, replaced on resend and
    deleted once used. Short-lived and attempt-capped (see config), so the plain code is
    stored directly rather than hashed."""

    __tablename__ = "email_verifications"

    email: Mapped[str] = mapped_column(
        ForeignKey("delegates.email", onupdate="CASCADE", ondelete="CASCADE"),
        primary_key=True,
    )
    code: Mapped[str]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    created_at: Mapped[datetime] = _created_at()


class MunExperienceRow(Base):
    __tablename__ = "mun_experiences"

    id: Mapped[int] = mapped_column(primary_key=True)
    delegate_id: Mapped[str] = mapped_column(
        ForeignKey("delegates.id", ondelete="CASCADE"), index=True
    )
    # Keeps the order the delegate entered their experiences in.
    position: Mapped[int]
    name: Mapped[str]
    committee: Mapped[str] = mapped_column(server_default="")
    delegation: Mapped[str] = mapped_column(server_default="")
    year: Mapped[int]
    award: Mapped[str] = mapped_column(server_default="")


class MMDelegateRow(Base):
    """Mumbai MUN details for a delegate. The delegate's profile lives in delegates.

    Meals are tracked by collection (meal_scans), not per-day eligibility, so this row no
    longer carries per-meal flags (docs/adr/0003). food_preference is the diet a plate is
    prepared for; food_notes is free text (allergies) that hospitality reads by hand."""

    __tablename__ = "mm_delegates"
    __table_args__ = (
        CheckConstraint(_FOOD_PREFERENCE_CHECK, name="food_preference_valid"),
    )

    delegate_id: Mapped[str] = mapped_column(
        ForeignKey("delegates.id", ondelete="CASCADE"), primary_key=True
    )
    country: Mapped[str] = mapped_column(server_default="")
    committee: Mapped[str] = mapped_column(server_default="")
    food_preference: Mapped[str | None]
    food_notes: Mapped[str] = mapped_column(server_default="")
    created_at: Mapped[datetime] = _created_at()


class AdminAuditRow(Base):
    """One row per role change. Emails are stored as plain text, not foreign keys,
    so the trail survives a deleted account."""

    __tablename__ = "admin_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_email: Mapped[str]
    target_email: Mapped[str]
    old_role: Mapped[str]
    new_role: Mapped[str]
    created_at: Mapped[datetime] = _created_at()


####################
# ORGANIZING COMMITTEE (docs/adr/0003)
# Teams and their permissions are data, scoped to an event; a membership grants a person
# a team's permissions until it lapses. The permission verbs live in permissions.py.
####################


class EventRow(Base):
    """One conference. Teams, memberships and (later) committees all belong to an event.
    starts_at/ends_at are nullable so an event can be created before its dates are set."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class TeamRow(Base):
    """An OC team a head defines from the app (Hospitality, Rapporteur, ...). Its powers
    are the team_permissions rows that point at it, not anything hardcoded here."""

    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("event_id", "name", name="event_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    description: Mapped[str] = mapped_column(server_default="")
    created_at: Mapped[datetime] = _created_at()

    permissions: Mapped[list["TeamPermissionRow"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )


class TeamPermissionRow(Base):
    """One permission verb granted to one team. The verb must be one of
    permissions.ALL_PERMISSIONS; that is enforced in the query layer, not by the DB."""

    __tablename__ = "team_permissions"
    __table_args__ = (
        UniqueConstraint("team_id", "permission", name="team_permission"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"), index=True
    )
    permission: Mapped[str]


class EventHeadRow(Base):
    """A head: every permission for every team of this event. Separate from the system
    admin role (ADR 0002), which is about server access, not running the event."""

    __tablename__ = "event_heads"
    __table_args__ = (
        UniqueConstraint("event_id", "user_email", name="event_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    user_email: Mapped[str] = mapped_column(
        ForeignKey("users.email", onupdate="CASCADE", ondelete="CASCADE"), index=True
    )
    granted_by: Mapped[str]
    created_at: Mapped[datetime] = _created_at()


class MembershipRow(Base):
    """One person in one team for one event. committee is set only for teams that work
    per committee (rapporteurs). ends_at defaults to the event's end at creation time, so
    access lapses on its own; a null ends_at means no expiry."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("event_id", "user_email", "team_id", name="event_user_team"),
        CheckConstraint(_LEVEL_CHECK, name="level_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    user_email: Mapped[str] = mapped_column(
        ForeignKey("users.email", onupdate="CASCADE", ondelete="CASCADE"), index=True
    )
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    committee: Mapped[str | None]
    level: Mapped[str] = mapped_column(server_default="member")
    starts_at: Mapped[datetime] = _created_at()
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class TeamInviteRow(Base):
    """A rostered email that is not a member yet. When this email registers and verifies,
    the invite becomes a membership (database.apply_pending_invites). Keyed by email
    because the account may not exist yet, so it is not a foreign key to users."""

    __tablename__ = "team_invites"
    __table_args__ = (
        UniqueConstraint("email", "team_id", name="email_team"),
        CheckConstraint(_LEVEL_CHECK, name="level_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(index=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"))
    committee: Mapped[str | None]
    level: Mapped[str] = mapped_column(server_default="member")
    created_at: Mapped[datetime] = _created_at()


class MembershipAuditRow(Base):
    """One row per roster change. Like admin_audit, emails and the team name are plain
    text, not foreign keys, so the trail survives a deleted account or team."""

    __tablename__ = "membership_audit"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor_email: Mapped[str]
    target_email: Mapped[str]
    team_name: Mapped[str]
    action: Mapped[str]  # grant | revoke | level_change
    created_at: Mapped[datetime] = _created_at()


####################
# FOOD: meal collection (docs/adr/0003)
# One plate per delegate per meal per day. A scan inserts a meal_scans row; the unique
# constraint makes a second scan for the same meal fail, and that failure is logged to
# meal_scan_flags so hospitality can see who tried for seconds.
####################


class MealScanRow(Base):
    """A delegate collected one meal on one day. day is 1-based within the event. The
    unique constraint is the "no seconds" rule."""

    __tablename__ = "meal_scans"
    __table_args__ = (
        UniqueConstraint("event_id", "delegate_id", "day", "meal", name="delegate_day_meal"),
        CheckConstraint(_MEAL_CHECK, name="meal_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    delegate_id: Mapped[str] = mapped_column(
        ForeignKey("delegates.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[int]
    meal: Mapped[str]
    served_by: Mapped[str]  # email of the OC member who scanned
    created_at: Mapped[datetime] = _created_at()


class MealScanFlagRow(Base):
    """A rejected second scan: the delegate had already collected this meal. This is
    hospitality's "flagged" list; it never blocks anything by itself."""

    __tablename__ = "meal_scan_flags"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    delegate_id: Mapped[str] = mapped_column(
        ForeignKey("delegates.id", ondelete="CASCADE"), index=True
    )
    day: Mapped[int]
    meal: Mapped[str]
    scanned_by: Mapped[str]
    created_at: Mapped[datetime] = _created_at()


####################
# CHAT: committees and their hospitality<->rapporteur channel (docs/adr/0003)
# A committee has one channel; its membership is computed (that committee's rapporteurs +
# all hospitality + heads), never stored. Messages are the durable record; the live
# fan-out is in-process (chat.py).
####################


class CommitteeRow(Base):
    """An MUN committee delegates sit in (UNSC, ...). status is what a rapporteur toggles
    when the committee breaks for a meal."""

    __tablename__ = "committees"
    __table_args__ = (
        UniqueConstraint("event_id", "name", name="event_committee_name"),
        CheckConstraint(_STATUS_CHECK, name="status_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str]
    status: Mapped[str] = mapped_column(server_default="in_session")
    created_at: Mapped[datetime] = _created_at()


class ChatMessageRow(Base):
    """One message in a committee's channel. kind='status' carries a quick-action in
    payload (e.g. {"type": "late", "minutes": 5}); kind='text' is a plain message."""

    __tablename__ = "chat_messages"
    __table_args__ = (CheckConstraint(_KIND_CHECK, name="kind_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    committee_id: Mapped[int] = mapped_column(
        ForeignKey("committees.id", ondelete="CASCADE"), index=True
    )
    sender_email: Mapped[str]
    kind: Mapped[str] = mapped_column(server_default="text")
    body: Mapped[str] = mapped_column(server_default="")
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _created_at()
