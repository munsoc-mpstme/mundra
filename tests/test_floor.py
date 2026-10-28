"""The committee floor for EB Tools: speakers' lists, the synced timer and the smartboard
socket (floor.py)."""

import asyncio
import json
import time
import uuid

import pytest
from helpers import auth_header
from starlette.websockets import WebSocketDisconnect
from test_chat import make_committee
from test_teams import add_head, make, make_event

import floor


@pytest.fixture
def committee():
    """A fresh committee per test, so floors never leak between tests."""
    return make_committee(make_event(), f"C-{uuid.uuid4().hex[:6]}")


@pytest.fixture
def eb():
    return auth_header(make(role="eb"))


@pytest.fixture
def clock(monkeypatch):
    """A controllable server clock (epoch ms)."""
    now = {"ms": 1_800_000_000_000}
    monkeypatch.setattr(floor, "now_ms", lambda: now["ms"])
    return now


def op(client, committee, headers, **body):
    return client.post(f"/committees/{committee}/floor/ops", json=body, headers=headers)


def state(client, committee):
    res = client.get(f"/committees/{committee}/floor")
    assert res.status_code == 200
    return res.json()["state"]


def names(st, name="gsl"):
    return [e["name"] for e in st["lists"][name]["entries"]]


def add(client, committee, headers, *portfolios, list_name="gsl"):
    for p in portfolios:
        res = op(client, committee, headers, op="add", list=list_name, name=p, code="in")
        assert res.status_code == 200, res.text
    return res.json()["state"]


# --- access -----------------------------------------------------------------------------


def test_anyone_can_read_a_floor(client, committee):
    st = state(client, committee)
    assert st["lists"]["gsl"]["entries"] == []
    assert st["timer"]["kind"] == "idle"
    assert st["lists"]["gsl"]["speaker_seconds"] == 90


def test_unknown_committee_is_404(client, eb):
    assert client.get("/committees/999999/floor").status_code == 404
    res = op(client, 999999, eb, op="add", list="gsl", name="India")
    assert res.status_code == 404


def test_only_the_eb_side_can_change_it(client, committee):
    body = {"op": "add", "list": "gsl", "name": "India"}
    assert client.post(f"/committees/{committee}/floor/ops", json=body).status_code == 401
    delegate = auth_header(make())
    assert op(client, committee, delegate, **body).status_code == 403
    for role in ("eb", "oc", "admin"):
        res = op(client, committee, auth_header(make(role=role)), **{**body, "name": role})
        assert res.status_code == 200, role


def test_a_head_can_change_it(client, committee):
    email = make()
    add_head(email, make_event())
    res = op(client, committee, auth_header(email), op="add", list="gsl", name="India")
    assert res.status_code == 200


def test_edits_record_who_made_them(client, committee):
    email = make(role="eb")
    res = op(client, committee, auth_header(email), op="add", list="gsl", name="India")
    assert res.json()["state"]["updated_by"] == email


# --- lists ------------------------------------------------------------------------------


def test_add_keeps_order_flag_and_rejects_duplicates(client, committee, eb):
    add(client, committee, eb, "India", "France")
    res = op(client, committee, eb, op="add", list="gsl", name="  india ")
    assert res.status_code == 409
    assert "already on the list" in res.json()["detail"]
    res = op(client, committee, eb, op="add", list="gsl", name="Mr  Smith", code=None)
    st = res.json()["state"]
    assert names(st) == ["India", "France", "Mr Smith"]
    assert st["lists"]["gsl"]["entries"][0]["code"] == "in"
    assert st["lists"]["gsl"]["entries"][2]["code"] is None


def test_lists_are_separate(client, committee, eb):
    add(client, committee, eb, "India")
    st = add(client, committee, eb, "India", list_name="mod")
    assert names(st, "gsl") == ["India"]
    assert names(st, "mod") == ["India"]


