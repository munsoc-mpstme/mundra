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
