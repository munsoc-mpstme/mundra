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
    # An OC member runs the event rather than attending it, so no guides or badge.
    "oc": frozenset(
        {APP_EB_TOOLS, APP_FOOD_SCAN, APP_CHAT_VIEW, APP_CHAT_SEND_REQUEST, APP_CHAT_RESPOND}
    ),
    "admin": frozenset(
        {
            APP_GUIDES_VIEW,
            APP_BADGE_VIEW,
            APP_EB_TOOLS,
            APP_FOOD_SCAN,
            APP_CHAT_VIEW,
            APP_CHAT_SEND_REQUEST,
            APP_CHAT_RESPOND,
            APP_ADMIN_ROLES,
        }
    ),
}

# Roles that hold the OC baseline on the server too: they may scan meals and use break
# coordination without being on a team. (Teams and heads still work as before.)
OC_BASELINE_ROLES = ("oc",)


def app_permissions(role: str, is_head: bool, team_permissions) -> set[str]:
    """The app-facing permission strings for a user: their role's, plus what OC team
    permissions or being a head add on top."""
    out = set(APP_ROLE_PERMISSIONS.get(role, ()))
    team = set(team_permissions)
    if is_head or FOOD_MANAGE_ENTITLEMENT in team:
        out.add(APP_FOOD_SCAN)
    if is_head or CHAT_VIEW in team:
        out.add(APP_CHAT_VIEW)
    if is_head or CHAT_POST in team:
        out.update({APP_CHAT_SEND_REQUEST, APP_CHAT_RESPOND})
    return out