@pytest.mark.parametrize(
    "body",
    [
        {"op": "add", "list": "gsl", "name": ""},
        {"op": "add", "list": "gsl", "name": "x" * 81},
        {"op": "add", "list": "gsl", "name": "bad\x00name"},
        {"op": "add", "list": "gsl", "name": "India", "code": "<script>"},
        {"op": "add", "list": "nope", "name": "India"},
        {"op": "add", "list": "gsl", "name": "India", "extra": 1},
        {"op": "set_speaker_time", "list": "gsl", "seconds": 4},
        {"op": "set_speaker_time", "list": "gsl", "seconds": 6000},
        {"op": "start_unmod", "seconds": 0},
        {"op": "set_display", "view": "everything"},
        {"op": "explode"},
    ],
)
def test_bad_ops_are_422(client, committee, eb, body):
    assert op(client, committee, eb, **body).status_code == 422


def test_move_and_remove_by_id(client, committee, eb):
    st = add(client, committee, eb, "A", "B", "C")
    ids = {e["name"]: e["id"] for e in st["lists"]["gsl"]["entries"]}
    st = op(client, committee, eb, op="move", list="gsl", entry_id=ids["C"], to_index=0).json()["state"]
    assert names(st) == ["C", "A", "B"]
    st = op(client, committee, eb, op="move", list="gsl", entry_id=ids["C"], to_index=99).json()["state"]
    assert names(st) == ["A", "B", "C"]
    st = op(client, committee, eb, op="remove", list="gsl", entry_id=ids["B"]).json()["state"]
    assert names(st) == ["A", "C"]
    res = op(client, committee, eb, op="remove", list="gsl", entry_id=ids["B"])
    assert res.status_code == 404


def test_clear(client, committee, eb):
    add(client, committee, eb, "A", "B")
    st = op(client, committee, eb, op="clear", list="gsl").json()["state"]
    assert names(st) == []


def test_the_list_has_a_limit(client, committee, eb, monkeypatch):
    monkeypatch.setattr(floor, "MAX_ENTRIES", 2)
    add(client, committee, eb, "A", "B")
    assert op(client, committee, eb, op="add", list="gsl", name="C").status_code == 409


# --- the speaker timer ------------------------------------------------------------------


def test_time_running_out_keeps_the_speaker_in_overtime(client, committee, eb, clock):
    add(client, committee, eb, "India", "France")
    op(client, committee, eb, op="set_speaker_time", list="gsl", seconds=60)
    res = op(client, committee, eb, op="start_speaker", list="gsl")
    body = res.json()
    timer = body["state"]["timer"]
    assert timer["kind"] == "speaker" and timer["running"]
    assert timer["ends_at"] == clock["ms"] + 60_000
    assert body["server_now"] == clock["ms"]
    assert body["state"]["display"] == "gsl"

    # Speeches go over: at and past the end nothing happens; clients draw ends_at - now
    # below zero (-00:15) until the EB presses Next.
    clock["ms"] += 75_000
    st = state(client, committee)
    assert names(st) == ["India", "France"]
    assert st["timer"]["running"] and st["timer"]["ends_at"] - clock["ms"] == -15_000
    india = st["lists"]["gsl"]["entries"][0]["id"]
    st = op(client, committee, eb, op="next", list="gsl", entry_id=india).json()["state"]
    assert names(st) == ["France"] and st["timer"]["kind"] == "idle"


def test_pausing_in_overtime_keeps_the_overtime(client, committee, eb, clock):
    add(client, committee, eb, "India")
    op(client, committee, eb, op="set_speaker_time", list="gsl", seconds=30)
    op(client, committee, eb, op="start_speaker", list="gsl")
    clock["ms"] += 42_000
    timer = op(client, committee, eb, op="pause").json()["state"]["timer"]
    assert not timer["running"] and timer["remaining_ms"] == -12_000
    clock["ms"] += 60_000  # paused: the overtime doesn't grow
    timer = op(client, committee, eb, op="resume").json()["state"]["timer"]
    assert timer["running"] and timer["ends_at"] == clock["ms"] - 12_000
    # Start on the paused overtime speaker carries on too (the other EB tapping Start).
    op(client, committee, eb, op="pause")
    timer = op(client, committee, eb, op="start_speaker", list="gsl").json()["state"]["timer"]
    assert timer["running"] and timer["ends_at"] == clock["ms"] - 12_000


