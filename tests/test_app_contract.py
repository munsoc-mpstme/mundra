"""The Delego app's contract: exactly the requests and responses the shipped app uses.

These replay what lib/auth/capabilities.dart, lib/api/scan_queue.dart,
lib/Pages/Qr_Page/Qr_scanner.dart and lib/Pages/Chat_Page/committee_chat_page.dart send,
so the backend and the app stay compatible.
"""

import asyncio
from datetime import datetime, timezone

import pytest

import auth
import permissions
from helpers import auth_header, create_user
from test_food import make_mm


def make(**kwargs):
    return asyncio.run(create_user(**kwargs))


# ---- /delegates/me: which screens each role gets ---------------------------------------

EXPECTED = {
    "delegate": {"guides.view", "badge.view"},
    "eb": {"guides.view", "badge.view", "eb.tools"},
    "oc": {"eb.tools", "food.scan", "chat.view", "chat.send_request"},
    "admin": {
        "guides.view", "badge.view", "eb.tools", "food.scan",
        "chat.view", "chat.send_request", "chat.respond", "admin.roles",
    },
}


@pytest.mark.parametrize("role", list(EXPECTED))
def test_me_gives_each_role_its_app_screens(client, role):
    email = make(role=role)
    me = client.get("/delegates/me", headers=auth_header(email)).json()
    assert me["role"] == role
    assert set(me["permissions"]) == EXPECTED[role]


def test_a_team_member_also_gets_the_matching_app_permissions(client):
    from test_teams import add_membership, make_event, make_team

    email = make()
    event = make_event()
    team = make_team(event, "Hosp", perms=[permissions.CHAT_VIEW, permissions.CHAT_POST])
    add_membership(email, event, team)
    perms = set(client.get("/delegates/me", headers=auth_header(email)).json()["permissions"])
    # An unscoped team with chat.post (hospitality) answers requests; it does not make them.
    assert {"chat.view", "chat.respond"} <= perms
    assert "chat.send_request" not in perms
    assert "food.scan" not in perms and "admin.roles" not in perms

    # A committee-scoped team with chat.post (a rapporteur) makes requests for its committee.
    rapporteur = make()
    scoped = make_team(event, "Rapporteurs-ct", perms=[permissions.CHAT_VIEW, permissions.CHAT_POST])
    add_membership(rapporteur, event, scoped, committee="UNSC")
    perms = set(
        client.get("/delegates/me", headers=auth_header(rapporteur)).json()["permissions"]
    )
    assert "chat.send_request" in perms and "chat.respond" not in perms


# ---- meal scanning, as scan_queue.dart sends it ----------------------------------------


