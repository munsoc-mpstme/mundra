import asyncio
from collections import defaultdict

class GSLHub:
    def __init__(self) -> None:
        self._channels = defaultdict(set)
        self._lock = asyncio.Lock()
        self._lists = defaultdict(list)

    async def connect(self, committee_id: int, websocket) -> None:
        async with self._lock:
            self._channels[committee_id].add(websocket)
        await websocket.send_json(self._lists[committee_id])

    async def disconnect(self, committee_id: int, websocket) -> None:
        async with self._lock:
            self._channels[committee_id].discard(websocket)
            if not self._channels[committee_id]:
                self._channels.pop(committee_id, None)

    async def update_list(self, committee_id: int, new_list: list) -> None:
        async with self._lock:
            self._lists[committee_id] = new_list
            sockets = list(self._channels.get(committee_id, ()))
        for websocket in sockets:
            try:
                await websocket.send_json(new_list)
            except Exception:
                await self.disconnect(committee_id, websocket)

gsl_hub = GSLHub()
