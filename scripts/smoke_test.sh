#!/usr/bin/env bash
# StoreLens platform-slice smoke test.
# Prereqs: server running (uvicorn server.app:app --port 8000) on a freshly
# seeded DB (python scripts/seed_demo.py). Requires curl + python for JSON checks.
set -e
BASE="${STORELENS_URL:-http://localhost:8000}/api/v1"
PY=python
command -v python >/dev/null || PY=python3

jqpy() { $PY -c "import json,sys; d=json.load(sys.stdin); $1"; }
pass() { echo "  ok: $1"; }

echo "1. health"
curl -sf "$BASE/health" >/dev/null && pass "server up"

echo "2. events pagination"
R1=$(curl -sf "$BASE/events?limit=5")
echo "$R1" | jqpy "assert d['count']==5 and d['total']>5 and d['next_cursor'], d" && pass "page 1: total + next_cursor"
CUR=$(echo "$R1" | jqpy "print(d['next_cursor'])")
R2=$(curl -sf "$BASE/events?limit=5&cursor=$CUR")
$PY - "$R1" "$R2" <<'EOF'
import json, sys
a, b = json.loads(sys.argv[1]), json.loads(sys.argv[2])
ids1 = {e["id"] for e in a["events"]}; ids2 = {e["id"] for e in b["events"]}
assert not ids1 & ids2, "cursor pages overlap"
print("  ok: page 2 has no duplicates")
EOF
curl -s -o /dev/null -w "%{http_code}" "$BASE/events?cursor=garbage" | grep -q 422 && pass "malformed cursor -> 422"
curl -sf "$BASE/events?event_type=zone_enter&limit=1" | jqpy "assert d['events'][0]['event_type']=='zone_enter'" && pass "event_type filter"

echo "3. derive-only dwell"
NOW=$($PY -c "import time; print(time.time())")
D0=$(curl -sf "$BASE/analytics/dwell")
echo "$D0" | jqpy "assert d['derived'] is True and d['rows'], d" && pass "dwell derived from seeded enter/exit"
# a lone zone_dwell with a huge value must NOT change analytics
ZID=$(curl -sf "$BASE/zones" | jqpy "print(d[0]['id'])")
curl -sf -X POST "$BASE/events" -H 'Content-Type: application/json' -d "{
  \"events\": [{\"event_type\": \"zone_dwell\", \"track_id\": \"smoke-legacy\", \"zone_id\": $ZID, \"value\": 999999}]}" >/dev/null
D1=$(curl -sf "$BASE/analytics/dwell")
$PY - "$D0" "$D1" <<'EOF'
import json, sys
a, b = json.loads(sys.argv[1]), json.loads(sys.argv[2])
ta = sum(r["total_s"] for r in a["rows"]); tb = sum(r["total_s"] for r in b["rows"])
assert abs(ta - tb) < 1, f"zone_dwell value leaked into analytics: {ta} vs {tb}"
print("  ok: zone_dwell value ignored by analytics")
EOF
# open visit: enter without exit shows up as open_visits
curl -sf -X POST "$BASE/events" -H 'Content-Type: application/json' -d "{
  \"events\": [{\"event_type\": \"zone_enter\", \"track_id\": \"smoke-open\", \"zone_id\": $ZID, \"ts\": $($PY -c "import time; print(time.time()-30)")}]}" >/dev/null
curl -sf "$BASE/analytics/dwell" | jqpy "assert d['open_visits'] >= 1, d" && pass "open visit counted"
curl -sf -X POST "$BASE/events" -H 'Content-Type: application/json' -d "{
  \"events\": [{\"event_type\": \"zone_exit\", \"track_id\": \"smoke-open\", \"zone_id\": $ZID}]}" >/dev/null && pass "visit closed"

