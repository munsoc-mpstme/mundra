"""The permission verbs the code checks, and the rule that a head holds all of them.

Teams carry permissions as data (the team_permissions table); this module only names
the verbs and combines them. It is pure logic with no database access, so both auth.py
(the request-time checks) and database.py (assembling a user's access) can import it
without a cycle. See docs/adr/0003.
"""

# Food / Hospitality
FOOD_MANAGE_ENTITLEMENT = "food.manage_entitlement"  # toggle a delegate's meal flags
FOOD_VIEW_ARRIVALS = "food.view_arrivals"  # the live "committees inbound" count

# Chat (hospitality <-> rapporteurs)
CHAT_POST = "chat.post"
CHAT_VIEW = "chat.view"

# In-house Logistics (IHL): the team that asks for breaks in a committee's chat. A team
# granted this verb is the IHL team (as food.manage_entitlement marks the food team).
IHL_REQUEST_BREAK = "ihl.request_break"

# Rapporteur, scoped to their own committee
RAPPORTEUR_VIEW_OWN_ROSTER = "rapporteur.view_own_roster"
RAPPORTEUR_SET_COMMITTEE_STATUS = "rapporteur.set_committee_status"

# Registration desk
REGISTRATION_MANUAL_VERIFY = "registration.manual_verify"

# Team administration
TEAM_MANAGE_ROSTER = "team.manage_roster"  # a lead: own team; a head: any team
TEAM_MANAGE_DEFINITION = "team.manage_definition"  # create/edit teams (heads only)

# Event-wide read (heads)
EVENT_VIEW_ALL = "event.view_all"

# Every verb the system knows. A team may only be granted a permission from this set,
# and a head is given exactly this set. Keep it in sync with the constants above.
ALL_PERMISSIONS = frozenset(
    {
        FOOD_MANAGE_ENTITLEMENT,
        FOOD_VIEW_ARRIVALS,
        CHAT_POST,
        CHAT_VIEW,
        IHL_REQUEST_BREAK,
        RAPPORTEUR_VIEW_OWN_ROSTER,
        RAPPORTEUR_SET_COMMITTEE_STATUS,
        REGISTRATION_MANUAL_VERIFY,
        TEAM_MANAGE_ROSTER,
        TEAM_MANAGE_DEFINITION,
        EVENT_VIEW_ALL,
    }
)

LEVELS = ("member", "lead")


def can_act_on_committee(is_head, memberships, permission: str, committee_name: str) -> bool:
    """Whether a user may use a committee-scoped permission for a given committee.

    A head may act on any committee. Otherwise the permission must come from one of their
    memberships: an unscoped membership (committee is None, e.g. hospitality) reaches every
    committee, while a committee-scoped one (a rapporteur) reaches only its own. `memberships`
    are the models.Membership objects from database.get_effective_access.
    """
    if is_head:
        return True
    for m in memberships:
        if permission in m.permissions and (m.committee is None or m.committee == committee_name):
            return True
    return False

# ---------------------------------------------------------------------------------------
# What the Delego app is told it may do.
#
# The app gates its screens on the strings below (GET /delegates/me -> permissions) and
# never on role names. They are derived, not stored: from the user's role, plus the OC
# permissions above (so a team member or head also gets what their team grants). The
# server still enforces the real checks on every route; this only decides what the app
# shows.
# ---------------------------------------------------------------------------------------

APP_GUIDES_VIEW = "guides.view"  # study guides
APP_BADGE_VIEW = "badge.view"  # own food QR badge
APP_EB_TOOLS = "eb.tools"  # GSL list, session timer
APP_FOOD_SCAN = "food.scan"  # meal scanner and plate counts
APP_CHAT_VIEW = "chat.view"  # read break coordination
APP_CHAT_SEND_REQUEST = "chat.send_request"  # ask for a break, say you are late
APP_CHAT_RESPOND = "chat.respond"  # accept or reject a break request
APP_ADMIN_ROLES = "admin.roles"  # change other users' roles

