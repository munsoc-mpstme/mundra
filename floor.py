"""The live committee floor that EB Tools runs and the smartboard shows.

Each committee has one floor: two speakers' lists (the GSL and the moderated caucus list)
and one timer, either for the current speaker of a list or for an unmoderated caucus.
Both EB members' phones change it through POST /committees/{id}/floor/ops, and every
phone and board follows it over WS /ws/committees/{id}/floor.

The state is a plain JSON document (see new_state), saved in committee_floors so it
survives a restart. The timer is stored as an absolute end time (ends_at, epoch ms on the
server clock), never as a ticking counter, so clients draw the countdown themselves and a
locked phone or a reconnecting board is never out of step.

When a speaker's time runs out, settle() removes that speaker (by id, so a reorder by the
other EB can't remove the wrong delegate) and leaves the timer idle for the next one. It
runs on every read and every change, so this never depends on the background task; the
task only makes it happen on time and tells the boards. Single process only, like chat.py.
Each watching socket has its own writer (Viewer), so a slow one never delays an op.
"""

import asyncio
import contextlib
import copy
import json
import logging
import time
import uuid
from collections import defaultdict

import database

LISTS = ("gsl", "mod")
VIEWS = ("gsl", "mod", "unmod")
DEFAULT_SPEAKER_SECONDS = {"gsl": 90, "mod": 60}
MAX_ENTRIES = 250
MAX_SOCKETS_PER_COMMITTEE = 30
SEND_TIMEOUT_SECONDS = 5
# A watcher with no change to report still gets the state this often, so it can tell a
# quiet floor from a dead link (clients reconnect after ~45 s without a frame).
HEARTBEAT_SECONDS = 20
# How long an op or a read waits for the committee's lock (another op stuck on a slow
# database) before giving up with 503, instead of piling up and landing much later.
LOCK_TIMEOUT_SECONDS = 10

log = logging.getLogger("uvicorn.error")


