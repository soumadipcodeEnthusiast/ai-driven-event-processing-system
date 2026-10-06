#!/usr/bin/env bash
# Generate synthetic raw events (docs/CONTRACTS.md §1.1) and publish them to the
# `raw-events` topic through the `kafka` docker-compose container.
set -euo pipefail

COUNT=100
INVALID_RATIO=0
RATE=0
CONTAINER="kafka"
BOOTSTRAP="kafka:29092"
TOPIC="raw-events"
DRY_RUN=false
SEED=""

usage() {
  cat <<'EOF'
Usage: scripts/produce-events.sh [options]

Generate N raw events and pipe them into the compose Kafka container:
  docker exec -i kafka kafka-console-producer --bootstrap-server kafka:29092 --topic raw-events

Options:
  -n, --count N            number of events to send (default: 100)
  -i, --invalid-ratio R    fraction of events that are deliberately invalid, 0.0-1.0 (default: 0)
                           invalid kinds rotate: missing event_id, missing source, timestamp
                           without offset, null payload, non-JSON line, JSON array
  -r, --rate N             events per second; 0 = as fast as possible (default: 0)
  -t, --topic NAME         target topic (default: raw-events)
  -c, --container NAME     Kafka container name (default: kafka)
  -b, --bootstrap HOST:P   bootstrap server inside the container (default: kafka:29092)
  -s, --seed N             random seed for reproducible output (default: time-based)
      --dry-run            print events to stdout instead of producing them
  -h, --help               show this help

Examples:
  scripts/produce-events.sh -n 1000
  scripts/produce-events.sh -n 5000 --invalid-ratio 0.1 --rate 500
  scripts/produce-events.sh -n 5 -i 0.4 --dry-run
EOF
}

die() { echo "error: $*" >&2; exit 2; }

need_value() { [[ $# -ge 2 && -n "$2" ]] || die "option $1 requires a value"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -n|--count)          need_value "$@"; COUNT="$2"; shift 2 ;;
    -i|--invalid-ratio)  need_value "$@"; INVALID_RATIO="$2"; shift 2 ;;
    -r|--rate)           need_value "$@"; RATE="$2"; shift 2 ;;
    -t|--topic)          need_value "$@"; TOPIC="$2"; shift 2 ;;
    -c|--container)      need_value "$@"; CONTAINER="$2"; shift 2 ;;
    -b|--bootstrap)      need_value "$@"; BOOTSTRAP="$2"; shift 2 ;;
    -s|--seed)           need_value "$@"; SEED="$2"; shift 2 ;;
    --dry-run)           DRY_RUN=true; shift ;;
    -h|--help)           usage; exit 0 ;;
    *)                   usage >&2; die "unknown option: $1" ;;
  esac
done

[[ "$COUNT" =~ ^[0-9]+$ ]] || die "--count must be a non-negative integer"
[[ "$RATE" =~ ^[0-9]+$ ]] || die "--rate must be a non-negative integer"
[[ "$INVALID_RATIO" =~ ^(0(\.[0-9]+)?|1(\.0+)?|\.[0-9]+)$ ]] || die "--invalid-ratio must be between 0.0 and 1.0"
[[ -z "$SEED" || "$SEED" =~ ^[0-9]+$ ]] || die "--seed must be an integer"
[[ -n "$SEED" ]] || SEED="$(date +%s)"

RUN_ID="$(date -u +%Y%m%d%H%M%S)-$$"
BASE_TS="$(date -u +%Y-%m-%dT%H:%M:%S)"

# Emits one JSON event per line. Valid events alternate "Z" and "+00:00" offsets.
generate() {
  awk -v count="$COUNT" -v ratio="$INVALID_RATIO" -v seed="$SEED" \
      -v run="$RUN_ID" -v ts="$BASE_TS" '
    BEGIN {
      srand(seed)
      ns = split("checkout-api payments-api auth-service inventory-api", sources, " ")
      nt = split("http_request db_query cache_miss error heartbeat", types, " ")
      nc = split("ingestion-service forecasting-service diagnostic-service kafka zookeeper prometheus graph-db llm", comps, " ")
      invalid_kind = 0
      for (n = 1; n <= count; n++) {
        id = "evt-" run "-" n
        src = sources[int(rand() * ns) + 1]
        typ = types[int(rand() * nt) + 1]
        comp = comps[int(rand() * nc) + 1]
        status = (rand() < 0.05) ? 500 : 200
        latency = int(rand() * 900) + 5
        stamp = ts ((n % 2) ? "Z" : "+00:00")
        payload = "{\"status\":" status ",\"latency_ms\":" latency "}"
        if (ratio > 0 && rand() < ratio) {
          k = invalid_kind++ % 6
          if (k == 0)      print "{\"timestamp\":\"" stamp "\",\"source\":\"" src "\",\"payload\":" payload "}"
          else if (k == 1) print "{\"event_id\":\"" id "\",\"timestamp\":\"" stamp "\",\"payload\":" payload "}"
          else if (k == 2) print "{\"event_id\":\"" id "\",\"timestamp\":\"" ts "\",\"source\":\"" src "\"}"
          else if (k == 3) print "{\"event_id\":\"" id "\",\"timestamp\":\"" stamp "\",\"source\":\"" src "\",\"payload\":null}"
          else if (k == 4) print "this is not json " id
          else             print "[\"" id "\"]"
        } else {
          print "{\"event_id\":\"" id "\",\"timestamp\":\"" stamp "\",\"source\":\"" src \
                "\",\"type\":\"" typ "\",\"component_id\":\"" comp "\",\"payload\":" payload "}"
        }
      }
    }'
}

# Releases at most RATE lines per second (whole-second batches).
throttle() {
  if [[ "$RATE" -eq 0 ]]; then
    cat
    return
  fi
  local i=0 line
  while IFS= read -r line; do
    printf '%s\n' "$line"
    i=$((i + 1))
    if (( i % RATE == 0 )); then
      sleep 1
    fi
  done
}

if $DRY_RUN; then
  generate | throttle
  exit 0
fi

command -v docker >/dev/null 2>&1 || die "docker not found in PATH"
if [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)" != "true" ]]; then
  die "container '$CONTAINER' is not running (start the stack: docker compose -f infra/docker-compose.yml up -d)"
fi

echo "Producing $COUNT events (invalid ratio $INVALID_RATIO, rate ${RATE:-0}/s, seed $SEED) to $TOPIC via $CONTAINER..." >&2
start=$(date +%s)
generate | throttle | docker exec -i "$CONTAINER" \
  kafka-console-producer --bootstrap-server "$BOOTSTRAP" --topic "$TOPIC" \
  --producer-property acks=all --producer-property linger.ms=5
echo "Done in $(( $(date +%s) - start ))s (run id $RUN_ID)." >&2
