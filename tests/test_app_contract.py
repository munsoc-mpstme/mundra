"""The Delego app's contract: exactly the requests and responses the shipped app uses.

These replay what lib/auth/capabilities.dart, lib/api/scan_queue.dart,
lib/Pages/Qr_Page/Qr_scanner.dart, lib/Pages/Chat_Page/committee_chat_page.dart and
lib/api/floor_client.dart (EB Tools) send,
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


def food_oc():
    """An OC-role user who is also on the food team: the only non-admin who may scan."""
    from test_teams import add_membership, make_event, make_team

    email = make(role="oc")
    event = make_event()
    team = make_team(event, "Food", perms=[permissions.FOOD_MANAGE_ENTITLEMENT])
    add_membership(email, event, team)
    return email


# ---- /delegates/me: which screens each role gets ---------------------------------------

EXPECTED = {
    "delegate": {"guides.view", "badge.view"},
    "eb": {"guides.view", "badge.view", "eb.tools"},
    # OC alone can neither scan (needs the food team, see food_oc) nor request a break
    # (needs the IHL team, see ihl_oc).
    "oc": {"badge.view", "chat.view"},
    # Admin answers break requests but never makes them.
    "admin": {
        "guides.view", "badge.view", "eb.tools", "food.scan",
        "chat.view", "chat.respond", "admin.roles",
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
    # A chat team (hospitality) can read the chat, but neither answers nor makes break
    # requests any more: answering is admin only, requesting is the IHL team only.
    assert "chat.view" in perms
    assert "chat.respond" not in perms and "chat.send_request" not in perms
    assert "food.scan" not in perms and "admin.roles" not in perms

    # A committee-scoped chat team (a rapporteur) no longer makes requests either.
    rapporteur = make()
    scoped = make_team(event, "Rapporteurs-ct", perms=[permissions.CHAT_VIEW, permissions.CHAT_POST])
    add_membership(rapporteur, event, scoped, committee="UNSC")
    perms = set(
        client.get("/delegates/me", headers=auth_header(rapporteur)).json()["permissions"]
    )
    assert "chat.send_request" not in perms and "chat.respond" not in perms


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
    oc = food_oc()
    _, delegate_id = make_mm("veg")

    res = scan(client, oc, delegate_id, meal="high_tea", diet="jain")

    assert res.status_code == 200
    body = res.json()
    assert body["result"] == "served"
    assert body["delegate_id"] == delegate_id
    assert body["name"]
    assert body["diet"] == "jain"


def test_second_scan_of_the_same_meal_is_a_duplicate_but_another_meal_serves(client):
    oc = food_oc()
    _, delegate_id = make_mm()

    assert scan(client, oc, delegate_id, "lunch").json()["result"] == "served"
    again = scan(client, oc, delegate_id, "lunch")
    assert again.status_code == 200 and again.json()["result"] == "duplicate"
    assert scan(client, oc, delegate_id, "high_tea").json()["result"] == "served"


def test_scan_rejects_bad_input_and_the_wrong_people(client):
    oc, delegate_role, eb = food_oc(), make(), make(role="eb")
    _, delegate_id = make_mm()

    assert scan(client, oc, delegate_id, meal="supper").status_code == 422
    assert scan(client, oc, delegate_id, diet="carnivore").status_code == 422
    assert scan(client, oc, "no-such-id").status_code == 404
    assert scan(client, delegate_role, delegate_id).status_code == 403
    assert scan(client, eb, delegate_id).status_code == 403


def test_only_admin_or_oc_on_the_food_team_can_scan(client):
    """Scanning needs BOTH the OC role and the food team (or admin)."""
    from test_teams import add_membership, make_event, make_team

    _, delegate_id = make_mm()

    # The OC role alone: refused, and the app does not show the scanner.
    oc_only = make(role="oc")
    assert scan(client, oc_only, delegate_id).status_code == 403
    me = client.get("/delegates/me", headers=auth_header(oc_only)).json()
    assert "food.scan" not in me["permissions"]

    # The food team alone (a delegate-role member): refused too.
    team_only = make()
    event = make_event()
    team = make_team(event, "Food-only", perms=[permissions.FOOD_MANAGE_ENTITLEMENT])
    add_membership(team_only, event, team)
    assert scan(client, team_only, delegate_id).status_code == 403
    me = client.get("/delegates/me", headers=auth_header(team_only)).json()
    assert "food.scan" not in me["permissions"]

    # OC and on the food team: allowed, and the app shows the scanner.
    both = food_oc()
    assert "food.scan" in client.get("/delegates/me", headers=auth_header(both)).json()["permissions"]
    assert scan(client, both, delegate_id, "lunch").status_code == 200

    # Admin: always allowed.
    assert scan(client, make(role="admin"), delegate_id, "high_tea").status_code == 200
    assert client.post("/food/scans", data={"delegate_id": "x", "meal": "lunch"}).status_code == 401


def test_the_flagged_list_works_before_the_event_dates_are_set(client):
    oc, admin = food_oc(), make(role="admin")
    _, delegate_id = make_mm()
    scan(client, oc, delegate_id, "lunch")
    scan(client, oc, delegate_id, "lunch")  # the second one is flagged

    res = client.get("/food/flags", headers=auth_header(admin))

    assert res.status_code == 200
    assert any(f["delegate_id"] == delegate_id and f["meal"] == "lunch" for f in res.json())


def test_plate_count_follows_the_diet_the_operator_picked(client):
    oc = food_oc()
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
        "/food/plate_count?meal=lunch", headers=auth_header(make(role="oc"))
    ).status_code == 403  # OC alone is not a scanner
    assert client.get(
        "/food/plate_count?meal=brunch", headers=auth_header(food_oc())
    ).status_code == 422


def test_flagged_scans_stay_team_or_head_only(client):
    assert client.get("/food/flags", headers=auth_header(make(role="oc"))).status_code == 403


# ---- break coordination, as committee_chat_page.dart uses it ---------------------------

COMMITTEES = ["UNSC", "CCC", "PSC", "WTO", "UNODC", "UNICEF", "ECOSOC", "IPC", "DISEC"]


def test_committee_list_for_oc_and_admin_only(client):
    for role in ("oc", "admin"):
        res = client.get("/committees", headers=auth_header(make(role=role)))
        assert res.status_code == 200
        names = [c["name"] for c in res.json()]
        # The migration seeds these in this order; other tests may add more after them.
        assert names[:9] == COMMITTEES
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
    """A plain delegate-role user on a Hospitality team. It can read and post chat, but it
    can no longer request or answer a break: that split is IHL and admin only."""
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


def ihl_oc(committee=None, role="oc"):
    """An OC-role user on the IHL team (the only one who may request a break). Pass a
    committee to scope the membership to it; pass role to build the non-OC case."""
    from test_teams import add_membership, make_event, make_team

    email = make(role=role)
    event = make_event()
    team = make_team(event, "IHL-" + email[:8], perms=[permissions.IHL_REQUEST_BREAK])
    add_membership(email, event, team, committee=committee)
    return email


def a_head():
    from test_teams import add_head, make_event

    email = make()
    add_head(email, make_event())
    return email


TEXT = {
    "free": "We are free for a break",
    "late": "Running 5 minutes late",
    "accept": "Accepted - come down now",
    "reject": "Rejected",
    "agree": "Offer accepted - we'll be there",
    "decline": "Offer declined",
}


def check_saved(res, kind, sender, cid):
    assert res.status_code == 201, res.text
    m = res.json()
    assert m["type"] == kind and m["body"] == TEXT[kind]
    assert m["sender"] == sender and m["sender_name"] and m["created_at"]
    assert m["committee_id"] == cid and isinstance(m["id"], int)


REQUESTS = ("free", "late", "agree", "decline")
ANSWERS = ("accept", "reject")


def test_ihl_requests_and_admin_answers_with_the_apps_fields(client):
    ihl, admin = ihl_oc(), make(role="admin")
    cid = committee_id(client, admin)

    for kind in ("free", "late"):
        check_saved(post(client, ihl, cid, type=kind), kind, ihl, cid)
    for kind in ANSWERS:
        check_saved(post(client, admin, cid, type=kind), kind, admin, cid)

    history = client.get(f"/committees/{cid}/messages", headers=auth_header(admin)).json()
    assert [m["type"] for m in history[-4:]] == ["free", "late", "accept", "reject"]


def test_ihl_answers_an_offer_and_times_come_back_in_history(client):
    ihl, admin = ihl_oc(), make(role="admin")
    cid = committee_id(client, admin)
    at = "2026-10-30T12:50:00+05:30"
    offer = post(
        client, admin, cid, type="reject", body="Rejected - come in 10 min (12:50 PM)",
        payload={"at": at},
    )
    assert offer.status_code == 201
    assert offer.json()["payload"] == {"at": at, "type": "reject"}
    for kind in ("agree", "decline"):
        check_saved(post(client, ihl, cid, type=kind), kind, ihl, cid)
    history = client.get(f"/committees/{cid}/messages", headers=auth_header(admin)).json()
    assert [m["type"] for m in history[-3:]] == ["reject", "agree", "decline"]
    assert history[-3]["payload"]["at"] == at


def test_nobody_but_ihl_can_request(client):
    """Requesting needs BOTH the OC role and the IHL team."""
    cid = committee_id(client, make(role="admin"))
    not_allowed = {
        "OC alone": make(role="oc"),
        "IHL team, not OC": ihl_oc(role="delegate"),
        "OC on the food team": food_oc(),
        "hospitality": hospitality_member(),
        "admin": make(role="admin"),
        "head": a_head(),
    }
    for who_name, who in not_allowed.items():
        for kind in REQUESTS:
            res = post(client, who, cid, type=kind)
            assert res.status_code == 403, (who_name, kind, res.status_code)


def test_nobody_but_admin_can_accept_or_reject(client):
    cid = committee_id(client, make(role="admin"))
    not_allowed = {
        "IHL (OC on the IHL team)": ihl_oc(),
        "OC alone": make(role="oc"),
        "hospitality": hospitality_member(),
        "head": a_head(),
    }
    for who_name, who in not_allowed.items():
        for kind in ANSWERS:
            res = post(client, who, cid, type=kind)
            assert res.status_code == 403, (who_name, kind, res.status_code)


def test_the_app_only_shows_each_side_its_own_buttons(client):
    perms = lambda who: set(
        client.get("/delegates/me", headers=auth_header(who)).json()["permissions"]
    )
    ihl, admin = perms(ihl_oc()), perms(make(role="admin"))
    assert "chat.send_request" in ihl and "chat.respond" not in ihl
    assert "chat.respond" in admin and "chat.send_request" not in admin
    for who in (make(role="oc"), hospitality_member(), ihl_oc(role="delegate"), a_head()):
        p = perms(who)
        assert "chat.send_request" not in p and "chat.respond" not in p


def test_the_generic_form_cannot_smuggle_past_the_split(client):
    ihl, admin = ihl_oc(), make(role="admin")
    cid = committee_id(client, admin)
    # Same thing as {"type": "accept"}, spelled as an ordinary status message.
    assert post(client, ihl, cid, kind="status", payload={"type": "accept"}).status_code == 403
    assert post(client, admin, cid, kind="status", payload={"type": "free"}).status_code == 403
    # Status messages with some other payload are not break actions.
    assert post(client, ihl, cid, kind="status", payload={"type": "minutes", "n": 5}).status_code == 201


def test_a_committee_scoped_ihl_member_requests_only_for_that_committee(client):
    ihl = ihl_oc(committee="UNSC")
    admin = make(role="admin")
    unsc, ccc = committee_id(client, admin, "UNSC"), committee_id(client, admin, "CCC")

    assert post(client, ihl, unsc, type="late").status_code == 201
    assert post(client, ihl, ccc, type="late").status_code == 403
    assert post(client, ihl, unsc, type="accept").status_code == 403


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
    oc = ihl_oc()
    cid = committee_id(client, oc, "CCC")
    token = auth.create_access_token({"sub": oc})
    with client.websocket_connect(f"/ws/committees/{cid}/chat") as ws:
        ws.send_json({"token": token})
        res = post(client, oc, cid, type="late")
        assert res.status_code == 201, res.text  # fail fast instead of waiting on the socket
        sent = res.json()
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

    # An OC-role user who is not on the team cannot scan yet (OC alone is not enough).
    member = make(role="oc")
    _, delegate_id = make_mm("veg")
    assert scan(client, member, delegate_id).status_code == 403

    # A delegate-role user on the team still cannot scan: the OC role is required too.
    delegate_member = make()
    client.post(
        f"/teams/{team_id}/members", json={"email": delegate_member, "level": "member"}, headers=h
    )
    assert scan(client, delegate_member, delegate_id).status_code == 403

    added = client.post(
        f"/teams/{team_id}/members", json={"email": member, "level": "member"}, headers=h
    )
    assert added.status_code == 201 and added.json()["status"] == "member"

    roster = client.get(f"/teams/{team_id}/members", headers=h).json()
    assert sorted(r["email"] for r in roster) == sorted([member, delegate_member])

    # The app shows the scanner (food.scan) and the server lets them scan.
    perms = set(client.get("/delegates/me", headers=auth_header(member)).json()["permissions"])
    assert "food.scan" in perms
    assert scan(client, member, delegate_id).json()["result"] == "served"

    removed = client.delete(f"/teams/{team_id}/members/{member}", headers=h)
    assert removed.status_code == 200
    assert scan(client, member, delegate_id, "lunch").status_code == 403


# ---- EB Tools: the committee floor (lib/api/floor_client.dart) -------------------------


def test_a_plain_eb_runs_the_floor_the_way_the_app_does(client):
    """An `eb`-role user has eb.tools, picks a committee from /public/committees (no
    /committees, which needs chat access) and drives the floor with ops."""
    headers = auth_header(make(role="eb"))
    me = client.get("/delegates/me", headers=headers).json()
    assert "eb.tools" in me["permissions"]

    committees = client.get("/public/committees").json()
    cid = next(c["id"] for c in committees if c["name"] == "IPC")

    # The app loads the committee's matrix for its dropdown ([] for IPC: free entry).
    unsc = next(c["id"] for c in committees if c["name"] == "UNSC")
    matrix = client.get(f"/public/committees/{unsc}/portfolios").json()
    assert set(matrix[0]) == {"name", "code", "tag", "detail"}
    assert client.get(f"/public/committees/{cid}/portfolios").json() == []

    # CCC's matrix is 24 historical figures, each with the placard flag they sit behind.
    ccc = next(c["id"] for c in committees if c["name"] == "CCC")
    ccc_matrix = client.get(f"/public/committees/{ccc}/portfolios").json()
    assert len(ccc_matrix) == 24
    assert (ccc_matrix[0]["name"], ccc_matrix[0]["code"]) == ("Cornell Franklin", "us")
    assert (ccc_matrix[-1]["name"], ccc_matrix[-1]["code"]) == ("Dr. Oskar Trautmann", "de-imp")
    assert all(p["code"] for p in ccc_matrix)

    def send(body):
        res = client.post(f"/committees/{cid}/floor/ops", json=body, headers=headers)
        assert res.status_code == 200, res.text
        return res.json()

    send({"op": "clear", "list": "gsl"})
    send({"op": "reset"})
    send({"op": "add", "list": "gsl", "name": "India", "code": "in"})
    frame = send({"op": "add", "list": "gsl", "name": "Ms Rao", "code": None})
    assert frame["type"] == "state" and isinstance(frame["server_now"], int)
    entry = frame["state"]["lists"]["gsl"]["entries"][-1]
    assert set(entry) == {"id", "name", "code", "tag"}

    frame = send({"op": "start_speaker", "list": "gsl", "replace": False})
    timer = frame["state"]["timer"]
    assert set(timer) == {
        "kind", "list", "speaker_id", "duration", "running", "ends_at", "remaining_ms"
    }
    send({"op": "pause"})
    send({"op": "reset"})
    send({"op": "clear", "list": "gsl"})

    with client.websocket_connect(f"/ws/committees/{cid}/floor") as ws:
        assert ws.receive_json()["state"]["lists"]["gsl"]["entries"] == []


def test_study_guides_endpoint(client):
    res = client.get("/study_guides")
    assert res.status_code == 200
    data = res.json()
    assert "guides" in data
    by_comm = {g["committee"]: g for g in data["guides"]}
    assert "UNSC" in by_comm and "IPC" in by_comm
    assert by_comm["UNSC"]["agendas"][0]["url"] == "/static/study_guides/UNSC.pdf"
    assert by_comm["IPC"]["agendas"][0]["url"] == "/static/study_guides/IPC.pdf"