class FloorError(Exception):
    """An op that can't be applied: status is the HTTP code (404 or 409)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def now_ms() -> int:
    return int(time.time() * 1000)


def idle_timer() -> dict:
    return {
        "kind": "idle",  # idle | speaker | unmod
        "list": None,  # the list a speaker timer belongs to
        "speaker_id": None,  # the entry being timed
        "duration": 0,  # seconds, for the progress bar
        "running": False,
        "ends_at": None,  # epoch ms on the server clock, while running
        "remaining_ms": 0,  # while paused (or 0 when an unmod has ended)
    }


def new_state() -> dict:
    return {
        "version": 0,
        "updated_by": None,
        "display": "gsl",  # what the smartboard shows: gsl | mod | unmod
        "lists": {
            name: {"speaker_seconds": DEFAULT_SPEAKER_SECONDS[name], "entries": []}
            for name in LISTS
        },
        "timer": idle_timer(),
    }


def frame(state: dict) -> dict:
    """What a client receives, over the socket or as an op's response. server_now lets it
    correct for its own clock."""
    return {"type": "state", "state": state, "server_now": now_ms()}


def _entries(state: dict, name: str) -> list[dict]:
    return state["lists"][name]["entries"]


def _index_of(entries: list[dict], entry_id: str) -> int:
    for i, entry in enumerate(entries):
        if entry["id"] == entry_id:
            return i
    raise FloorError(404, "That entry is no longer on the list")


def _timer_is_active(timer: dict) -> bool:
    """Running, or paused with time left. A finished unmod (0 left) is not active."""
    return timer["kind"] != "idle" and (timer["running"] or timer["remaining_ms"] > 0)


def _times(timer: dict, list_name: str) -> bool:
    return timer["kind"] == "speaker" and timer["list"] == list_name


def current_speaker(state: dict, list_name: str) -> dict | None:
    """The speaker being timed on this list, or else the first on it."""
    entries = _entries(state, list_name)
    timer = state["timer"]
    if _times(timer, list_name):
        for entry in entries:
            if entry["id"] == timer["speaker_id"]:
                return entry
    return entries[0] if entries else None


def settle(state: dict, now: int) -> bool:
    """Apply a timer that has run out. Returns whether anything changed."""
    timer = state["timer"]
    if not (timer["running"] and timer["ends_at"] is not None and now >= timer["ends_at"]):
        return False
    if timer["kind"] == "speaker":
        entries = _entries(state, timer["list"])
        entries[:] = [e for e in entries if e["id"] != timer["speaker_id"]]
        state["timer"] = idle_timer()
    else:  # an unmod stops at 00:00 and stays on the board until it is reset
        timer.update(running=False, ends_at=None, remaining_ms=0)
    return True


def apply_op(state: dict, op: dict, now: int) -> None:
    """Change the state in place for one op (a validated models.FloorOp, dumped). Raises
    FloorError when it can't be applied."""
    kind = op["op"]
    timer = state["timer"]

    if kind == "add":
        entries = _entries(state, op["list"])
        if len(entries) >= MAX_ENTRIES:
            raise FloorError(409, "The list is full")
        name = op["name"]
        for e in entries:
            if e["name"].casefold() == name.casefold():
                raise FloorError(409, f"{e['name']} is already on the list")
        entries.append({
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "code": op["code"],
            "tag": op.get("tag"),  # a party abbreviation (PSC), set by the server
        })

    elif kind == "remove":
        entries = _entries(state, op["list"])
        entries.pop(_index_of(entries, op["entry_id"]))
        if _times(timer, op["list"]) and timer["speaker_id"] == op["entry_id"]:
            state["timer"] = idle_timer()

    elif kind == "move":
        entries = _entries(state, op["list"])
        entry = entries.pop(_index_of(entries, op["entry_id"]))
        entries.insert(min(op["to_index"], len(entries)), entry)

    elif kind == "clear":
        _entries(state, op["list"]).clear()
        if _times(timer, op["list"]):
            state["timer"] = idle_timer()

    elif kind == "set_speaker_time":
        state["lists"][op["list"]]["speaker_seconds"] = op["seconds"]

    elif kind == "start_speaker":
        name = op["list"]
        if _times(timer, name) and timer["running"]:
            return  # the other EB already started it
        if _times(timer, name) and timer["remaining_ms"] > 0:
            _resume(state, now)
            state["display"] = name
            return
        if _timer_is_active(timer) and not op["replace"]:
            raise FloorError(409, "Another timer is running")
        speaker = current_speaker(state, name)
        if speaker is None:
            raise FloorError(409, "The list is empty")
        seconds = state["lists"][name]["speaker_seconds"]
        state["timer"] = {
            **idle_timer(),
            "kind": "speaker",
            "list": name,
            "speaker_id": speaker["id"],
            "duration": seconds,
            "running": True,
            "ends_at": now + seconds * 1000,
            "remaining_ms": seconds * 1000,
        }
        state["display"] = name

    elif kind == "start_unmod":
        if timer["kind"] == "speaker" and _timer_is_active(timer) and not op["replace"]:
            raise FloorError(409, "Another timer is running")
        seconds = op["seconds"]
        state["timer"] = {
            **idle_timer(),
            "kind": "unmod",
            "duration": seconds,
            "running": True,
            "ends_at": now + seconds * 1000,
            "remaining_ms": seconds * 1000,
        }
        state["display"] = "unmod"

    elif kind == "pause":
        if timer["running"]:
            timer.update(
                running=False,
                remaining_ms=max(0, timer["ends_at"] - now),
                ends_at=None,
            )

    elif kind == "resume":
        if timer["running"]:
            return
        if not _timer_is_active(timer):
            raise FloorError(409, "There is nothing to resume")
        _resume(state, now)

    elif kind == "reset":
        state["timer"] = idle_timer()

    elif kind == "next":
        # End the speech of the speaker the EB confirmed (by id). If their time already
        # ran out, or the other EB already moved on, they are gone: do nothing rather than
        # take the next delegate off before they have spoken.
        name = op["list"]
        entries = _entries(state, name)
        for i, entry in enumerate(entries):
            if entry["id"] == op["entry_id"]:
                entries.pop(i)
                break
        else:
            raise FloorError(409, "That speaker has already finished")
        if _times(timer, name) and timer["speaker_id"] == op["entry_id"]:
            state["timer"] = idle_timer()

    elif kind == "set_display":
        state["display"] = op["view"]

    else:  # models.FloorOp only lets the kinds above through
        raise FloorError(409, f"Unknown op {kind}")


