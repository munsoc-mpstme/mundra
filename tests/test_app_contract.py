"""The Delego app's contract: exactly the requests and responses the shipped app uses.

These replay what lib/auth/capabilities.dart, lib/api/scan_queue.dart,
lib/Pages/Qr_Page/Qr_scanner.dart and lib/Pages/Chat_Page/committee_chat_page.dart send,
so the backend and the app stay compatible.
"""

import asyncio

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
    "oc": {"eb.tools", "food.scan", "chat.view", "chat.send_request", "chat.respond"},
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
    assert {"chat.view", "chat.send_request", "chat.respond"} <= perms
    assert "food.scan" not in perms and "admin.roles" not in perms


# ---- meal scanning, as scan_queue.dart sends it ----------------------------------------


def scan(client, who, delegate_id, meal="breakfast", diet="veg"):
    # Form-encoded, with the extra scanned_at field the app always sends.
    return client.post(
        "/food/scans",
        data={
            "delegate_id": delegate_id,
            "meal": meal,
            "diet": diet,
            "scanned_at": "2026-10-03T08:00:00.000Z",
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


def test_each_break_action_is_saved_with_standard_text_and_the_apps_fields(client):
    oc = make(role="oc")
    cid = committee_id(client, oc)
    expected = {
        "free": "We are free for a break",
        "late": "Running 5 minutes late",
        "accept": "Accepted - come down now",
        "reject": "Rejected - canteen is full",
    }
    for kind, text in expected.items():
        res = post(client, oc, cid, type=kind)
        assert res.status_code == 201, res.text
        m = res.json()
        assert m["type"] == kind and m["body"] == text
        assert m["sender"] == oc and m["sender_name"] and m["created_at"]
        assert m["committee_id"] == cid and isinstance(m["id"], int)

    history = client.get(f"/committees/{cid}/messages", headers=auth_header(oc)).json()
    assert [m["type"] for m in history[-4:]] == list(expected)


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
