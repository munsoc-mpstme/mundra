"""In-process live fan-out for committee chat channels (docs/adr/0003).

The app runs as a single uvicorn process, so every open WebSocket lives in this process's
memory and a plain in-memory registry reaches all of them. Postgres (chat_messages) is the
durable record; this only pushes new messages to connected sockets.

The ChatHub interface is deliberately small (`connect`, `disconnect`, `broadcast`) so that
if the app is ever scaled to multiple processes, this can be backed by Postgres
LISTEN/NOTIFY without touching the routes.
"""

import asyncio
from collections import defaultdict


class ChatHub:
    def __init__(self) -> None:
        # committee_id -> set of connected WebSockets
        self._channels: dict[int, set] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, committee_id: int, websocket) -> None:
        async with self._lock:
            self._channels[committee_id].add(websocket)

    async def disconnect(self, committee_id: int, websocket) -> None:
        async with self._lock:
            self._channels[committee_id].discard(websocket)
            if not self._channels[committee_id]:
                self._channels.pop(committee_id, None)

    async def broadcast(self, committee_id: int, message: dict) -> None:
        """Send a message to every socket on this committee's channel, dropping any that
        have gone away."""
        async with self._lock:
            sockets = list(self._channels.get(committee_id, ()))
        for websocket in sockets:
            try:
                await websocket.send_json(message)
            except Exception:
                await self.disconnect(committee_id, websocket)


hub = ChatHub()
