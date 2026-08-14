"""Alert rule evaluation, in two parts:

- `evaluate_batch` runs synchronously right after a batch is persisted (so its
  derivation queries see the same-batch rows) and reacts to what just arrived:
  a dwell that just completed, a matched observation, a state that just changed.
- `evaluate_ongoing` runs on a periodic timer (see server/app.py's lifespan
  task, every ALERT_POLL_INTERVAL_S) and does NOT depend on any event arriving.
  This is what makes "still loitering", "occupancy still over threshold",
  "state still stuck", and unified analysis-condition alerts fire even when a
  zone or series goes quiet — the previous design only re-checked ongoing
  conditions when another event happened to land in the same zone, so a sparse
  enter/exit-only zone could loiter forever undetected.

Dwell and state durations are always platform-derived (services/derive.py) —
worker-posted zone_dwell values and state_change/state durations are never
trusted. Webhooks fire on daemon threads so neither ingestion nor the periodic
timer ever blocks on the network.
"""
import json
import threading
import urllib.request

from .. import db
from . import derive

LEGACY_KINDS = {"dwell_exceeds", "occupancy_exceeds", "state_alert", "event_match"}
ONGOING_KINDS = {"dwell_exceeds", "occupancy_exceeds", "state_alert", "analysis_condition", "query_condition"}


def _fire(rule: dict, title: str, message: str, payload: dict, ts: float) -> dict:
    alert_id = db.ex(
        "INSERT INTO alerts (rule_id, ts, title, message, payload_json, space_revision_id, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (rule["id"], ts, title, message, json.dumps(payload), db.current_space_revision_id(), db.now()),
    )
    db.ex("UPDATE alert_rules SET last_fired_at=? WHERE id=?", (ts, rule["id"]))
    alert = {"id": alert_id, "rule_id": rule["id"], "rule_name": rule["name"], "ts": ts,
             "title": title, "message": message, "payload": payload, "acknowledged": 0,
             "status": "new", "note": "", "resolved_at": None}
    if rule.get("webhook_url"):
        threading.Thread(target=_webhook, args=(rule["webhook_url"], alert), daemon=True).start()
    return alert