def scan(client, who, delegate_id, meal="breakfast", diet="veg"):
    # Form-encoded, with the scanned_at field the app always sends (the phone's UTC time).
    return client.post(
        "/food/scans",
        data={
            "delegate_id": delegate_id,
            "meal": meal,
            "diet": diet,
            "scanned_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        headers=auth_header(who),
    )


def test_oc_scans_high_tea_with_a_diet_and_no_event_dates(client):
    oc = make(role="oc")
    _, delegate_id = make_mm("veg")

    res = scan(client, oc, delegate_id, meal="high_tea", diet="jain")

    assert res.status_code == 200
    body = res.json()
    assert body["result"] == "served"
    assert body["delegate_id"] == delegate_id
    assert body["name"]
    assert body["diet"] == "jain"


def test_second_scan_of_the_same_meal_is_a_duplicate_but_another_meal_serves(client):
    oc = make(role="oc")
    _, delegate_id = make_mm()

    assert scan(client, oc, delegate_id, "lunch").json()["result"] == "served"
    again = scan(client, oc, delegate_id, "lunch")
    assert again.status_code == 200 and again.json()["result"] == "duplicate"
    assert scan(client, oc, delegate_id, "high_tea").json()["result"] == "served"


def test_scan_rejects_bad_input_and_the_wrong_people(client):
    oc, delegate_role, eb = make(role="oc"), make(), make(role="eb")
    _, delegate_id = make_mm()

    assert scan(client, oc, delegate_id, meal="supper").status_code == 422
    assert scan(client, oc, delegate_id, diet="carnivore").status_code == 422
    assert scan(client, oc, "no-such-id").status_code == 404
    assert scan(client, delegate_role, delegate_id).status_code == 403
    assert scan(client, eb, delegate_id).status_code == 403
    assert client.post("/food/scans", data={"delegate_id": "x", "meal": "lunch"}).status_code == 401


def test_the_flagged_list_works_before_the_event_dates_are_set(client):
    oc, admin = make(role="oc"), make(role="admin")
    _, delegate_id = make_mm()
    scan(client, oc, delegate_id, "lunch")
    scan(client, oc, delegate_id, "lunch")  # the second one is flagged

    res = client.get("/food/flags", headers=auth_header(admin))

    assert res.status_code == 200
    assert any(f["delegate_id"] == delegate_id and f["meal"] == "lunch" for f in res.json())


def test_plate_count_follows_the_diet_the_operator_picked(client):
    oc = make(role="oc")
    _, registered_veg = make_mm("veg")
    _, registered_none = make_mm(None)

    def counts():
        res = client.get("/food/plate_count?meal=high_tea", headers=auth_header(oc))
        assert res.status_code == 200
        return res.json()

    before = counts()
    scan(client, oc, registered_veg, "high_tea", diet="jain")  # picked jain, registered veg
    scan(client, oc, registered_none, "high_tea", diet="veg")
    after = counts()

    assert after["jain"] == before["jain"] + 1
    assert after["veg"] == before["veg"] + 1
    assert after["total"] == before["total"] + 2


def test_plate_count_is_for_scanners_only(client):
    assert client.get(
        "/food/plate_count?meal=lunch", headers=auth_header(make())
    ).status_code == 403
    assert client.get(
        "/food/plate_count?meal=brunch", headers=auth_header(make(role="oc"))
    ).status_code == 422


def test_flagged_scans_stay_team_or_head_only(client):
    assert client.get("/food/flags", headers=auth_header(make(role="oc"))).status_code == 403


# ---- break coordination, as committee_chat_page.dart uses it ---------------------------

COMMITTEES = ["UNSC", "CCC", "PSC", "WTO", "UNODC", "UNICEF", "ECOSOC", "IPC"]


def test_committee_list_for_oc_and_admin_only(client):
    for role in ("oc", "admin"):
        res = client.get("/committees", headers=auth_header(make(role=role)))
        assert res.status_code == 200
        names = [c["name"] for c in res.json()]
        # The migration seeds these in this order; other tests may add more after them.
        assert names[:8] == COMMITTEES
        assert all({"id", "name"} <= set(c) for c in res.json())
    for role in ("delegate", "eb"):
        assert client.get("/committees", headers=auth_header(make(role=role))).status_code == 403
    assert client.get("/committees").status_code == 401


def committee_id(client, who, name="UNSC"):
    return next(
        c["id"]
        for c in client.get("/committees", headers=auth_header(who)).json()
        if c["name"] == name
    )


def post(client, who, cid, **body):
    return client.post(f"/committees/{cid}/messages", json=body, headers=auth_header(who))


def hospitality_member():
    """A plain delegate-role user on a Hospitality team (scanner + answers requests)."""
    from test_teams import add_membership, make_event, make_team

    email = make()
    event = make_event()
    team = make_team(
        event,
        "Hospitality-" + email[:8],
        perms=[permissions.FOOD_MANAGE_ENTITLEMENT, permissions.CHAT_VIEW, permissions.CHAT_POST],
    )
    add_membership(email, event, team)
    return email


TEXT = {
    "free": "We are free for a break",
    "late": "Running 5 minutes late",
    "accept": "Accepted - come down now",
    "reject": "Rejected - canteen is full",
}


def check_saved(res, kind, sender, cid):
    assert res.status_code == 201, res.text
    m = res.json()
    assert m["type"] == kind and m["body"] == TEXT[kind]
    assert m["sender"] == sender and m["sender_name"] and m["created_at"]
    assert m["committee_id"] == cid and isinstance(m["id"], int)


def test_oc_requests_and_hospitality_answers_with_the_apps_fields(client):
    oc, hospitality = make(role="oc"), hospitality_member()
    cid = committee_id(client, oc)

    for kind in ("free", "late"):
        check_saved(post(client, oc, cid, type=kind), kind, oc, cid)
    for kind in ("accept", "reject"):
        check_saved(post(client, hospitality, cid, type=kind), kind, hospitality, cid)

    history = client.get(f"/committees/{cid}/messages", headers=auth_header(oc)).json()
    assert [m["type"] for m in history[-4:]] == ["free", "late", "accept", "reject"]


def test_the_oc_role_cannot_accept_or_reject(client):
    oc = make(role="oc")
    cid = committee_id(client, oc)
    for kind in ("accept", "reject"):
        assert post(client, oc, cid, type=kind).status_code == 403


def test_hospitality_cannot_make_requests(client):
    hospitality = hospitality_member()
    cid = committee_id(client, hospitality)
    for kind in ("free", "late"):
        assert post(client, hospitality, cid, type=kind).status_code == 403


def test_the_generic_form_cannot_smuggle_past_the_split(client):
    oc, hospitality = make(role="oc"), hospitality_member()
    cid = committee_id(client, oc)
    # Same thing as {"type": "accept"}, spelled as an ordinary status message.
    sneaky = post(client, oc, cid, kind="status", payload={"type": "accept"})
    assert sneaky.status_code == 403
    assert post(client, hospitality, cid, kind="status", payload={"type": "free"}).status_code == 403
    # Status messages with some other payload are not break actions.
    assert post(client, oc, cid, kind="status", payload={"type": "minutes", "n": 5}).status_code == 201


def test_a_rapporteur_requests_only_for_their_own_committee(client):
    from test_teams import add_membership, make_event, make_team

    rapporteur, event = make(), make_event()
    team = make_team(
        event, "Rapporteurs-" + rapporteur[:6], perms=[permissions.CHAT_VIEW, permissions.CHAT_POST]
    )
    add_membership(rapporteur, event, team, committee="UNSC")
    admin = make(role="admin")
    unsc, ccc = committee_id(client, admin, "UNSC"), committee_id(client, admin, "CCC")

    assert post(client, rapporteur, unsc, type="late").status_code == 201
    assert post(client, rapporteur, unsc, type="accept").status_code == 403
    assert post(client, rapporteur, ccc, type="late").status_code == 403


def test_admin_can_use_every_action(client):
    admin = make(role="admin")
    cid = committee_id(client, admin)
    for kind in ("free", "late", "accept", "reject"):
        assert post(client, admin, cid, type=kind).status_code == 201


def test_the_live_feed_applies_the_same_split(client):
    oc = make(role="oc")
    cid = committee_id(client, oc, "PSC")
    token = auth.create_access_token({"sub": oc})
    with client.websocket_connect(f"/ws/committees/{cid}/chat") as ws:
        ws.send_json({"token": token})
        ws.send_json({"type": "accept"})
        for _ in range(60):  # skip the history replay until the server's answer
            msg = ws.receive_json()
            if "error" in msg:
                assert "cannot send" in msg["error"]
                break
        else:
            raise AssertionError("no error for an OC trying to accept over the socket")


def test_break_actions_reject_bad_types_and_the_wrong_people(client):
    oc = make(role="oc")
    cid = committee_id(client, oc)
    assert post(client, oc, cid, type="dance").status_code == 422
    assert post(client, make(), cid, type="free").status_code == 403
    assert post(client, make(role="eb"), cid, type="free").status_code == 403
    assert client.get(
        f"/committees/{cid}/messages", headers=auth_header(make())
    ).status_code == 403
    assert post(client, oc, 999999, type="free").status_code == 404


def test_plain_upstream_text_messages_still_work_and_appear_as_text(client):
    oc = make(role="oc")
    cid = committee_id(client, oc)
    res = post(client, oc, cid, kind="text", body="hello there")
    assert res.status_code == 201
    assert res.json()["type"] == "text" and res.json()["body"] == "hello there"


def test_live_feed_pushes_a_break_action_with_the_apps_fields(client):
    oc = make(role="oc")
    cid = committee_id(client, oc, "CCC")
    token = auth.create_access_token({"sub": oc})
    with client.websocket_connect(f"/ws/committees/{cid}/chat") as ws:
        ws.send_json({"token": token})
        sent = post(client, oc, cid, type="late").json()
        seen = None
        for _ in range(60):  # history replays first; wait for ours (the app dedupes by id)
            msg = ws.receive_json()
            if msg["id"] == sent["id"]:
                seen = msg
                break
    assert seen is not None
    assert seen["type"] == "late" and seen["sender"] == oc and seen["body"]


def test_live_feed_closes_with_the_codes_the_app_understands(client):
    from starlette.websockets import WebSocketDisconnect

    oc = make(role="oc")
    cid = committee_id(client, oc)

    def close_code(token, committee):
        with client.websocket_connect(f"/ws/committees/{committee}/chat") as ws:
            ws.send_json({"token": token})
            with pytest.raises(WebSocketDisconnect) as e:
                ws.receive_json()
            return e.value.code

    assert close_code("garbage", cid) == 4401
    assert close_code(auth.create_access_token({"sub": make()}), cid) == 4403
    assert close_code(auth.create_access_token({"sub": oc}), 999999) == 4404


# ---- the Hospitality team screen (admin creates the team and adds scanners) ------------


def test_admin_builds_a_hospitality_team_and_its_member_can_scan(client):
    admin = make(role="admin")
    h = auth_header(admin)

    events = client.get("/events", headers=h)
    assert events.status_code == 200 and events.json()
    event_id = events.json()[0]["id"]
    # Only head/admin may list events.
    assert client.get("/events", headers=auth_header(make(role="oc"))).status_code == 403

    team = client.post(
        f"/events/{event_id}/teams",
        json={
            "name": "Hospitality-ct",
            "description": "Scans delegate QR codes for food",
            "permissions": [permissions.FOOD_MANAGE_ENTITLEMENT],
        },
        headers=h,
    )
    assert team.status_code == 201, team.text
    team_id = team.json()["id"]

    # A delegate-role user who is not on the team cannot scan yet.
    member = make()
    _, delegate_id = make_mm("veg")
    assert scan(client, member, delegate_id).status_code == 403

    added = client.post(
        f"/teams/{team_id}/members", json={"email": member, "level": "member"}, headers=h
    )
    assert added.status_code == 201 and added.json()["status"] == "member"

    roster = client.get(f"/teams/{team_id}/members", headers=h).json()
    assert [r["email"] for r in roster] == [member]

    # The app shows the scanner (food.scan) and the server lets them scan.
    perms = set(client.get("/delegates/me", headers=auth_header(member)).json()["permissions"])
    assert "food.scan" in perms
    assert scan(client, member, delegate_id).json()["result"] == "served"

    removed = client.delete(f"/teams/{team_id}/members/{member}", headers=h)
    assert removed.status_code == 200
    assert scan(client, member, delegate_id, "lunch").status_code == 403
