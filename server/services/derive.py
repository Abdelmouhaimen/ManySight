"""Platform-side derivation of dwell visits and state durations from raw observations.

The platform never trusts worker-computed aggregates: `zone_dwell.value` and
`state_change.value`/`attributes.prev_label` are stored as observations but ignored.
Dwell is always derived from zone_enter/zone_exit pairs; state durations from
consecutive state_change timestamps. Used by both analytics and the alert engine so
the logic exists exactly once.
"""
from .. import db

# Visits longer than this are capped: an enter without a matching exit (dead track,
# missed detection) would otherwise dwell forever.
MAX_DWELL_S = 3600.0


def derive_dwells(since: float, until: float, zone_id: int | None = None,
                  max_dwell_s: float = MAX_DWELL_S) -> tuple[list[dict], int]:
    """Pair zone_enter/zone_exit per (track, zone) into visits.

    Returns (visits, open_count). Each visit: {zone_id, track_id, t0, value,
    attributes, completed}. Completed visits are attributed to the window their exit
    falls in; still-open visits are clipped to `until` with completed=False.
    A lookback of max_dwell_s before `since` catches visits that started earlier.
    """
    where = ["ts BETWEEN ? AND ?", "event_type IN ('zone_enter','zone_exit')",
             "track_id IS NOT NULL", "zone_id IS NOT NULL"]
    args: list = [since - max_dwell_s, until]
    if zone_id is not None:
        where.append("zone_id=?")
        args.append(zone_id)
    rows = db.q(
        f"SELECT ts, event_type, track_id, zone_id, attributes FROM events"
        f" WHERE {' AND '.join(where)} ORDER BY ts, id", args)
    open_at: dict[tuple, tuple] = {}
    visits: list[dict] = []
    for r in rows:
        key = (r["track_id"], r["zone_id"])
        if r["event_type"] == "zone_enter":
            # keep-first on duplicate enters: debounce flicker is far more common
            # than a missed exit, and max_dwell_s bounds the damage of the latter.
            open_at.setdefault(key, (r["ts"], db.jload(r["attributes"], {})))
        elif key in open_at:
            t0, attrs = open_at.pop(key)
            if r["ts"] > t0 and r["ts"] >= since:
                visits.append({"zone_id": r["zone_id"], "track_id": r["track_id"], "t0": t0,
                               "value": min(r["ts"] - t0, max_dwell_s),
                               "attributes": db.jload(r["attributes"], {}) or attrs,
                               "completed": True})
    open_count = 0
    for (track, zid), (t0, attrs) in open_at.items():
        if until > t0:
            open_count += 1
            visits.append({"zone_id": zid, "track_id": track, "t0": t0,
                           "value": min(until - t0, max_dwell_s),
                           "attributes": attrs, "completed": False})
    return visits, open_count


def dwell_on_exit(track_id: str, zone_id: int, exit_ts: float,
                  lookback_s: float = MAX_DWELL_S) -> float | None:
    """Dwell duration for a just-ingested zone_exit: seconds since the latest
    zone_enter of the same (track, zone) within lookback_s, or None if unmatched."""
    row = db.q1(
        "SELECT MAX(ts) t0 FROM events WHERE event_type='zone_enter'"
        " AND track_id=? AND zone_id=? AND ts<? AND ts>=?",
        (track_id, zone_id, exit_ts, exit_ts - lookback_s))
    if not row or row["t0"] is None:
        return None
    return exit_ts - row["t0"]


def open_dwells(now: float, zone_id: int | None = None, min_seconds: float = 0,
                lookback_s: float = MAX_DWELL_S) -> list[dict]:
    """Tracks currently inside a zone (enter without exit) for at least min_seconds."""
    visits, _ = derive_dwells(now - lookback_s, now, zone_id, max_dwell_s=lookback_s)
    return [v for v in visits if not v["completed"] and v["value"] >= min_seconds]


def state_before(source_id: int, ts: float) -> dict | None:
    """Latest state_change for a source strictly before ts: {ts, label} or None."""
    return db.q1(
        "SELECT ts, label FROM events WHERE event_type='state_change' AND source_id=?"
        " AND ts<? ORDER BY ts DESC, id DESC LIMIT 1", (source_id, ts))


def current_state(source_id: int, now: float) -> dict | None:
    """Latest state_change for a source at or before now: {ts, label} or None."""
    return db.q1(
        "SELECT ts, label FROM events WHERE event_type='state_change' AND source_id=?"
        " AND ts<=? ORDER BY ts DESC, id DESC LIMIT 1", (source_id, now))