APP_ROLE_PERMISSIONS = {
    "delegate": frozenset({APP_GUIDES_VIEW, APP_BADGE_VIEW}),
    "eb": frozenset({APP_GUIDES_VIEW, APP_BADGE_VIEW, APP_EB_TOOLS}),
    # An OC member runs the event rather than attending it, so no guides. EB Tools is for
    # the EB (and admins). Two things are NOT in this base set, and are added only by team
    # (see app_permissions): meal scanning (OC on the food team) and requesting a break
    # (OC on the IHL team). So the OC role alone can do neither.
    "oc": frozenset({APP_BADGE_VIEW, APP_CHAT_VIEW}),
    "admin": frozenset(
        {
            APP_GUIDES_VIEW,
            APP_BADGE_VIEW,
            APP_EB_TOOLS,
            APP_FOOD_SCAN,
            APP_CHAT_VIEW,
            # Admin answers break requests (accept/reject) but does not make them: only the
            # IHL team requests.
            APP_CHAT_RESPOND,
            APP_ADMIN_ROLES,
        }
    ),
}

# Who may change a committee's floor (EB Tools: speakers' lists and timer): only the EB and
# admin roles (plus heads). Any committee: each EB picks their own in the app.
FLOOR_ROLES = ("eb", "admin")


def can_run_floor(role: str, is_head: bool) -> bool:
    return role in FLOOR_ROLES or is_head


# Roles that hold the OC baseline on the server for break coordination: they may read and
# post in every committee's chat without being on a team. Meal scanning is NOT part of this
# baseline (an OC member must also be on the food team to scan).
OC_BASELINE_ROLES = ("oc",)


# The break-coordination quick actions. Requesting is one side of the conversation,
# answering the other: the IHL team asks, an admin answers. Nobody else may do either.
# The requesting side: ask for a break, say it is running late, and answer an offered
# time (agree to it, or decline with a time of their own).
BREAK_REQUEST_ACTIONS = ("free", "late", "agree", "decline")
BREAK_RESPONSE_ACTIONS = ("accept", "reject")


def on_ihl_team(memberships, committee_name: str | None = None) -> bool:
    """Whether the user has an IHL team membership. With a committee name, the membership
    must reach that committee: unscoped (every committee) or scoped to that one."""
    return any(
        IHL_REQUEST_BREAK in m.permissions
        and (committee_name is None or m.committee is None or m.committee == committee_name)
        for m in memberships
    )


def can_request_break(role: str, memberships, committee_name: str | None = None) -> bool:
    """Request a break (free, late, agree, decline): an OC-role member who is also on the
    IHL team. Both are required. Nobody else, not an admin or a head."""
    return role == "oc" and on_ihl_team(memberships, committee_name)


def can_answer_break(role: str) -> bool:
    """Accept or reject a break request: admins only. Nobody else, not a head or any team."""
    return role == "admin"


def app_permissions(role: str, is_head: bool, team_permissions, memberships=()) -> set[str]:
    """The app-facing permission strings for a user: their role's, plus what OC team
    permissions or being a head add on top. `memberships` are the user's
    models.Membership objects. Requesting a break needs the OC role and the IHL team;
    answering one is for admins only (in their role set)."""
    out = set(APP_ROLE_PERMISSIONS.get(role, ()))
    team = set(team_permissions)
    if is_head:
        out.add(APP_CHAT_VIEW)
    if can_request_break(role, memberships):
        out.add(APP_CHAT_SEND_REQUEST)
    # Meal scanning: admins always (their role set), otherwise only an OC-role member who is
    # also on the food team (a team membership granting food.manage_entitlement). Food-team
    # membership alone, or the OC role alone, or a head, does not grant it.
    on_food_team = any(FOOD_MANAGE_ENTITLEMENT in m.permissions for m in memberships)
    if role == "oc" and on_food_team:
        out.add(APP_FOOD_SCAN)
    if CHAT_VIEW in team:
        out.add(APP_CHAT_VIEW)
    return out


def can_use_break_action(
    role: str, is_head: bool, memberships, action: str, committee_name: str
) -> bool:
    """Whether a user may send this quick action in this committee's chat. Same rule the
    app uses to show the buttons: requesting (free, late, agree, decline) is for an OC
    member on the IHL team (reaching this committee); accepting or rejecting is for admins
    only. Nobody else may do either, including heads."""
    if action in BREAK_REQUEST_ACTIONS:
        return can_request_break(role, memberships, committee_name)
    if action in BREAK_RESPONSE_ACTIONS:
        return can_answer_break(role)
    return True  # not a break action: the ordinary chat.post check applies