def test_a_timer_in_overtime_gives_way_to_the_next_one(client, committee, eb, clock):
    add(client, committee, eb, "India")
    add(client, committee, eb, "Chile", list_name="mod")
    op(client, committee, eb, op="set_speaker_time", list="gsl", seconds=10)
    op(client, committee, eb, op="start_speaker", list="gsl")
    clock["ms"] += 9_000  # time still left: starting another needs replace
    assert op(client, committee, eb, op="start_speaker", list="mod").status_code == 409
    clock["ms"] += 2_000  # in overtime: it has done its job
    st = op(client, committee, eb, op="start_speaker", list="mod").json()["state"]
    assert st["timer"]["list"] == "mod"
    assert names(st, "gsl") == ["India"]  # nobody is removed by giving way


def test_pause_and_resume_keep_the_time_left(client, committee, eb, clock):
    add(client, committee, eb, "India")
    op(client, committee, eb, op="start_speaker", list="gsl")
    clock["ms"] += 30_000
    timer = op(client, committee, eb, op="pause").json()["state"]["timer"]
    assert not timer["running"] and timer["remaining_ms"] == 60_000
    clock["ms"] += 600_000  # a paused timer stands still
    assert names(state(client, committee)) == ["India"]
    timer = op(client, committee, eb, op="resume").json()["state"]["timer"]
    assert timer["running"] and timer["ends_at"] == clock["ms"] + 60_000
    # Start on a paused speaker resumes too (the other EB tapping Start).
    op(client, committee, eb, op="pause")
    timer = op(client, committee, eb, op="start_speaker", list="gsl").json()["state"]["timer"]
    assert timer["running"] and timer["ends_at"] == clock["ms"] + 60_000


def test_start_twice_is_harmless(client, committee, eb, clock):
    add(client, committee, eb, "India")
    first = op(client, committee, eb, op="start_speaker", list="gsl").json()["state"]["timer"]
    clock["ms"] += 5_000
    again = op(client, committee, eb, op="start_speaker", list="gsl").json()["state"]["timer"]
    assert again["ends_at"] == first["ends_at"]


def test_next_ends_the_named_speakers_speech_early(client, committee, eb, clock):
    st = add(client, committee, eb, "India", "France")
    india, france = (e["id"] for e in st["lists"]["gsl"]["entries"])
    op(client, committee, eb, op="start_speaker", list="gsl")
    st = op(client, committee, eb, op="next", list="gsl", entry_id=india).json()["state"]
    assert names(st) == ["France"] and st["timer"]["kind"] == "idle"
    st = op(client, committee, eb, op="next", list="gsl", entry_id=france).json()["state"]
    assert names(st) == []


def test_next_needs_the_speaker(client, committee, eb):
    add(client, committee, eb, "India")
    assert op(client, committee, eb, op="next", list="gsl").status_code == 422


def test_next_after_the_time_ran_out_removes_only_that_speaker(client, committee, eb, clock):
    """The review's scenario, now with overtime: India's time ran out and India is still
    on (in overtime); both EBs confirm "End India's speech?". Only India goes."""
    vice = auth_header(make(role="eb"))
    st = add(client, committee, eb, "India", "Brazil", "Chad")
    india = st["lists"]["gsl"]["entries"][0]["id"]
    op(client, committee, eb, op="set_speaker_time", list="gsl", seconds=5)
    op(client, committee, eb, op="start_speaker", list="gsl")
    clock["ms"] += 6_500
    assert op(client, committee, eb, op="next", list="gsl", entry_id=india).status_code == 200
    res = op(client, committee, vice, op="next", list="gsl", entry_id=india)
    assert res.status_code == 409
    assert res.json()["detail"] == "That speaker has already finished"
    assert names(state(client, committee)) == ["Brazil", "Chad"]


def test_both_ebs_pressing_next_remove_one_speaker(client, committee, eb):
    vice = auth_header(make(role="eb"))
    st = add(client, committee, eb, "India", "Brazil", "Chad")
    india = st["lists"]["gsl"]["entries"][0]["id"]
    first = op(client, committee, eb, op="next", list="gsl", entry_id=india)
    second = op(client, committee, vice, op="next", list="gsl", entry_id=india)
    assert (first.status_code, second.status_code) == (200, 409)
    assert names(state(client, committee)) == ["Brazil", "Chad"]