def _resume(state: dict, now: int) -> None:
    timer = state["timer"]
    if timer["kind"] == "speaker" and not any(
        e["id"] == timer["speaker_id"] for e in _entries(state, timer["list"])
    ):
        state["timer"] = idle_timer()  # the speaker was removed meanwhile
        raise FloorError(409, "That speaker is no longer on the list")
    timer.update(running=True, ends_at=now + timer["remaining_ms"])


class Viewer:
    """One socket watching a committee. Frames go out through its own writer task, which
    always sends the newest state and skips any it was too slow for, so a slow board or
    phone never holds up an EB's tap or the other viewers. A client that doesn't take a
    frame within SEND_TIMEOUT_SECONDS is closed (1011), so it reconnects and catches up."""

    def __init__(self, websocket, on_dead) -> None:
        self.websocket = websocket
        self._on_dead = on_dead
        self._text: str | None = None
        self._version = -1
        self._wake = asyncio.Event()
        self.dead = False
        self._task = asyncio.create_task(self._run())

    def push(self, text: str, version: int) -> None:
        """Queue a frame (already serialised); an older version than the last one queued is
        ignored, an equal one (a heartbeat) is sent again."""
        if self.dead or version < self._version:
            return
        self._version = version
        self._text = text
        self._wake.set()

    def push_state(self, state: dict) -> None:
        self.push(json.dumps(frame(state)), state["version"])

    async def _run(self) -> None:
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                text, self._text = self._text, None
                if text is not None:
                    await asyncio.wait_for(
                        self.websocket.send_text(text), SEND_TIMEOUT_SECONDS
                    )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a timeout or any failure means a dead socket
            self.dead = True
            self._on_dead(self)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.websocket.close(code=1011), 1)

    def stop(self) -> None:
        self.dead = True
        self._task.cancel()


def _due(state: dict, now: int) -> bool:
    """A timer has run out and settle() has work to do."""
    timer = state["timer"]
    return bool(timer["running"] and timer["ends_at"] is not None and now >= timer["ends_at"])


