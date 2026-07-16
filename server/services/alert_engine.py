"""Alert rule evaluation. Runs synchronously on each ingested event batch;
webhooks fire on daemon threads so ingestion never blocks on the network."""
import json
import threading
import urllib.request

from .. import db


def _fire(rule: dict, title: str, message: str, payload: dict, ts: float) -> dict:
    alert_id = db.ex(
        "INSERT INTO alerts (rule_id, ts, title, message, payload_json, created_at) VALUES (?,?,?,?,?,?)",
        (rule["id"], ts, title, message, json.dumps(payload), db.now()),
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


def evaluate(batch: list[dict], zone_names: dict[int, str]) -> list[dict]:
    """batch: enriched events just inserted. Returns alerts fired (already persisted)."""
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
            for e in batch:
                val = float(e.get("value") or 0)
                if e["event_type"] == "zone_dwell" and (zid is None or e.get("zone_id") == zid) and val >= secs:
                    zn = zone_names.get(e.get("zone_id"), f"zone {e.get('zone_id')}")
                    alert = _fire(rule, rule["name"],
                                  f"Track {e.get('track_id')} dwelled {val:.0f}s in {zn} (limit {secs:.0f}s)",
                                  {"event": e}, e["ts"])
                    break
        elif rule["kind"] == "occupancy_exceeds":
            zid, count, win = p.get("zone_id"), int(p.get("count", 5)), float(p.get("window_s", 60))
            if any(e.get("zone_id") == zid or zid is None for e in batch if e.get("zone_id") is not None):
                where, args = "zone_id IS NOT NULL", []
                if zid is not None:
                    where, args = "zone_id=?", [zid]
                row = db.q1(
                    f"SELECT COUNT(DISTINCT track_id) n FROM events WHERE {where} AND ts>=? AND ts<=? AND track_id IS NOT NULL",
                    (*args, batch_max_ts - win, batch_max_ts),
                )
                n = row["n"] if row else 0
                if n >= count:
                    zn = zone_names.get(zid, "the store") if zid is not None else "the store"
                    alert = _fire(rule, rule["name"],
                                  f"{n} people in {zn} over the last {win:.0f}s (limit {count})",
                                  {"occupancy": n, "zone_id": zid}, batch_max_ts)
        elif rule["kind"] == "state_alert":
            label, src, min_s = p.get("label", ""), p.get("source_id"), p.get("min_seconds")
            for e in batch:
                if e["event_type"] != "state_change" or (src is not None and e.get("source_id") != src):
                    continue
                if min_s is not None:
                    # fires when a state *ends*: e.value carries the finished state's duration,
                    # e.attributes.prev_label the state that just ended
                    prev = (e.get("attributes") or {}).get("prev_label")
                    val = float(e.get("value") or 0)
                    if prev == label and val >= float(min_s):
                        alert = _fire(rule, rule["name"],
                                      f"State '{label}' lasted {val:.0f}s (limit {float(min_s):.0f}s)",
                                      {"event": e}, e["ts"])
                        break
                elif e.get("label") == label:
                    alert = _fire(rule, rule["name"], f"State changed to '{label}'", {"event": e}, e["ts"])
                    break
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
