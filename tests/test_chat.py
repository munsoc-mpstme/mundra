"""Committees, session status and the hospitality<->rapporteur chat (docs/adr/0003)."""

import asyncio
import uuid

import pytest

import auth
import database
import db
import permissions
from helpers import auth_header
from test_teams import add_head, add_membership, make, make_event, make_team


CHAT_PERMS = [permissions.CHAT_POST, permissions.CHAT_VIEW]


def make_committee(event_id, name):
    async def go():
        async with db.SessionLocal() as session, session.begin():
            row = db.CommitteeRow(event_id=event_id, name=name)
            session.add(row)
            await session.flush()
            return row.id

    return asyncio.run(go())


def rapporteur(event, committee_name, perms=None):
    """A rapporteur scoped to one committee."""
    email = make()
    team = make_team(
        event,
        f"Rapporteur-{uuid.uuid4().hex[:6]}",
        perms=perms if perms is not None else CHAT_PERMS + [permissions.RAPPORTEUR_SET_COMMITTEE_STATUS],
    )
    add_membership(email, event, team, committee=committee_name)
    return email


def hospitality(event):
    """Hospitality: chat access to every committee (unscoped)."""
    email = make()
    team = make_team(event, f"Hospitality-{uuid.uuid4().hex[:6]}", perms=CHAT_PERMS)
    add_membership(email, event, team)
    return email


# --- committees & status ----------------------------------------------------------


def test_a_head_creates_a_committee(client):
    admin = make(role="admin")  # admin passes require_head
    event = make_event()
    res = client.post(
        f"/events/{event}/committees", json={"name": "UNSC"}, headers=auth_header(admin)
    )
    assert res.status_code == 201
    assert res.json()["name"] == "UNSC" and res.json()["status"] == "in_session"


def test_any_delegate_can_read_committee_statuses(client):
    event = make_event()
    make_committee(event, "UNSC")
    delegate = make()
    res = client.get(f"/events/{event}/committees", headers=auth_header(delegate))
    assert res.status_code == 200
    assert [c["name"] for c in res.json()] == ["UNSC"]


def test_a_rapporteur_sets_only_their_own_committees_status(client):
    event = make_event()
    unsc = make_committee(event, "UNSC")
    uncsw = make_committee(event, "UNCSW")
    rep = rapporteur(event, "UNSC")

    ok = client.patch(
        f"/committees/{unsc}/status", json={"status": "adjourned"}, headers=auth_header(rep)
    )
    assert ok.status_code == 200 and ok.json()["status"] == "adjourned"

    denied = client.patch(
        f"/committees/{uncsw}/status", json={"status": "adjourned"}, headers=auth_header(rep)
    )
    assert denied.status_code == 403


# --- chat access ------------------------------------------------------------------


def test_rapporteur_posts_to_own_committee_not_others(client):
    event = make_event()
    unsc = make_committee(event, "UNSC")
    uncsw = make_committee(event, "UNCSW")
    rep = rapporteur(event, "UNSC")

    assert client.post(
        f"/committees/{unsc}/messages", json={"body": "we are free"}, headers=auth_header(rep)
    ).status_code == 201
    assert client.post(
        f"/committees/{uncsw}/messages", json={"body": "hi"}, headers=auth_header(rep)
    ).status_code == 403


def test_hospitality_posts_to_any_committee(client):
    event = make_event()
    unsc = make_committee(event, "UNSC")
    hospi = hospitality(event)
    res = client.post(
        f"/committees/{unsc}/messages",
        json={"kind": "text", "body": "coming down?"},
        headers=auth_header(hospi),
    )
    assert res.status_code == 201
    assert res.json()["sender_name"]


def test_an_outsider_cannot_read_a_channel(client):
    event = make_event()
    unsc = make_committee(event, "UNSC")
    outsider = make()
    assert client.get(f"/committees/{unsc}/messages", headers=auth_header(outsider)).status_code == 403


def test_history_is_returned_in_order(client):
    event = make_event()
    unsc = make_committee(event, "UNSC")
    hospi = hospitality(event)
    for text in ("first", "second", "third"):
        client.post(f"/committees/{unsc}/messages", json={"body": text}, headers=auth_header(hospi))
    history = client.get(f"/committees/{unsc}/messages", headers=auth_header(hospi)).json()
    assert [m["body"] for m in history] == ["first", "second", "third"]


def test_a_status_quick_action_carries_its_payload(client):
    event = make_event()
    unsc = make_committee(event, "UNSC")
    rep = rapporteur(event, "UNSC")
    res = client.post(
        f"/committees/{unsc}/messages",
        json={"kind": "status", "body": "Running late", "payload": {"type": "late", "minutes": 5}},
        headers=auth_header(rep),
    )
    assert res.status_code == 201
    assert res.json()["kind"] == "status"
    assert res.json()["payload"] == {"type": "late", "minutes": 5}


# --- websocket --------------------------------------------------------------------


def test_websocket_delivers_a_live_message(client):
    event = make_event()
    unsc = make_committee(event, "UNSC")
    hospi = hospitality(event)
    token = auth.create_access_token({"sub": hospi})

    with client.websocket_connect(f"/ws/committees/{unsc}/chat") as ws:
        ws.send_json({"token": token})
        ws.send_json({"kind": "text", "body": "over here"})
        msg = ws.receive_json()
        assert msg["body"] == "over here"
        assert msg["sender_email"] == hospi


def test_websocket_rejects_a_bad_token(client):
    event = make_event()
    unsc = make_committee(event, "UNSC")
    with client.websocket_connect(f"/ws/committees/{unsc}/chat") as ws:
        ws.send_json({"token": "not-a-real-token"})
        with pytest.raises(Exception):
            ws.receive_json()


def test_websocket_rejects_someone_without_access(client):
    event = make_event()
    unsc = make_committee(event, "UNSC")
    outsider = make()
    token = auth.create_access_token({"sub": outsider})
    with client.websocket_connect(f"/ws/committees/{unsc}/chat") as ws:
        ws.send_json({"token": token})
        with pytest.raises(Exception):
            ws.receive_json()
