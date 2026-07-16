"""In-memory SSE broker. Publish must be called from the event loop thread
(all publishers are async endpoints, so this holds)."""
import asyncio
import json


class Broker:
    def __init__(self):
        self._clients: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self._clients.discard(q)

    def publish(self, event: str, data: dict):
        msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        for q in list(self._clients):
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # slow client: drop rather than block ingestion

    @property
    def client_count(self) -> int:
        return len(self._clients)


broker = Broker()