echo "4. derived alerts"
CHECKOUT=$(curl -sf "$BASE/zones" | jqpy "print(next(z['id'] for z in d if z['ztype']=='checkout'))")
T_ENTER=$($PY -c "import time; print(time.time()-130)")
N_BEFORE=$(curl -sf "$BASE/alerts?limit=1000" | jqpy "print(len(d))")
curl -sf -X POST "$BASE/events" -H 'Content-Type: application/json' -d "{
  \"events\": [
    {\"event_type\": \"zone_enter\", \"track_id\": \"smoke-loiter\", \"zone_id\": $CHECKOUT, \"ts\": $T_ENTER},
    {\"event_type\": \"zone_exit\",  \"track_id\": \"smoke-loiter\", \"zone_id\": $CHECKOUT}
  ]}" | jqpy "assert d['alerts'] >= 1, d" && pass "dwell_exceeds fired on derived 130s visit"
curl -sf "$BASE/alerts?limit=1" | jqpy "assert 'dwelled 13' in d[0]['message'], d[0]['message']" && pass "message shows derived duration"

echo "5. insight registry"
curl -sf "$BASE/insights" | jqpy "assert len(d)==5, len(d)" && pass "5 seeded insights"
curl -sf "$BASE/insights?pinned=true" | jqpy "assert len(d)==2, len(d)" && pass "2 pinned"
NEW=$(curl -sf -X POST "$BASE/insights" -H 'Content-Type: application/json' \
  -d '{"title":"Smoke insight","block":"line","dataset":"occupancy"}')
IID=$(echo "$NEW" | jqpy "print(d['id'])")
curl -sf -X PUT "$BASE/insights/$IID" -H 'Content-Type: application/json' -d '{"pinned":true}' \
  | jqpy "assert d['pinned'] is True" && pass "update"
curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/insights" -H 'Content-Type: application/json' \
  -d '{"title":"bad","block":"line","dataset":"heatmap"}' | grep -q 422 && pass "invalid block/dataset -> 422"
curl -sf "$BASE/insights/templates" | jqpy "assert d['templates'], d" && pass "templates assembled"
curl -sf -X DELETE "$BASE/insights/$IID" >/dev/null && pass "delete"

echo "6. zone from camera pixels"
SRC=$(curl -sf "$BASE/sources" | jqpy "print(next(s['id'] for s in d if s['name']=='Entrance cam'))")
UNCAL=$(curl -sf "$BASE/sources" | jqpy "print(next(s['id'] for s in d if s['name']=='Fridge cam'))")
Z=$(curl -sf -X POST "$BASE/zones" -H 'Content-Type: application/json' -d "{
  \"name\": \"Smoke restricted\", \"ztype\": \"restricted\", \"source_id\": $SRC,
  \"polygon_px\": [{\"x\":300,\"y\":400},{\"x\":900,\"y\":400},{\"x\":900,\"y\":650},{\"x\":300,\"y\":650}]}")
echo "$Z" | jqpy "assert d['ztype']=='restricted' and all(0 < p['x'] < 25 and 0 < p['y'] < 15 for p in d['polygon']), d" \
  && pass "pixel polygon projected to plausible map meters"
