import re
import unicodedata
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
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


FoodPreference = Literal["veg", "non_veg", "jain"]


class Me(AuthUser):
    """The /delegates/me response: the caller's profile and role, plus their OC access.
    A superset of AuthUser, so the extra fields are additive for older app installs."""

    is_head: bool = False
    permissions: list[str] = []
    teams: list[Membership] = []
    food_preference: FoodPreference | None = None


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
    contact: str = ""
    dateofbirth: str = ""
    food_preference: FoodPreference | None = None
    password: str

    @field_validator("food_preference", mode="before")
    def normalize_food_preference(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            cleaned = v.strip().lower().replace("-", "_").replace(" ", "_")
            if not cleaned:
                return None
            if cleaned == "nonveg":
                return "non_veg"
            return cleaned
        return v

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


class Badge(BaseModel):
    """A delegate's own food badge: the string their QR encodes, and what it says."""

    qr: str
    delegate_id: str
    name: str
    food_preference: FoodPreference | None = None


class MealWindows(BaseModel):
    """When each meal starts, conference-local HH:MM."""

    breakfast: str = "00:00"
    lunch: str
    high_tea: str


class ScanContext(BaseModel):
    """What a scanner needs so the operator never picks the day or meal: today's event
    day and current meal, plus the dates and meal windows to work them out offline."""

    event_id: int | None = None
    # False when no event's dates cover today: scans still work, but are filed under the
    # calendar date instead of day 1, 2 or 3. A head should set the event dates.
    event_dates_set: bool
    day: int | None = None
    meal: Meal
    meal_windows: MealWindows
    event_dates: list[date] = []
    utc_offset_minutes: int
    server_time: datetime


class BadgeLookup(BaseModel):
    """A scanned badge, before (or instead of) serving a plate: who it is, what they eat,
    and whether they already had the current meal."""

    delegate_id: str
    name: str
    food_preference: FoodPreference | None = None
    food_notes: str = ""
    committee: str = ""
    day: int
    meal: Meal
    collected_today: list[Meal] = []
    already_collected: bool


class DelegateMatch(BaseModel):
    """A name-search result, for serving a delegate who cannot show their QR."""

    delegate_id: str
    name: str
    email_hint: str
    committee: str = ""
    country: str = ""
    food_preference: FoodPreference | None = None


class FlaggedScan(BaseModel):
    """A logged second-scan attempt, for hospitality's flagged list."""

    delegate_id: str
    name: str
    day: int
    meal: Meal
    scanned_by: str
    at: datetime


# --- Committee floor (EB Tools: speakers' lists and timer, see floor.py) ----------------

FloorList = Literal["gsl", "mod"]
FloorView = Literal["gsl", "mod", "unmod"]
FloorSeconds = Annotated[int, Field(ge=5, le=5999)]  # up to 99:59
_FLAG_CODE = re.compile(r"^[a-z]{2}(-[a-z]{2,4})?$")  # ISO 3166 alpha-2, or e.g. gb-sct


class _FloorOp(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FloorAdd(_FloorOp):
    """Add a portfolio to the end of a list. code is its flag (ISO alpha-2, lower case);
    null for a person, who is shown with a person icon."""

    op: Literal["add"]
    list: FloorList
    name: str
    code: str | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if any(unicodedata.category(ch) in ("Cc", "Cs") for ch in value):
            raise ValueError("The name has control characters")
        value = " ".join(value.split())
        if not 1 <= len(value) <= 80:
            raise ValueError("The name must be 1 to 80 characters")
        return value

    @field_validator("code")
    @classmethod
    def check_code(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        value = value.lower()
        if not _FLAG_CODE.match(value):
            raise ValueError("Not a flag code")
        return value


class FloorRemove(_FloorOp):
    op: Literal["remove"]
    list: FloorList
    entry_id: str = Field(max_length=32)


class FloorMove(_FloorOp):
    """Move an entry so it ends up at to_index (counted after it is taken out)."""

    op: Literal["move"]
    list: FloorList
    entry_id: str = Field(max_length=32)
    to_index: int = Field(ge=0)


class FloorClear(_FloorOp):
    op: Literal["clear"]
    list: FloorList


class FloorSetSpeakerTime(_FloorOp):
    op: Literal["set_speaker_time"]
    list: FloorList
    seconds: FloorSeconds


class FloorStartSpeaker(_FloorOp):
    """Time the current speaker of a list. replace=True stops another running timer."""

    op: Literal["start_speaker"]
    list: FloorList
    replace: bool = False


class FloorStartUnmod(_FloorOp):
    op: Literal["start_unmod"]
    seconds: FloorSeconds
    replace: bool = False


class FloorPause(_FloorOp):
    op: Literal["pause"]


class FloorResume(_FloorOp):
    op: Literal["resume"]


class FloorReset(_FloorOp):
    op: Literal["reset"]


class FloorNext(_FloorOp):
    """The speaker the EB named is done (finished early or yielded): take them off the
    list. 409 if they are already gone (their time ran out, or the other EB moved on)."""

    op: Literal["next"]
    list: FloorList
    entry_id: str = Field(max_length=32)


class FloorSetDisplay(_FloorOp):
    op: Literal["set_display"]
    view: FloorView


FloorOp = Annotated[
    FloorAdd
    | FloorRemove
    | FloorMove
    | FloorClear
    | FloorSetSpeakerTime
    | FloorStartSpeaker
    | FloorStartUnmod
    | FloorPause
    | FloorResume
    | FloorReset
    | FloorNext
    | FloorSetDisplay,
    Field(discriminator="op"),
]


class CommitteePortfolio(BaseModel):
    """One portfolio of a committee's matrix. code is the flag (ISO alpha-2); a person
    (PSC member) has none but a party (detail) and its abbreviation (tag)."""

    name: str
    code: str | None = None
    tag: str | None = None
    detail: str | None = None


class FloorFrame(BaseModel):
    """The whole floor, as pushed over the socket and returned by every op. state is
    floor.new_state()'s shape; server_now (epoch ms) lets clients correct their clocks."""

    type: Literal["state"] = "state"
    state: dict
    server_now: int