def test_next_on_a_speaker_who_is_not_timed_keeps_the_running_timer(client, committee, eb, clock):
    st = add(client, committee, eb, "India", "Brazil")
    india, brazil = (e["id"] for e in st["lists"]["gsl"]["entries"])
    op(client, committee, eb, op="move", list="gsl", entry_id=brazil, to_index=0)
    op(client, committee, eb, op="start_speaker", list="gsl")  # times Brazil
    st = op(client, committee, eb, op="next", list="gsl", entry_id=india).json()["state"]
    assert names(st) == ["Brazil"]
    assert st["timer"]["speaker_id"] == brazil and st["timer"]["running"]


def test_removing_the_timed_speaker_stops_the_timer(client, committee, eb, clock):
    st = add(client, committee, eb, "India", "France")
    india = st["lists"]["gsl"]["entries"][0]["id"]
    op(client, committee, eb, op="start_speaker", list="gsl")
    st = op(client, committee, eb, op="remove", list="gsl", entry_id=india).json()["state"]
    assert st["timer"]["kind"] == "idle"
    assert names(st) == ["France"]


def test_reset_keeps_the_speaker(client, committee, eb, clock):
    add(client, committee, eb, "India")
    op(client, committee, eb, op="start_speaker", list="gsl")
    st = op(client, committee, eb, op="reset").json()["state"]
    assert st["timer"]["kind"] == "idle" and names(st) == ["India"]


def test_an_empty_list_cannot_start(client, committee, eb):
    assert op(client, committee, eb, op="start_speaker", list="gsl").status_code == 409


def test_another_timer_needs_replace(client, committee, eb, clock):
    add(client, committee, eb, "India")
    add(client, committee, eb, "Chile", list_name="mod")
    op(client, committee, eb, op="start_speaker", list="gsl")
    assert op(client, committee, eb, op="start_speaker", list="mod").status_code == 409
    assert op(client, committee, eb, op="start_unmod", seconds=300).status_code == 409
    st = op(client, committee, eb, op="start_speaker", list="mod", replace=True).json()["state"]
    assert st["timer"]["list"] == "mod" and st["display"] == "mod"
    assert names(st, "gsl") == ["India"]  # replacing doesn't remove anyone


# --- unmod ------------------------------------------------------------------------------


def test_unmod_runs_into_overtime(client, committee, eb, clock):
    add(client, committee, eb, "India")
    st = op(client, committee, eb, op="start_unmod", seconds=600).json()["state"]
    assert st["display"] == "unmod" and st["timer"]["kind"] == "unmod"
    clock["ms"] += 630_000
    st = state(client, committee)
    assert st["timer"]["kind"] == "unmod" and st["timer"]["running"]
    assert st["timer"]["ends_at"] - clock["ms"] == -30_000
    assert names(st) == ["India"]
    # An unmod in overtime doesn't block the next speaker.
    assert op(client, committee, eb, op="start_speaker", list="gsl").status_code == 200


def test_set_display(client, committee, eb):
    st = op(client, committee, eb, op="set_display", view="mod").json()["state"]
    assert st["display"] == "mod"


# --- persistence and the socket ---------------------------------------------------------


def test_the_floor_survives_a_restart(client, committee, eb, clock):
    add(client, committee, eb, "India", "France")
    op(client, committee, eb, op="start_speaker", list="gsl")
    floor.hub.forget()
    st = state(client, committee)
    assert names(st) == ["India", "France"] and st["timer"]["running"]
    floor.hub.forget()
    clock["ms"] += 100_000  # past the end while restarting: still counting overtime
    st = state(client, committee)
    assert names(st) == ["India", "France"]
    assert st["timer"]["running"] and st["timer"]["ends_at"] - clock["ms"] == -10_000


def test_version_goes_up_on_every_change(client, committee, eb):
    v0 = state(client, committee)["version"]
    st = add(client, committee, eb, "A", "B")
    assert st["version"] == v0 + 2


def test_websocket_sends_the_state_and_every_change(client, committee, eb):
    with client.websocket_connect(f"/ws/committees/{committee}/floor") as ws:
        first = ws.receive_json()
        assert first["type"] == "state" and "server_now" in first
        add(client, committee, eb, "India")
        update = ws.receive_json()
        assert names(update["state"]) == ["India"]
        assert update["state"]["version"] > first["state"]["version"]


