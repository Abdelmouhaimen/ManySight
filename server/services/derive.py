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
            if r["ts"] >= t0 and r["ts"] >= since:
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


# ---------------------------------------------------------------------------
# Current-contract derivation (schema_version=2): workers submit only
# detection/measurement/state observations. Everything below derives visits,
# state intervals, and measurement aggregates from those raw rows, and is
# designed to be merged with the legacy functions above so historical
# zone_enter/zone_exit/state_change rows keep contributing to the same
# analytics. All thresholds are explicit and overridable per call, per the
# platform's "configurable rules" requirement.
# ---------------------------------------------------------------------------

MAX_GAP_S = 45.0            # gap between same-zone detections before a visit session closes
MIN_CONFIRM_SAMPLES = 2     # detections required before a visit is confirmed (debounces boundary jitter)
STATE_STALE_S = 120.0       # a state sample older than this no longer counts as "current"
PRESENCE_TIMEOUT_S = 30.0   # an entity is "active" if detected within this window


def derive_visits_from_detections(since: float, until: float, zone_id: int | None = None,
                                   gap_s: float = MAX_GAP_S, min_samples: int = MIN_CONFIRM_SAMPLES,
                                   max_dwell_s: float = MAX_DWELL_S) -> tuple[list[dict], int]:
    """Zone visits derived purely from ordered, zone-assigned `detection` rows
    grouped by (entity_id, zone_id) -- the current contract's substitute for
    worker-authored zone_enter/zone_exit pairs.

    Consecutive detections for the same entity in the same zone belong to one
    visit as long as no gap between them exceeds `gap_s` (bridges missed frames
    or brief occlusion); a larger gap, or the entity/zone changing, closes the
    visit. A visit is only counted once it has at least `min_samples` confirmed
    detections, so a single noisy frame at a zone boundary is never a confirmed
    entry or exit. Returns (visits, open_count) in the same shape as
    `derive_dwells`, so callers can merge legacy and current-contract visits.
    """
    where = ["ts BETWEEN ? AND ?", "event_type='detection'", "track_id IS NOT NULL", "zone_id IS NOT NULL"]
    args: list = [since - max_dwell_s, until]
    if zone_id is not None:
        where.append("zone_id=?")
        args.append(zone_id)
    rows = db.q(
        f"SELECT ts, track_id, zone_id, attributes FROM events WHERE {' AND '.join(where)}"
        f" ORDER BY track_id, zone_id, ts, id", args)
    visits: list[dict] = []
    open_count = 0

    def emit(session: dict, end: float, completed: bool):
        nonlocal open_count
        if session["samples"] < min_samples or end <= since:
            return
        if not completed:
            open_count += 1
        visits.append({
            "zone_id": session["zone_id"], "track_id": session["track_id"], "t0": session["t0"],
            "value": min(end - session["t0"], max_dwell_s),
            "attributes": session["attributes"], "completed": completed,
        })

    session = None
    for r in rows:
        key = (r["track_id"], r["zone_id"])
        if session is not None and (session["track_id"], session["zone_id"]) == key \
                and r["ts"] - session["last_ts"] <= gap_s:
            session["last_ts"] = r["ts"]
            session["samples"] += 1
            continue
        if session is not None:
            # A different entity/zone follows, or the gap was exceeded: this
            # session's fate is certain within the queried window -- it ended.
            emit(session, session["last_ts"], completed=True)
        session = {"track_id": r["track_id"], "zone_id": r["zone_id"], "t0": r["ts"],
                  "last_ts": r["ts"], "samples": 1, "attributes": db.jload(r["attributes"], {})}
    if session is not None:
        # The last session in the window may still be ongoing.
        stale = (until - session["last_ts"]) > gap_s
        emit(session, session["last_ts"] if stale else until, completed=stale)
    return visits, open_count


def derive_visits(since: float, until: float, zone_id: int | None = None,
                  max_dwell_s: float = MAX_DWELL_S) -> tuple[list[dict], int]:
    """Zone visits from BOTH sources: legacy worker-authored zone_enter/zone_exit
    pairs (derive_dwells) and current-contract detection sessions
    (derive_visits_from_detections). This is the function analytics and alerts
    should call — it keeps historical data replayable while requiring nothing
    but tracked detections from current workers."""
    legacy_visits, legacy_open = derive_dwells(since, until, zone_id, max_dwell_s)
    detection_visits, detection_open = derive_visits_from_detections(since, until, zone_id, max_dwell_s=max_dwell_s)
    return legacy_visits + detection_visits, legacy_open + detection_open