def _webhook(url: str, alert: dict):
    try:
        req = urllib.request.Request(
            url, data=json.dumps(alert).encode(), headers={"Content-Type": "application/json"}, method="POST"
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass  # best-effort; the alert row is already persisted


def _cooled_down(rule: dict, ts: float) -> bool:
    last = rule.get("last_fired_at")
    return last is None or (ts - last) >= (rule.get("cooldown_s") or 60)


def evaluate_batch(batch: list[dict], zone_names: dict[int, str]) -> list[dict]:
    """batch: enriched events/observations just inserted. Returns alerts fired
    (already persisted). Reacts to completed conditions in this batch; ongoing
    conditions are also checked here for immediate responsiveness, but do not
    rely on this being called again soon — evaluate_ongoing is the backstop."""
    rules = db.q("SELECT * FROM alert_rules WHERE enabled=1")
    if not rules or not batch:
        return []
    fired: list[dict] = []
    batch_max_ts = max(e["ts"] for e in batch)
    for rule in rules:
        p = db.jload(rule["params_json"], {})
        if not _cooled_down(rule, batch_max_ts):
            continue
        alert = None
        if rule["kind"] == "dwell_exceeds":
            zid, secs = p.get("zone_id"), float(p.get("seconds", 60))
            for e in batch:  # completed legacy visits: derive duration for each just-ingested exit
                if e["event_type"] != "zone_exit" or not e.get("track_id") or e.get("zone_id") is None:
                    continue
                if zid is not None and e.get("zone_id") != zid:
                    continue
                dur = derive.dwell_on_exit(e["track_id"], e["zone_id"], e["ts"], max(secs * 4, derive.MAX_DWELL_S))
                if dur is not None and dur >= secs:
                    zn = zone_names.get(e.get("zone_id"), f"zone {e.get('zone_id')}")
                    alert = _fire(rule, rule["name"],
                                  f"Track {e.get('track_id')} dwelled {dur:.0f}s in {zn} (limit {secs:.0f}s)",
                                  {"event": e, "derived_dwell_s": round(dur, 1)}, e["ts"])
                    break
            if alert is None:
                alert = _check_dwell_ongoing(rule, p, zone_names, batch_max_ts)
        elif rule["kind"] == "occupancy_exceeds":
            alert = _check_occupancy(rule, p, zone_names, batch_max_ts)
        elif rule["kind"] == "state_alert":
            label, src, min_s = p.get("label", ""), p.get("source_id"), p.get("min_seconds")
            for e in batch:
                if e["event_type"] not in ("state_change", "state") or \
                        (src is not None and e.get("source_id") != src):
                    continue
                if min_s is None and e.get("label") == label:
                    alert = _fire(rule, rule["name"], f"State changed to '{label}'", {"event": e}, e["ts"])
                    break
            if alert is None and min_s is not None and src is not None:
                alert = _check_state_ongoing(rule, p, batch_max_ts)
        elif rule["kind"] == "event_match":
            etype, zid = p.get("event_type", ""), p.get("zone_id")
            ak, av = p.get("attr_key"), p.get("attr_value")
            for e in batch:
                if etype and e["event_type"] != etype:
                    continue
                if zid is not None and e.get("zone_id") != zid:
                    continue
                if ak and str((e.get("attributes") or {}).get(ak)) != str(av):
                    continue
                zn = zone_names.get(e.get("zone_id"), "")
                alert = _fire(rule, rule["name"],
                              f"Matched {e['event_type']}" + (f" in {zn}" if zn else ""), {"event": e}, e["ts"])
                break
        if alert:
            fired.append(alert)
    return fired


def evaluate_ongoing(now: float, zone_names: dict[int, str]) -> list[dict]:
    """Periodic, batch-independent check of every enabled rule's ongoing/time-
    based condition. Call this on a timer (see app.py); it never needs a new
    observation to run. Returns alerts fired (already persisted)."""
    rules = db.q("SELECT * FROM alert_rules WHERE enabled=1")
    fired: list[dict] = []
    for rule in rules:
        if rule["kind"] not in {"analysis_condition", "query_condition"} and not _cooled_down(rule, now):
            continue
        alert = None
        if rule["kind"] == "dwell_exceeds":
            alert = _check_dwell_ongoing(rule, db.jload(rule["params_json"], {}), zone_names, now)
        elif rule["kind"] == "occupancy_exceeds":
            alert = _check_occupancy(rule, db.jload(rule["params_json"], {}), zone_names, now)
        elif rule["kind"] == "state_alert":
            p = db.jload(rule["params_json"], {})
            if p.get("min_seconds") is not None and p.get("source_id") is not None:
                alert = _check_state_ongoing(rule, p, now)
        elif rule["kind"] in {"analysis_condition", "query_condition"}:
            alert = _check_analysis_condition(rule, now)
        if alert:
            fired.append(alert)
    return fired


def _check_dwell_ongoing(rule: dict, p: dict, zone_names: dict, now: float) -> dict | None:
    """Tracks still inside a zone past the threshold, from BOTH legacy
    zone_enter/zone_exit and current-contract tracked detections
    (derive.derive_visits) — checked unconditionally, not gated on recent
    batch content, so a sparse zone still gets caught while someone loiters."""
    zid, secs = p.get("zone_id"), float(p.get("seconds", 60))
    lookback = max(secs * 4, derive.MAX_DWELL_S)
    visits, _ = derive.derive_visits(now - lookback, now, zid, lookback)
    opens = [v for v in visits if not v["completed"] and v["value"] >= secs]
    if not opens:
        return None
    v = max(opens, key=lambda o: o["value"])
    zn = zone_names.get(v["zone_id"], f"zone {v['zone_id']}")
    return _fire(rule, rule["name"],
                f"Track {v['track_id']} has been in {zn} for {v['value']:.0f}s and counting (limit {secs:.0f}s)",
                {"open_visit": {k: v[k] for k in ("zone_id", "track_id", "t0", "value")}}, now)


def _check_occupancy(rule: dict, p: dict, zone_names: dict, now: float) -> dict | None:
    zid, count, win = p.get("zone_id"), int(p.get("count", 5)), float(p.get("window_s", 60))
    where, args = "zone_id IS NOT NULL", []
    if zid is not None:
        where, args = "zone_id=?", [zid]
    row = db.q1(
        f"SELECT COUNT(DISTINCT track_id) n FROM events WHERE {where} AND ts>=? AND ts<=? "
        "AND track_id IS NOT NULL AND space_revision_id=?",
        (*args, now - win, now, db.current_space_revision_id()),
    )
    n = row["n"] if row else 0
    if n < count:
        return None
    zn = zone_names.get(zid, "the store") if zid is not None else "the store"
    return _fire(rule, rule["name"], f"{n} people in {zn} over the last {win:.0f}s (limit {count})",
                {"occupancy": n, "zone_id": zid}, now)


def _check_state_ongoing(rule: dict, p: dict, now: float) -> dict | None:
    """Still in `label` past the threshold (e.g. fridge door left open), using
    the coalesced current-state interval so repeated heartbeat samples of the
    same label don't reset the duration."""
    label, src, min_s = p.get("label", ""), p.get("source_id"), float(p.get("min_seconds"))
    entity_id = p.get("entity_id")
    interval = derive.current_state_interval(src, p.get("name") or "", entity_id, now)
    if not interval or interval["label"] != label or interval["stale"]:
        return None
    dur = now - interval["start"]
    if dur < min_s:
        return None
    return _fire(rule, rule["name"], f"State '{label}' ongoing for {dur:.0f}s (limit {min_s:.0f}s)",
                {"source_id": src, "since_ts": interval["start"], "derived_duration_s": round(dur, 1)}, now)


_COMPARATORS = {
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
}


def _check_analysis_condition(rule: dict, now: float) -> dict | None:
    """Evaluate a unified analysis (subject/measures/filters) as a scalar KPI
    over a short trailing window and compare it against `condition`. `for_seconds`
    is tracked in condition_state_json across polls — a threshold only fires once
    it has held continuously for that long, and resets the moment it stops."""
    from ..routers import analytics_query
    from ..routers.analyses import serialize as serialize_query
    condition = db.jload(rule["condition_json"], {})
    if rule["kind"] == "query_condition":
        query_id = db.jload(rule["params_json"], {}).get("query_id")
        saved = db.q1("SELECT * FROM analyses WHERE id=?", (query_id,)) if query_id else None
        if not saved:
            return None
        definition = serialize_query(saved)
        analysis = {"name": definition["name"], "subject": definition["subject"],
                    "measures": definition["measures"], "filters": definition["filters"]}
    else:
        analysis = db.jload(rule["analysis_json"], {})
    subject, measures = analysis.get("subject"), analysis.get("measures") or []
    if subject not in analytics_query.SUBJECTS or not measures:
        return None
    window_s = float(condition.get("window_s", 300))
    q = analytics_query.QueryIn(
        subject=subject, measures=measures, filters=analysis.get("filters", {}),
        grouping=analytics_query.GroupingIn(),
        range=analytics_query.RangeIn(since=now - window_s, until=now),
    )
    result = analytics_query.query_analytics(q)
    value = result["rows"][0].get(measures[0]) if result["rows"] else None
    quality = result["rows"][0].get("quality", "known") if result["rows"] else "unknown"
    op, threshold, for_s = condition.get("operator", ">"), condition.get("value"), float(condition.get("for_seconds", 0))
    comparator = _COMPARATORS.get(op)
    valid_quality = quality == "known" or bool(condition.get("allow_partial", False) and quality == "partial")
    holds = (comparator(value, threshold) if
             (valid_quality and comparator and value is not None and isinstance(value, (int, float))) else False)
    state = db.jload(rule.get("condition_state_json"), {})
    if not valid_quality:
        # Unknown/partial evidence cannot assert a false transition or clear an
        # already-active edge. Preserve state until known evidence returns.
        return None
    if not holds:
        if state:
            db.ex("UPDATE alert_rules SET condition_state_json='{}' WHERE id=?", (rule["id"],))
        return None
    true_since = state.get("true_since") or now
    db.ex("UPDATE alert_rules SET condition_state_json=? WHERE id=?",
          (json.dumps({"true_since": true_since, "active": True,
                       "fired": bool(state.get("fired"))}), rule["id"]))
    if now - true_since < for_s or state.get("fired") or not _cooled_down(rule, now):
        return None
    alert = _fire(rule, rule["name"],
                f"{measures[0]} {op} {threshold} for {now - true_since:.0f}s "
                f"(query: {analysis.get('name', subject)})",
                {"subject": subject, "measure": measures[0], "value": value,
                 "condition": condition, "held_since": true_since, "quality": quality,
                 "query_id": db.jload(rule["params_json"], {}).get("query_id"),
                 "evidence_window": result.get("metadata", {}).get("evidence_window"),
                 "query_result": {"shape": result.get("shape"), "rows": result.get("rows"),
                                  "metadata": result.get("metadata")}}, now)
    db.ex("UPDATE alert_rules SET condition_state_json=? WHERE id=?",
          (json.dumps({"true_since": true_since, "active": True, "fired": True}), rule["id"]))
    return alert