def test_websocket_closes_a_client_that_talks(client, committee):
    with client.websocket_connect(f"/ws/committees/{committee}/floor") as ws:
        ws.receive_json()
        ws.send_text("hello")
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 1008


def test_websocket_unknown_committee(client):
    with client.websocket_connect("/ws/committees/999999/floor") as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4404


def test_websocket_viewer_limit(client, committee, monkeypatch):
    monkeypatch.setattr(floor, "MAX_SOCKETS_PER_COMMITTEE", 1)
    with client.websocket_connect(f"/ws/committees/{committee}/floor") as first:
        first.receive_json()
        with client.websocket_connect(f"/ws/committees/{committee}/floor") as second:
            with pytest.raises(WebSocketDisconnect) as e:
                second.receive_json()
            assert e.value.code == 1013


def test_websocket_heartbeat_resends_the_state_when_nothing_changes(client, committee, monkeypatch):
    monkeypatch.setattr(floor, "HEARTBEAT_SECONDS", 0.2)
    with client.websocket_connect(f"/ws/committees/{committee}/floor") as ws:
        first = ws.receive_json()
        beat = ws.receive_json()  # no op in between
        assert beat["type"] == "state"
        assert beat["state"]["version"] == first["state"]["version"]
        assert beat["server_now"] >= first["server_now"]


class _Stalled:
    """A socket whose client stopped reading."""

    def __init__(self):
        self.closed_with = None
        self.sent = []

    async def send_text(self, text):
        await asyncio.sleep(10)

    async def close(self, code=1000):
        self.closed_with = code


class _Healthy(_Stalled):
    async def send_text(self, text):
        self.sent.append(json.loads(text))


def test_a_socket_that_stops_taking_frames_is_closed_not_just_dropped(monkeypatch):
    """A stalled board used to be dropped from broadcasts but left open, so it showed
    "Live" with a frozen list. Now it is closed, so it reconnects."""

    async def go():
        monkeypatch.setattr(floor, "SEND_TIMEOUT_SECONDS", 0.05)
        hub = floor.FloorHub()
        stalled, healthy = _Stalled(), _Healthy()
        hub.connect(1, stalled)
        hub.connect(1, healthy)
        hub._broadcast(1, floor.new_state())
        await asyncio.sleep(0.2)
        return hub, stalled, healthy

    hub, stalled, healthy = asyncio.run(go())
    assert stalled.closed_with == 1011
    assert healthy.closed_with is None and len(healthy.sent) == 1
    assert {v.websocket for v in hub._viewers[1]} == {healthy}


def test_an_op_does_not_wait_for_slow_viewers(monkeypatch):
    """An EB's tap used to wait (up to 5 s) for every board to take the update."""

    async def nothing(*args):
        return None

    monkeypatch.setattr(floor.database, "get_floor_state", nothing)
    monkeypatch.setattr(floor.database, "save_floor_state", nothing)

    async def go():
        hub = floor.FloorHub()
        for _ in range(10):
            hub.connect(1, _Stalled())
        started = time.monotonic()
        await hub.apply(1, {"op": "add", "list": "gsl", "name": "India", "code": "in"}, "eb@x")
        took = time.monotonic() - started
        await hub.shutdown()
        return took

    assert asyncio.run(go()) < 0.5


def test_a_slow_viewer_gets_the_newest_state_not_a_backlog():
    """Frames a viewer was too slow for are skipped; it gets the latest one, in order."""

    class Slow(_Healthy):
        async def send_text(self, text):
            await asyncio.sleep(0.05)
            self.sent.append(json.loads(text)["state"]["version"])

    async def go():
        hub = floor.FloorHub()
        slow = Slow()
        hub.connect(1, slow)
        state = floor.new_state()
        for version in range(1, 21):
            state["version"] = version
            hub._broadcast(1, state)
            await asyncio.sleep(0.005)
        await asyncio.sleep(0.3)
        await hub.shutdown()
        return slow.sent

    sent = asyncio.run(go())
    assert sent == sorted(sent) and sent[-1] == 20
    assert len(sent) < 20  # skipped the ones it was too slow for


