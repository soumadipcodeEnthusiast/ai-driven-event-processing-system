#!/usr/bin/env bash
# scripts/demo.sh — guided live demo of the running stack.
#
#   make up && ./scripts/demo.sh          # pauses after each step (press Enter)
#   DEMO_AUTO=1 ./scripts/demo.sh         # no pauses (rehearsal / CI)
#
# Each step prints what it shows, runs the real commands against the stack
# and leaves it as it found it (thresholds are restored).
set -euo pipefail
cd "$(dirname "$0")/.."

BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[0;32m'; CYAN=$'\033[0;36m'; RESET=$'\033[0m'
INGEST=http://localhost:9080
DIAG=http://localhost:9082
ARCHIVER=http://localhost:9083

step() { printf '\n%s━━ %s ━━%s\n' "$BOLD$CYAN" "$1" "$RESET"; }
say()  { printf '%s%s%s\n' "$DIM" "$*" "$RESET"; }
run()  { printf '%s$ %s%s\n' "$GREEN" "$*" "$RESET"; }
pause() { [[ "${DEMO_AUTO:-}" == "1" ]] || read -r -p $'\n⏎  Enter for the next step… ' _; }
json() { python3 -m json.tool 2>/dev/null || cat; }
post() { curl -s -X "$1" "$2" -H 'content-type: application/json' ${3:+-d "$3"}; }
metric() { curl -s "$1/metrics" 2>/dev/null | grep -E "^($2)" || true; }

FORECAST=http://localhost:9081

step "0. Pre-flight: is everything up?"
for url in "$INGEST/actuator/health" "$DIAG/health" "$FORECAST/health" "$ARCHIVER/metrics" http://localhost:9090/-/healthy http://localhost:9030/api/health; do
  if curl -sf --max-time 5 "$url" >/dev/null; then echo "  ✔ $url"; else echo "  ✘ $url — start the stack first: make up"; exit 1; fi
done
say "Prefer clicking? The same demo runs in the browser: http://localhost:9099 (make console)."
say "Open in the browser now: Grafana http://localhost:9030 (dashboard: AIOps Service Health),"
say "MinIO http://localhost:9001 (minioadmin / minioadmin), APIs $FORECAST/docs and $DIAG/docs"
pause

step "1. Send traffic: 300 events, 10% deliberately broken"
say "Producers publish JSON events to the Kafka topic raw-events. Some are malformed on purpose."
run "./scripts/produce-events.sh -n 300 -i 0.1"
./scripts/produce-events.sh -n 300 -i 0.1 | tail -1
sleep 6
pause

step "2. Ingestion: validate → normalise, or dead-letter (REQ-001/005/008)"
say "ingestion-service (Java) checks every event against the schema."
run "curl $INGEST/actuator/prometheus | grep ingestion_events_total"
curl -s "$INGEST/actuator/prometheus" | grep '^ingestion_events_total' | sed 's/application="ingestion-service",//'
say $'\nA valid event after normalisation (UTC time, defaults, schema_version, ingested_at):'
docker exec kafka kafka-console-consumer --bootstrap-server kafka:29092 --topic normalized-events \
  --max-messages 1 --timeout-ms 8000 2>/dev/null | json || true
say $'\nA rejected event in the dead-letter topic, with the reason in its headers:'
docker exec kafka kafka-console-consumer --bootstrap-server kafka:29092 --topic raw-events.DLT \
  --from-beginning --max-messages 1 --timeout-ms 8000 --property print.headers=true 2>/dev/null \
  | tr ',' '\n' | head -4 || true
pause

step "3. Anomaly detection: every event gets a score 0–1 (REQ-016/017/019)"
say "diagnostic-service learns what 'normal' looks like per service and flags outliers."
say "Teaching it a baseline: 25 checkout requests at ~100 ms…"
for i in $(seq 1 25); do
  post POST "$DIAG/score/demo-base-$i" "{\"event\":{\"source\":\"demo-checkout\",\"type\":\"request\",\"payload\":{\"latency_ms\":$((100 + i % 5))}}}" >/dev/null
done
say $'\nA normal request (102 ms):'
run "POST $DIAG/score/demo-normal"
post POST "$DIAG/score/demo-normal" '{"event":{"source":"demo-checkout","type":"request","payload":{"latency_ms":102}}}' | json
say $'\nA latency spike (950 ms):'
run "POST $DIAG/score/demo-spike"
post POST "$DIAG/score/demo-spike" '{"event":{"source":"demo-checkout","type":"request","payload":{"latency_ms":950}}}' | json
sleep 2
say $'\nThe spike is now on the audit topic anomaly-audit:'
docker exec kafka kafka-console-consumer --bootstrap-server kafka:29092 --topic anomaly-audit \
  --from-beginning --timeout-ms 8000 2>/dev/null | grep '"demo-spike"' | tail -1 | json || true
say $'\nEvery event from step 1 was scored automatically off Kafka too:'
metric "$DIAG" 'anomaly_events_scored_total|anomalies_detected_total|anomaly_audit_published_total'
pause

step "4. Change it live — no restart (REQ-020, REQ-018)"
run "PUT $DIAG/thresholds {\"threshold\":0.9}"
post PUT "$DIAG/thresholds" '{"threshold":0.9}'; echo
run "PUT $DIAG/model {\"model_version\":\"ewma-zscore-demo\"}"
post PUT "$DIAG/model" '{"model_version":"ewma-zscore-demo","warm_start":true}'; echo
say "Putting the threshold back to 0.8."
post PUT "$DIAG/thresholds" '{"threshold":0.8}' >/dev/null
pause

step "5. Predictive analytics: forecast the next 60 minutes (REQ-021..025)"
say "forecasting-service reads CPU, memory and error rate from Prometheus and predicts ahead."
run "POST $FORECAST/forecast {\"component_id\":\"ingestion-service\"}"
post POST "$FORECAST/forecast" '{"component_id":"ingestion-service"}' > /tmp/demo-forecast.json
python3 - /tmp/demo-forecast.json <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); det = d["details"]
print(f"  forecaster={det['forecaster']}  latency={det['latency_seconds']} s  alert_emitted={d['alert_emitted']}")
for name, m in det["metrics"].items():
    print(f"  {name:<20} now={m.get('last_value')}  p90 peak={m.get('max_upper_90')}  "
          f"threshold={m.get('threshold')}  confidence={m.get('confidence')}  -> {m.get('outcome')}")
PY
if grep -q insufficient_data /tmp/demo-forecast.json; then
  say $'\nCPU/memory need ~10 minutes of metric history after a (re)start before they can be'
  say "forecast — that's why they show insufficient_data. Start the stack ~15 min before the demo."
  say "Skipping the simulated alert; re-run ./scripts/demo.sh later to show it."
  pause
else
say $'\nNow simulate a problem: lower the CPU threshold so the forecast predicts a breach.'
run "PUT $FORECAST/thresholds/ingestion-service {\"cpu_utilisation\":0.001}"
post PUT "$FORECAST/thresholds/ingestion-service" '{"cpu_utilisation":0.001}'; echo
post POST "$FORECAST/forecast" '{"component_id":"ingestion-service"}' > /tmp/demo-forecast.json
python3 - /tmp/demo-forecast.json <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
a = d.get("alert") or {}
print(f"  alert_emitted={d['alert_emitted']}")
if a:
    print(f"  PredictiveAlert {a['alert_id']}: {a['component_id']}/{a['metric_name']} "
          f"severity={a['severity']} predicted breach {a['predicted_breach_time']} (lead {a['lead_time_minutes']:.0f} min)")
PY
run "GET $FORECAST/alerts/summary"
curl -s "$FORECAST/alerts/summary" | json
say "Resolving the demo alert and restoring the threshold (0.80)."
for id in $(curl -s "$FORECAST/alerts?status=open&component_id=ingestion-service" | python3 -c 'import json,sys; print(" ".join(a["alert_id"] for a in json.load(sys.stdin)))'); do
  post POST "$FORECAST/alerts/$id/resolve" >/dev/null
done
post PUT "$FORECAST/thresholds/ingestion-service" '{"cpu_utilisation":0.80}' >/dev/null
pause
fi

step "6. Archival: everything lands in S3 (REQ-028..030)"
say "event-archiver batches both topics into gzipped files, partitioned by date and hour."
say "Waiting up to 35 s for the current batch to flush…"
sleep 35
run "mc ls --recursive local/event-archive | tail"
docker exec -e HOME=/tmp minio sh -c 'mc alias set l http://127.0.0.1:9000 minioadmin minioadmin >/dev/null && mc ls --recursive l/event-archive | tail -6 && echo "objects in bucket: $(mc ls --recursive l/event-archive | wc -l)"' 2>/dev/null \
  || say "(archive bucket not reachable — check the minio container)"
metric "$ARCHIVER" 'archive_records_total|archive_objects_total'
pause

step "7. Watch it live"
say "Grafana  http://localhost:9030  → AIOps Service Health: throughput, DLT, anomalies, archival"
say "Prometheus http://localhost:9090/alerts  → alert rules and their state"
say "MinIO    http://localhost:9001  → bucket event-archive/events/…"
say "APIs     $FORECAST/docs  and  $DIAG/docs  (try them from the browser)"
printf '\n%sDemo complete.%s\n' "$BOLD" "$RESET"