class FloorHub:
    """Holds each committee's floor (cached, backed by committee_floors), applies ops one at
    a time per committee, runs the expiry timers and pushes every change to the viewers."""

    def __init__(self) -> None:
        self._states: dict[int, dict] = {}
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._viewers: defaultdict[int, set[Viewer]] = defaultdict(set)
        self._expiry: dict[int, asyncio.Task] = {}

    # --- viewers ---------------------------------------------------------------------

    def connect(self, committee_id: int, websocket) -> Viewer | None:
        """Register a socket; None when the committee already has too many."""
        viewers = self._viewers[committee_id]
        if len(viewers) >= MAX_SOCKETS_PER_COMMITTEE:
            return None
        viewer = Viewer(websocket, lambda v: self.disconnect(committee_id, v))
        viewers.add(viewer)
        return viewer

    def disconnect(self, committee_id: int, viewer: Viewer) -> None:
        viewers = self._viewers.get(committee_id)
        if viewers is not None:
            viewers.discard(viewer)
            if not viewers:
                self._viewers.pop(committee_id, None)
        if not viewer.dead:
            viewer.stop()

    def _broadcast(self, committee_id: int, state: dict) -> None:
        """Queue the state for every viewer. Synchronous (it only queues), so calling it
        under the lock keeps frames in commit order and nobody waits for a slow socket."""
        viewers = self._viewers.get(committee_id)
        if not viewers:
            return
        text = json.dumps(frame(state))  # serialised once, not once per socket
        for viewer in list(viewers):
            viewer.push(text, state["version"])

    # --- state -----------------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def _locked(self, committee_id: int):
        lock = self._locks[committee_id]
        try:
            await asyncio.wait_for(lock.acquire(), LOCK_TIMEOUT_SECONDS)
        except TimeoutError:
            raise FloorError(503, "The server is busy. Try again in a moment.") from None
        try:
            yield
        finally:
            lock.release()

    async def _load(self, committee_id: int) -> dict:
        state = self._states.get(committee_id)
        if state is None:
            state = await database.get_floor_state(committee_id) or new_state()
            self._states[committee_id] = state
            self._schedule(committee_id, state)
        return state

    async def _commit(self, committee_id: int, state: dict, email: str | None) -> None:
        """Save a changed copy, make it the cached state and queue it for the viewers.
        Call under the lock."""
        state["version"] += 1
        if email is not None:
            state["updated_by"] = email
        await database.save_floor_state(committee_id, state, email)
        self._states[committee_id] = state
        self._schedule(committee_id, state)
        self._broadcast(committee_id, state)

    async def snapshot(self, committee_id: int) -> dict:
        """The current floor, with a timer that ran out already applied. Served from the
        cache without waiting for the lock when there is nothing to settle, so reads (the
        boards' polls, heartbeats) never queue behind ops."""
        cached = self._states.get(committee_id)
        if cached is not None and not _due(cached, now_ms()):
            return copy.deepcopy(cached)
        async with self._locked(committee_id):
            state = copy.deepcopy(await self._load(committee_id))
            if settle(state, now_ms()):
                await self._commit(committee_id, state, None)
        return state

    async def apply(self, committee_id: int, op: dict, email: str) -> dict:
        """Apply one op; the viewers get the result without the caller waiting for them.
        Raises FloorError."""
        async with self._locked(committee_id):
            state = copy.deepcopy(await self._load(committee_id))
            now = now_ms()
            settled = settle(state, now)
            try:
                apply_op(state, op, now)
            except FloorError:
                if settled:  # still record the speaker whose time ran out
                    await self._commit(committee_id, state, None)
                raise
            await self._commit(committee_id, state, email)
        return state

    # --- expiry ----------------------------------------------------------------------

    def _schedule(self, committee_id: int, state: dict) -> None:
        old = self._expiry.pop(committee_id, None)
        if old is not None and old is not asyncio.current_task():
            old.cancel()
        timer = state["timer"]
        if timer["running"] and timer["ends_at"] is not None:
            self._expiry[committee_id] = asyncio.create_task(
                self._expire(committee_id, timer["ends_at"])
            )

    async def _expire(self, committee_id: int, ends_at: int) -> None:
        # A little after the end, so the board's own countdown reaches 00:00 first.
        await asyncio.sleep(max(0, ends_at - now_ms()) / 1000 + 0.05)
        try:
            await self.snapshot(committee_id)
        except Exception:
            log.exception("Could not settle the floor timer of committee %s", committee_id)

    async def restore(self) -> None:
        """On startup: reschedule the timers that were running when the server stopped (and
        settle any that ran out meanwhile). A problem is logged, never raised."""
        try:
            for committee_id in await database.list_running_floor_ids():
                await self.snapshot(committee_id)
        except Exception:
            log.exception("Could not restore the committee floor timers")

    async def shutdown(self) -> None:
        for viewers in list(self._viewers.values()):
            for viewer in list(viewers):
                viewer.stop()
        self._viewers.clear()
        tasks = list(self._expiry.values())
        self._expiry.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def forget(self) -> None:
        """Drop the cache (tests use this to prove the state comes back from the DB)."""
        self._states.clear()


hub = FloorHub()