def test_a_stuck_floor_answers_busy_instead_of_queueing(client, committee, eb, monkeypatch):
    """An op stuck on a slow database used to hold the committee for a minute and make
    later taps land long after the EB gave up. Now they get 503 quickly."""
    monkeypatch.setattr(floor, "LOCK_TIMEOUT_SECONDS", 0.2)

    async def hold():
        await floor.hub._locks[committee].acquire()

    client.portal.call(hold)
    try:
        res = op(client, committee, eb, op="add", list="gsl", name="India")
        assert res.status_code == 503
        assert res.json()["detail"] == "The server is busy. Try again in a moment."
    finally:
        client.portal.call(lambda: _release(committee))


async def _release(committee):
    floor.hub._locks[committee].release()


def test_public_committees_lists_event_one(client):
    res = client.get("/public/committees")
    assert res.status_code == 200
    assert {"UNSC", "IPC"} <= {c["name"] for c in res.json()}


# --- portfolio matrices -----------------------------------------------------------------


def committee_id(client, name):
    return next(c["id"] for c in client.get("/public/committees").json() if c["name"] == name)


def test_the_matrices_are_seeded(client):
    def matrix(name):
        res = client.get(f"/public/committees/{committee_id(client, name)}/portfolios")
        assert res.status_code == 200
        return res.json()

    unsc = matrix("UNSC")
    assert len(unsc) == 20
    assert unsc[0] == {"name": "China", "code": "cn", "tag": None, "detail": None}
    disec = matrix("DISEC")
    assert len(disec) == 33
    assert disec[0] == {"name": "United States of America", "code": "us", "tag": None, "detail": None}
    assert disec[-1] == {"name": "Australia", "code": "au", "tag": None, "detail": None}
    for name in ("ECOSOC", "UNICEF", "UNODC", "WTO", "PSC"):
        assert len(matrix(name)) == 45, name
    psc = matrix("PSC")
    assert psc[0] == {
        "name": "Ajay Maken", "code": None, "tag": "INC", "detail": "Indian National Congress",
    }
    assert all(p["code"] is None and p["tag"] for p in psc)
    assert len(matrix("CCC")) == 24 and matrix("IPC") == []
    assert "Equatorial Guinea" in {p["name"] for p in matrix("UNICEF")}  # sheet typo fixed


def test_portfolios_of_an_unknown_committee_is_404(client):
    assert client.get("/public/committees/999999/portfolios").status_code == 404


def test_a_matrix_add_takes_the_matrix_name_and_flag(client, eb):
    unsc = committee_id(client, "UNSC")
    op(client, unsc, eb, op="clear", list="gsl")
    res = op(client, unsc, eb, op="add", list="gsl", name="united states of america", code="fr")
    entry = res.json()["state"]["lists"]["gsl"]["entries"][-1]
    assert entry["name"] == "United States of America" and entry["code"] == "us"
    op(client, unsc, eb, op="clear", list="gsl")


def test_add_other_in_a_matrix_committee_is_a_person(client, eb):
    unsc = committee_id(client, "UNSC")
    op(client, unsc, eb, op="clear", list="mod")
    res = op(client, unsc, eb, op="add", list="mod", name="Special Rapporteur", code="in")
    entry = res.json()["state"]["lists"]["mod"]["entries"][-1]
    assert entry["name"] == "Special Rapporteur"
    assert entry["code"] is None and entry["tag"] is None
    op(client, unsc, eb, op="clear", list="mod")


def test_a_psc_member_carries_their_party_tag(client, eb):
    psc = committee_id(client, "PSC")
    op(client, psc, eb, op="clear", list="gsl")
    res = op(client, psc, eb, op="add", list="gsl", name="Mahua Moitra", code=None)
    entry = res.json()["state"]["lists"]["gsl"]["entries"][-1]
    assert entry == {**entry, "name": "Mahua Moitra", "code": None, "tag": "AITC"}
    op(client, psc, eb, op="clear", list="gsl")


def test_a_committee_without_a_matrix_is_free_entry(client, eb):
    ipc = committee_id(client, "IPC")  # the press never has a matrix
    op(client, ipc, eb, op="clear", list="gsl")
    res = op(client, ipc, eb, op="add", list="gsl", name="Atlantis", code="gr")
    entry = res.json()["state"]["lists"]["gsl"]["entries"][-1]
    assert entry["name"] == "Atlantis" and entry["code"] == "gr"
    op(client, ipc, eb, op="clear", list="gsl")
