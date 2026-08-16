"""The live coordinator: latest source state, dirty groups, and a 100 Hz scheduler.

Two paths leave ingestion, and they have opposite priorities.

*Raw history favours completeness.* Every accepted DetectionSample is durably
persisted before the HTTP response returns. Nothing in this module can drop,
skip, or defer a raw observation.

*Live state favours freshness.* Fusion no longer runs once per arriving camera
frame. Ingestion publishes the source's newest completed sample here and marks
its groups dirty; an independent monotonic scheduler runs at most one group tick
every `STORELENS_LIVE_TICK_INTERVAL_S` (10 ms by default) and consumes only the
newest state. If four frames arrive from one camera between two ticks, all four
stay in `events`, and exactly one of them — the newest — takes part in the next
fusion. That is *coalescing*, not loss: the counter is named accordingly.

Scheduling rules:

* Dirty-only. A group with no new source generation since its last tick is
  skipped, so a 30 FPS deployment does not burn 100 fusions a second.
* Monotonic deadlines. Ticks are scheduled against `time.monotonic()` deadlines,
  never `run; sleep(period)`, so a 4 ms tick does not stretch the period to 14 ms.
* No backlog. If a tick overruns its deadline the missed deadlines are counted
  and dropped; the loop resumes at the next future deadline with the newest state.
* Race-safe. A sample arriving while a tick is running bumps the group's pending
  sequence, so the tick that is finishing cannot mark the group clean.

Execution model: one process owns a workspace database. The coordinator keeps
latest-state in memory, so running several ingesting worker processes against one
SQLite workspace would split that state. See docs/realtime-pipeline.md.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import logging
import os
import threading
import time

from .. import db
from . import config_cache
from .metrics import registry

logger = logging.getLogger("storelens.realtime")

# 100 Hz maximum live fusion cadence.
TICK_INTERVAL_S = float(os.environ.get("STORELENS_LIVE_TICK_INTERVAL_S", "0.01"))
ENABLED = os.environ.get("STORELENS_LIVE_SCHEDULER", "1").lower() not in {"0", "false", "no"}

# Ingestion and fusion both write, and SQLite serializes writers regardless, so
# running them on many pool threads buys no parallelism — it only adds GIL
# hand-off and lock convoying between however many threads asyncio's default
# executor happens to have. One dedicated pipeline thread turns that into a
# plain FIFO queue: the same total work, in arrival order, with far less
# scheduling overhead and much tighter tail latency.
pipeline_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="storelens-pipeline")


async def run_in_pipeline(function, *args):
    """Run one unit of pipeline work on the dedicated pipeline thread.

    Mirrors `asyncio.to_thread`, including copying the current context, so the
    demo-session database override still applies inside the call.
    """
    import contextvars
    context = contextvars.copy_context()
    return await asyncio.get_running_loop().run_in_executor(
        pipeline_executor, lambda: context.run(function, *args))


class SourceSnapshot:
    """The newest completed sample for one (workspace, source, entity type)."""

    __slots__ = ("db_path", "source_id", "entity_type", "sample_key", "sample_id",
                 "timestamp", "sequence", "received_at")

    def __init__(self, db_path: str, sample: dict, sequence: int, received_at: float) -> None:
        self.db_path = db_path
        self.source_id = sample["source_id"]
        self.entity_type = sample["entity_type"]
        self.sample_key = sample["sample_key"]
        self.sample_id = sample.get("sample_id")
        self.timestamp = sample["timestamp"]
        self.sequence = sequence
        self.received_at = received_at

    def as_sample(self) -> dict:
        return {
            "source_id": self.source_id, "entity_type": self.entity_type,
            "sample_id": self.sample_id, "sample_key": self.sample_key,
            "timestamp": self.timestamp,
        }

    @property
    def source_key(self) -> tuple:
        return (self.db_path, self.source_id, self.entity_type)


class RealtimeStateCoordinator:
    """Latest-wins source state plus a dirty-group, max-100-Hz fusion scheduler."""

    def __init__(self, interval_s: float = TICK_INTERVAL_S) -> None:
        self.interval_s = interval_s
        self._lock = threading.Lock()
        # Serializes tick execution: a scheduler tick and a read-driven drain
        # must never fuse the same group at the same time.
        self._execution_lock = threading.RLock()
        self._sequence = 0
        self._latest: dict[tuple, SourceSnapshot] = {}
        self._dirty: dict[tuple, int] = {}
        self._consumed: dict[tuple, dict[tuple, int]] = {}
        self._task: asyncio.Task | None = None
        self._running = False

    # -- ingestion side ---------------------------------------------------

    def publish(self, samples: list[dict]) -> None:
        """Record the newest completed samples and mark their groups dirty.

        Called on the ingestion thread *after* the raw evidence has been
        committed. Never blocks on fusion, geometry, or SQLite.
        """
        if not samples:
            return
        db_path = db.current_db_path()
        # Reading group configuration touches SQLite, so resolve it before
        # taking the lock: the critical section stays pure bookkeeping.
        routed = [(sample, config_cache.groups_for_source(sample["source_id"]))
                  for sample in samples]
        received_at = time.monotonic()
        with self._lock:
            for sample, groups in routed:
                self._sequence += 1
                snapshot = SourceSnapshot(db_path, sample, self._sequence, received_at)
                previous = self._latest.get(snapshot.source_key)
                self._latest[snapshot.source_key] = snapshot
                if previous is not None and self._is_unconsumed(previous):
                    # The superseded snapshot will never take part in a live
                    # fusion. Its raw rows remain in `events`.
                    registry.increment("realtime.live_updates_coalesced")
                for group in groups:
                    group_key = (db_path, group["id"], sample["entity_type"])
                    self._dirty[group_key] = self._sequence
        registry.increment("realtime.source_updates", len(samples))
        registry.gauge("realtime.dirty_groups", len(self._dirty))

    def _is_unconsumed(self, snapshot: SourceSnapshot) -> bool:
        for consumed in self._consumed.values():
            if consumed.get(snapshot.source_key, 0) >= snapshot.sequence:
                return False
        return True

    # -- scheduling -------------------------------------------------------

    def pending_groups(self) -> list[tuple]:
        with self._lock:
            return sorted(self._dirty)

    def oldest_pending_age_s(self) -> float:
        """Age of the oldest live update that has not reached fused state yet."""
        with self._lock:
            return self._oldest_pending_age_locked()

    def _oldest_pending_age_locked(self) -> float:
        if not self._dirty:
            return 0.0
        now = time.monotonic()
        unconsumed = [now - snapshot.received_at for snapshot in self._latest.values()
                      if self._is_unconsumed(snapshot)]
        return round(max(unconsumed), 6) if unconsumed else 0.0

    def drain(self, budget: int = 64) -> int:
        """Run every currently dirty group tick synchronously.

        Fused-state reads call this so a read never reports state older than the
        newest committed sample. Bounded by the groups dirty at entry, so it can
        never chase a continuously ingesting workload.
        """
        executed = 0
        for group_key in self.pending_groups()[:budget]:
            executed += 1 if self._run_group_tick(group_key) else 0
        return executed

    def _run_due_ticks(self) -> None:
        for group_key in self.pending_groups():
            self._run_group_tick(group_key)

    def _run_group_tick(self, group_key: tuple) -> bool:
        db_path, group_id, entity_type = group_key
        with self._execution_lock:
            with self._lock:
                pending = self._dirty.get(group_key)
                if pending is None:
                    return False
                consumed = dict(self._consumed.get(group_key, {}))
                staged = [
                    snapshot for snapshot in self._latest.values()
                    if snapshot.db_path == db_path and snapshot.entity_type == entity_type
                    and snapshot.sequence > consumed.get(snapshot.source_key, 0)
                ]
            try:
                with db.using_database(db_path):
                    group = config_cache.group_config()["by_id"].get(group_id)
                    if group is None:
                        # The group was disabled or deleted; stop tracking it.
                        with self._lock:
                            self._dirty.pop(group_key, None)
                            self._consumed.pop(group_key, None)
                        return False
                    members = {int(source_id) for source_id in group["source_ids"]}
                    staged = [snapshot for snapshot in staged if snapshot.source_id in members]
                    started = time.perf_counter()
                    from . import multiview
                    with db.transaction():
                        multiview.run_group_tick(
                            group, entity_type,
                            [snapshot.as_sample() for snapshot in staged], db.now())
                    duration = time.perf_counter() - started
            except Exception:
                # A failed tick must not lose the dirty marker or kill the loop.
                registry.increment("realtime.tick_errors")
                logger.exception("live fusion tick failed", extra={"group_id": group_id})
                return False
        registry.observe("realtime.tick_duration_s", duration)
        registry.increment("realtime.ticks_executed")
        completed_at = time.monotonic()
        with self._lock:
            entry = self._consumed.setdefault(group_key, {})
            for snapshot in staged:
                entry[snapshot.source_key] = snapshot.sequence
                registry.observe("realtime.source_to_combined_state_s",
                                 completed_at - snapshot.received_at)
            # Only clear the dirty marker if nothing new arrived while the tick
            # was running; otherwise the group stays dirty for the next tick.
            if self._dirty.get(group_key) == pending:
                del self._dirty[group_key]
            registry.gauge("realtime.dirty_groups", len(self._dirty))
        return True

    async def _scheduler(self) -> None:
        period = self.interval_s
        deadline = time.monotonic() + period
        while self._running:
            delay = deadline - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            registry.increment("realtime.ticks_requested")
            if self._dirty:
                await run_in_pipeline(self._run_due_ticks)
            else:
                registry.increment("realtime.ticks_skipped_clean")
            now = time.monotonic()
            if deadline <= now:
                # Never queue missed ticks: count them and resume at the next
                # future deadline with whatever the newest source state is.
                missed = int((now - deadline) // period)
                if missed:
                    registry.increment("realtime.deadlines_missed", missed)
                deadline += (missed + 1) * period
            else:
                deadline += period

    # -- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._task is not None or not ENABLED:
            return
        self._running = True
        self._task = asyncio.create_task(self._scheduler())
        logger.info("live fusion scheduler started at %.0f Hz", 1.0 / self.interval_s)

    async def stop(self) -> None:
        self._running = False
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # Anything still dirty is fused before the process exits so the
        # persisted current state matches the persisted raw evidence.
        await asyncio.to_thread(self.drain)

    def reconcile(self) -> int:
        """Rebuild live bookkeeping from persisted state after a restart.

        Seeds the latest snapshot for every source that already has a committed
        sample and marks its groups dirty, so current fused state becomes
        internally coherent without waiting for a camera to send a new frame.
        The seeded snapshots are recorded as already consumed: their associations
        are already persisted, and a restart must not re-run them.
        """
        db_path = db.current_db_path()
        rows = db.q("SELECT source_id,entity_type,sample_id,sample_key,ts FROM "
                    "source_current_samples ORDER BY ts,source_id")
        if not rows:
            return 0
        received_at = time.monotonic()
        marked = 0
        with self._lock:
            for row in rows:
                sample = {"source_id": row["source_id"], "entity_type": row["entity_type"],
                          "sample_id": row["sample_id"], "sample_key": row["sample_key"],
                          "timestamp": row["ts"]}
                self._sequence += 1
                snapshot = SourceSnapshot(db_path, sample, self._sequence, received_at)
                self._latest[snapshot.source_key] = snapshot
                for group in config_cache.groups_for_source(row["source_id"]):
                    group_key = (db_path, group["id"], row["entity_type"])
                    self._dirty[group_key] = self._sequence
                    self._consumed.setdefault(group_key, {})[snapshot.source_key] = snapshot.sequence
                    marked += 1
        return marked

    def reset(self) -> None:
        """Forget all live bookkeeping. Used by tests and workspace reset."""
        with self._lock:
            self._latest.clear()
            self._dirty.clear()
            self._consumed.clear()

    def status(self) -> dict:
        with self._lock:
            return {
                "enabled": ENABLED,
                "running": self._task is not None,
                "max_cadence_hz": round(1.0 / self.interval_s, 3),
                "tracked_sources": len(self._latest),
                "dirty_groups": len(self._dirty),
                "oldest_unconsumed_live_update_s": self._oldest_pending_age_locked(),
            }


coordinator = RealtimeStateCoordinator()


def execution_model() -> dict:
    """Report whether this deployment matches the single-process assumption."""
    declared = os.environ.get("WEB_CONCURRENCY") or os.environ.get("UVICORN_WORKERS") or "1"
    try:
        workers = int(declared)
    except ValueError:
        workers = 1
    return {
        "supported": "single process owns the workspace database",
        "declared_worker_processes": workers,
        "coordinator_scope": "in-process",
        "warning": (None if workers <= 1 else
                    "More than one worker process is configured. Live latest-state and "
                    "the fusion scheduler are process-local, so several ingesting "
                    "processes against one workspace would each hold a partial view. "
                    "Run one process per workspace database."),
    }
