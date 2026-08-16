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

    def has_subscribers(self) -> bool:
        """Whether anyone is listening on the caller's workspace channel.

        Serializing a message nobody receives is pure cost, and ingestion emits
        several per observation. At 240 frames/second with no browser attached
        that was the single largest item on the hot path.
        """
        if not self._clients:
            return False
        channel = db.current_db_path()
        return any(subscribed == channel for subscribed in self._clients.values())

    def publish(self, event: str, data: dict):
        if not self._clients:
            return
        channel = db.current_db_path()
        targets = [q for q, subscribed in self._clients.items() if subscribed == channel]
        if not targets:
            return
        msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        for q in targets:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # slow client: drop rather than block ingestion

    @property
    def client_count(self) -> int:
        return len(self._clients)


broker = Broker()
