from datetime import datetime
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    EmailStr,
    field_validator,
    model_validator,
)

Role = Literal["delegate", "eb", "oc", "admin"]


class Token(BaseModel):
    access_token: str
    token_type: str
    user_type: str

class ErrorResponse(BaseModel):
    error: str


class MunExperience(BaseModel):
    name: str
    committee: str = ""
    delegation: str = ""
    year: int
    award: str = ""


class newDelegate(BaseModel):
    firstname: str
    lastname: str
    email: EmailStr
    backup_email: str = ""
    contact: str = ""
    dateofbirth: str = ""
    gender: str = ""
    pastmuns: list[MunExperience] = []
    verified: bool = False


class Delegate(newDelegate):
    id: str


class AuthUser(Delegate):
    """The authenticated caller: their delegate profile plus their role."""

    role: Role = "delegate"


class RoleChange(BaseModel):
    role: Role


class VerifyEmail(BaseModel):
    email: EmailStr
    code: str


# ORGANIZING COMMITTEE (docs/adr/0003)


Level = Literal["member", "lead"]


class Membership(BaseModel):
    """One of a user's OC team memberships, as returned to the app."""

    team: str
    committee: str | None = None
    level: Level = "member"
    permissions: list[str] = []


class Me(AuthUser):
    """The /delegates/me response: the caller's profile and role, plus their OC access.
    A superset of AuthUser, so the extra fields are additive for older app installs."""

    is_head: bool = False
    permissions: list[str] = []
    teams: list[Membership] = []


class Event(BaseModel):
    id: int
    name: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class EventDates(BaseModel):
    # An offset is required (e.g. 2026-10-30T00:00:00+05:30): the days are counted in the
    # conference's local time, so a bare date-time would be ambiguous.
    starts_at: AwareDatetime
    ends_at: AwareDatetime

    @model_validator(mode="after")
    def _ends_after_it_starts(self):
        if self.ends_at < self.starts_at:
            raise ValueError("ends_at must not be before starts_at")
        return self


class NewTeam(BaseModel):
    name: str
    description: str = ""
    permissions: list[str] = []


class TeamPermissionsChange(BaseModel):
    permissions: list[str]


class Team(BaseModel):
    id: int
    event_id: int
    name: str
    description: str = ""
    permissions: list[str] = []


class RosterAdd(BaseModel):
    email: EmailStr
    committee: str | None = None
    level: Level = "member"


class RosterEntry(BaseModel):
    """One person on a team's roster. `status` is `member` once they have an account, or
    `invited` while they are only a rostered email."""

    email: EmailStr
    name: str | None = None
    committee: str | None = None
    level: Level = "member"
    status: Literal["member", "invited"]


class HeadAdd(BaseModel):
    email: EmailStr


# CHAT (docs/adr/0003)


CommitteeStatus = Literal["in_session", "adjourned"]
ChatKind = Literal["text", "status"]


class NewCommittee(BaseModel):
    name: str


class Committee(BaseModel):
    id: int
    event_id: int
    name: str
    status: CommitteeStatus = "in_session"


class CommitteeStatusChange(BaseModel):
    status: CommitteeStatus


# The quick actions the Delego app sends for break coordination. They are stored as a
# `status` message whose payload is {"type": <one of these>}.
BreakAction = Literal["free", "late", "accept", "reject"]


class NewChatMessage(BaseModel):
    kind: ChatKind = "text"
    body: str = ""
    payload: dict | None = None
    # Shorthand used by the app: {"type": "free"} means a status message carrying that
    # quick action, with a standard text if no body is given.
    type: BreakAction | None = None


class ChatMessage(BaseModel):
    id: int
    committee_id: int
    sender_email: EmailStr
    sender_name: str
    kind: ChatKind
    body: str = ""
    payload: dict | None = None
    created_at: datetime
    # Aliases the Delego app reads: `sender` is sender_email, and `type` is the quick
    # action for a status message (free, late, accept, reject), otherwise the kind.
    sender: str = ""
    type: str = "text"

    @model_validator(mode="after")
    def _fill_app_fields(self):
        self.sender = self.sender_email
        action = (self.payload or {}).get("type") if self.kind == "status" else None
        self.type = action if isinstance(action, str) and action else self.kind
        return self


class User(BaseModel):
    firstname: str
    lastname: str
    email: EmailStr
    backup_email: str = ""
    password: str

    @field_validator("password")
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        return v


class ResetPassword(BaseModel):
    password: str

    @field_validator("password")
    def validate_password(cls, v):
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters long")
        return v


# MUMBAI MUN STUFF


Meal = Literal["breakfast", "lunch", "hitea"]
FoodPreference = Literal["veg", "non_veg", "jain"]


class MMDelegate(Delegate):
    country: str = ""
    committee: str = ""
    food_preference: FoodPreference | None = None
    food_notes: str = ""


class FoodPreferenceChange(BaseModel):
    food_preference: FoodPreference | None = None
    food_notes: str | None = None  # None leaves the existing note unchanged


class ScanResult(BaseModel):
    """The outcome of scanning a delegate's QR at a meal. `served` means the plate is
    theirs; `duplicate` means they already collected this meal and are flagged."""

    result: Literal["served", "duplicate"]
    delegate_id: str
    name: str
    food_preference: FoodPreference | None = None
    day: int
    meal: Meal
    # The diet the operator served (falls back to the delegate's registered preference).
    diet: str | None = None


class MealCount(BaseModel):
    """The live plate count for one meal on one day, broken down by diet."""

    day: int
    meal: Meal
    total: int = 0
    veg: int = 0
    non_veg: int = 0
    jain: int = 0
    unspecified: int = 0


class FlaggedScan(BaseModel):
    """A logged second-scan attempt, for hospitality's flagged list."""

    delegate_id: str
    name: str
    day: int
    meal: Meal
    scanned_by: str
    at: datetime