SZID=$(echo "$Z" | jqpy "print(d['id'])")
curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE/zones" -H 'Content-Type: application/json' -d "{
  \"name\": \"bad\", \"source_id\": $UNCAL, \"polygon_px\": [{\"x\":1,\"y\":1},{\"x\":2,\"y\":1},{\"x\":2,\"y\":2}]}" \
  | grep -q 409 && pass "uncalibrated camera -> 409"
curl -sf -X DELETE "$BASE/zones/$SZID" >/dev/null && pass "cleanup"

echo "7. plane geometry, evidence provenance, and worker lifecycle"
SURFACE=$(curl -sf -X POST "$BASE/projection-surfaces" -H 'Content-Type: application/json' -d "{
  \"source_id\": $SRC, \"name\": \"Smoke elevated plane\", \"kind\": \"platform\", \"height_m\": 0.5,
  \"points\": [
    {\"px\":{\"x\":300,\"y\":300},\"map\":{\"x\":10,\"y\":8}},
    {\"px\":{\"x\":900,\"y\":300},\"map\":{\"x\":14,\"y\":8}},
    {\"px\":{\"x\":900,\"y\":650},\"map\":{\"x\":14,\"y\":11}},
    {\"px\":{\"x\":300,\"y\":650},\"map\":{\"x\":10,\"y\":11}}
  ], \"frame_w\": 1280, \"frame_h\": 720}")
SID=$(echo "$SURFACE" | jqpy "print(d['id'])")
echo "$SURFACE" | jqpy "assert d['revision']==1 and d['kind']=='platform', d" && pass "named plane created"
VIEW=$(curl -sf -X POST "$BASE/zone-views" -H 'Content-Type: application/json' -d "{
  \"zone_id\": $CHECKOUT, \"source_id\": $SRC, \"projection_surface_id\": $SID,
  \"outer_polygon_px\":[{\"x\":300,\"y\":300},{\"x\":900,\"y\":300},{\"x\":900,\"y\":650},{\"x\":300,\"y\":650}],
  \"detection_polygon_px\":[{\"x\":350,\"y\":350},{\"x\":850,\"y\":350},{\"x\":850,\"y\":620},{\"x\":350,\"y\":620}],
  \"membership_rule\":\"bbox_overlap\", \"threshold\":0.5}")
VID=$(echo "$VIEW" | jqpy "print(d['id'])")
JOB=$(curl -sf -X POST "$BASE/jobs" -H 'Content-Type: application/json' -d "{
  \"name\":\"Smoke plane job\",\"source_ids\":[$SRC],\"event_types\":[\"detection\"]}")
JID=$(echo "$JOB" | jqpy "print(d['id'])")
WORKER=$(curl -sf -X POST "$BASE/workers" -H 'Content-Type: application/json' -d "{
  \"job_id\":$JID,\"name\":\"smoke-worker\",\"version\":\"1\"}")
WID=$(echo "$WORKER" | jqpy "print(d['id'])")
curl -sf -X POST "$BASE/workers/$WID/heartbeat" -H 'Content-Type: application/json' \
  -d '{"status":"running","metrics":{"fps":4}}' \
  | jqpy "assert d['effective_status']=='running' and d['metrics']['fps']==4, d" && pass "worker heartbeat running"
curl -sf -X POST "$BASE/events" -H 'Content-Type: application/json' -d "{
  \"job_id\":$JID,\"events\":[{\"source_id\":$SRC,\"event_type\":\"detection\",\"track_id\":\"smoke-person\",
  \"bbox\":[400,400,200,150],\"keypoints\":[{\"x\":500,\"y\":480,\"name\":\"hip\",\"confidence\":0.9}],
  \"point_kind\":\"bbox_bottom_center\"}]}" \
  | jqpy "assert d['zone_view_assigned']==1 and d['projected']==1, d" && pass "zone view assigned + named plane projected"
curl -sf "$BASE/events?job_id=$JID&limit=1" \
  | jqpy "e=d['events'][0]; assert e['bbox'] and e['keypoints'] and e['projection_surface_id']==$SID and e['zone_view_id']==$VID and e['surface_revision']==1 and e['zone_view_revision']==1, e" \
  && pass "evidence and geometry revisions persisted"
curl -sf -X PUT "$BASE/workers/$WID/desired-state" -H 'Content-Type: application/json' -d '{"desired_state":"restart"}' >/dev/null
curl -sf -X POST "$BASE/workers/$WID/heartbeat" -H 'Content-Type: application/json' -d '{"status":"running"}' \
  | jqpy "assert d['should_stop'] and d['restart_requested'], d" && pass "restart command reaches heartbeat"
curl -sf -X DELETE "$BASE/zone-views/$VID" >/dev/null
curl -sf -X DELETE "$BASE/projection-surfaces/$SID" >/dev/null
curl -sf -X DELETE "$BASE/jobs/$JID?purge_events=true" >/dev/null && pass "geometry/worker cleanup"

echo
echo "All smoke checks passed."