def coalesce_state_intervals(rows: list[dict], until: float,
                             stale_s: float = STATE_STALE_S) -> tuple[list[dict], bool]:
    """rows: ordered {ts, label} samples for ONE (source_id, name, entity_id) key.
    Workers may send repeated identical samples every heartbeat; repeated
    identical samples must not create repeated transitions, so consecutive
    same-label samples are coalesced into one interval before duration or
    transition-count math ever sees them. The trailing interval is marked
    stale — and its end clipped to last_sample_ts + stale_s rather than
    extended to `until` — once the most recent sample is older than `stale_s`,
    so an unresponsive worker can never make a state look verified forever.
    Returns (intervals, is_current_stale)."""
    coalesced: list[dict] = []
    for r in rows:
        if coalesced and coalesced[-1]["label"] == r["label"]:
            coalesced[-1]["samples"] += 1
            coalesced[-1]["last_sample_ts"] = r["ts"]
            continue
        coalesced.append({"label": r["label"], "start": r["ts"], "last_sample_ts": r["ts"], "samples": 1})
    if not coalesced:
        return [], False
    intervals = []
    for i, iv in enumerate(coalesced):
        end = coalesced[i + 1]["start"] if i + 1 < len(coalesced) else None
        intervals.append({"label": iv["label"], "start": iv["start"], "samples": iv["samples"],
                          "last_sample_ts": iv["last_sample_ts"], "end": end, "stale": False})
    last = intervals[-1]
    stale = (until - last["last_sample_ts"]) > stale_s
    last["end"] = (last["last_sample_ts"] + stale_s) if stale else until
    last["stale"] = stale
    return intervals, stale


def state_keys(since: float, until: float, source_id: int | None = None) -> list[tuple]:
    """Distinct (source_id, name, entity_id) state series with samples in range."""
    where, args = ["event_type='state'", "ts BETWEEN ? AND ?", "name IS NOT NULL"], [since, until]
    if source_id is not None:
        where.append("source_id=?")
        args.append(source_id)
    rows = db.q(f"SELECT DISTINCT source_id, name, track_id FROM events WHERE {' AND '.join(where)}", args)
    return [(r["source_id"], r["name"], r["track_id"]) for r in rows]


def state_samples(source_id: int, name: str, entity_id: str | None, since: float, until: float) -> list[dict]:
    where = ["event_type='state'", "source_id=?", "name=?", "ts<=?"]
    args: list = [source_id, name, until]
    if entity_id is None:
        where.append("track_id IS NULL")
    else:
        where.append("track_id=?")
        args.append(entity_id)
    return db.q(f"SELECT ts, label FROM events WHERE {' AND '.join(where)} ORDER BY ts, id", args)


def current_state_interval(source_id: int, name: str, entity_id: str | None, now: float,
                           stale_s: float = STATE_STALE_S) -> dict | None:
    """The current (possibly stale) coalesced interval for one state series, or
    None if it has never reported."""
    rows = state_samples(source_id, name, entity_id, now - 30 * 24 * 3600, now)
    intervals, _ = coalesce_state_intervals(rows, now, stale_s)
    return intervals[-1] if intervals else None


def measurement_series(since: float, until: float, name: str, source_id: int | None = None,
                       entity_id: str | None = None, label: str | None = None) -> list[dict]:
    """Ordered raw samples for one measurement series: {ts, value, value_kind,
    source_id, track_id, label, attributes}. Callers that only need the
    aggregate math (aggregate_measurement) can ignore the extra columns; callers
    doing further filtering (e.g. the unified analytics query engine) need them
    on the row without a second query."""
    where = ["event_type='measurement'", "name=?", "ts BETWEEN ? AND ?", "value IS NOT NULL"]
    args: list = [name, since, until]
    if source_id is not None:
        where.append("source_id=?")
        args.append(source_id)
    if entity_id is not None:
        where.append("track_id=?")
        args.append(entity_id)
    if label is not None:
        where.append("label=?")
        args.append(label)
    return db.q(
        f"SELECT ts, value, value_kind, source_id, track_id, label, attributes FROM events"
        f" WHERE {' AND '.join(where)} ORDER BY ts, id", args)


def aggregate_measurement(rows: list[dict]) -> dict:
    """Aggregate one ordered measurement series, respecting `value_kind`. Gauge
    samples are never summed by default (a sampled instantaneous value has no
    meaningful sum). Cumulative series get counter-reset detection so a worker
    restart never produces a negative rate; delta series are summed and rated
    directly. Aggregation must always respect value_kind — never guess."""
    if not rows:
        return {"latest": None, "minimum": None, "maximum": None, "average": None,
               "sum": None, "samples": 0, "rate": None}
    values = [r["value"] for r in rows]
    kind = rows[-1]["value_kind"] or "gauge"
    duration = max(rows[-1]["ts"] - rows[0]["ts"], 1e-9)
    base = {"latest": values[-1], "minimum": min(values), "maximum": max(values),
           "average": sum(values) / len(values), "samples": len(values)}
    if kind == "delta":
        total = sum(values)
        return {**base, "sum": total, "rate": total / duration if len(values) > 1 else None}
    if kind == "cumulative":
        increase = sum(cur - prev for prev, cur in zip(values, values[1:]) if cur >= prev)
        return {**base, "sum": None, "rate": increase / duration if len(values) > 1 else None}
    return {**base, "sum": None, "rate": None}  # gauge
