"""In-memory SSE broker. Publish must be called from the event loop thread
(all publishers are async endpoints, so this holds)."""
import asyncio
import json

from .. import db


class Broker:
    def __init__(self):
        self._clients: dict[asyncio.Queue, str] = {}

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._clients[q] = db.current_db_path()
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._clients.pop(q, None)

    def publish(self, event: str, data: dict):
        msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        channel = db.current_db_path()
        for q, subscribed_channel in list(self._clients.items()):
            if subscribed_channel != channel:
                continue
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # slow client: drop rather than block ingestion

    @property
    def client_count(self) -> int:
        return len(self._clients)


broker = Broker()
