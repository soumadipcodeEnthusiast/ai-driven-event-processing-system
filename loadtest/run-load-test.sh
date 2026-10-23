#!/usr/bin/env bash
# loadtest/run-load-test.sh — JMeter load test against the running local stack.
#
#   loadtest/run-load-test.sh [--rate N] [--duration S] [--threads N] [--batch N] [--api-rate N]
#
# Needs: the stack up (make up), JMeter 5.6+ (on PATH or JMETER_HOME), Maven
# (to fetch the Kafka client jars for JMeter, offline from ~/.m2 when cached).
# Output: loadtest/results/<run id>/ — results.jtl, HTML dashboard (report/),
# summary.md (PASS/FAIL per requirement). Exit status 1 if any check fails.
#
# Targets default to the compose stack's host ports; override to run from
# inside the compose network (the Jenkins "Load test" stage does):
#   KAFKA_BOOTSTRAP, INGESTION_URL, DIAGNOSTIC_HOST/_PORT, FORECASTING_HOST/_PORT, PROMETHEUS_URL
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RATE=2000 DURATION=120 THREADS=8 BATCH=100 API_RATE=30 API_THREADS=4 API_MAX_MS=500
while [[ $# -gt 0 ]]; do
  case "$1" in
    --rate) RATE="$2"; shift 2 ;;
    --duration) DURATION="$2"; shift 2 ;;
    --threads) THREADS="$2"; shift 2 ;;
    --batch) BATCH="$2"; shift 2 ;;
    --api-rate) API_RATE="$2"; shift 2 ;;
    --api-max-ms) API_MAX_MS="$2"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

# JMeter 5.6's Groovy can't run on very new JDKs (e.g. 26), and on macOS its
# launcher picks the newest installed one — pin JDK 21/17 unless JAVA_HOME is set.
if [[ -z "${JAVA_HOME:-}" ]]; then
  for candidate in \
      "$( [[ -x /usr/libexec/java_home ]] && /usr/libexec/java_home -v 21 2>/dev/null )" \
      "$( [[ -x /usr/libexec/java_home ]] && /usr/libexec/java_home -v 17 2>/dev/null )" \
      /opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home \
      /opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home \
      /usr/local/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home; do
    if [[ -n "$candidate" && -x "$candidate/bin/java" ]]; then export JAVA_HOME="$candidate"; break; fi
  done
fi

JMETER="${JMETER_HOME:+$JMETER_HOME/bin/}jmeter"
command -v "$JMETER" >/dev/null || { echo "JMeter not found: install it or set JMETER_HOME" >&2; exit 1; }
KAFKA_BOOTSTRAP="${KAFKA_BOOTSTRAP:-localhost:9092}"
INGESTION_URL="${INGESTION_URL:-http://localhost:9080}"
DIAGNOSTIC_HOST="${DIAGNOSTIC_HOST:-localhost}"
FORECASTING_HOST="${FORECASTING_HOST:-localhost}"
DIAGNOSTIC_PORT="${DIAGNOSTIC_PORT:-9082}"
FORECASTING_PORT="${FORECASTING_PORT:-9081}"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"
curl -sf "$INGESTION_URL/actuator/health" >/dev/null || { echo "stack not reachable at $INGESTION_URL: make up" >&2; exit 1; }

# Kafka client for the JSR223 producer (JMeter doesn't ship one). Taken from
# the ingestion-service boot jar when built (same versions the service uses),
# else resolved with Maven.
LIB="$ROOT/loadtest/.lib"
JARS="kafka-clients lz4-java snappy-java zstd-jni"
if ! ls "$LIB"/kafka-clients-*.jar >/dev/null 2>&1; then
  mkdir -p "$LIB"
  BOOT_JAR="$(ls "$ROOT"/ingestion-service/target/ingestion-service-*.jar 2>/dev/null | head -1 || true)"
  if [[ -n "$BOOT_JAR" ]]; then
    echo "Extracting Kafka client jars from $(basename "$BOOT_JAR") …"
    python3 - "$BOOT_JAR" "$LIB" $JARS <<'PY'
import os, sys, zipfile
jar, out, names = sys.argv[1], sys.argv[2], sys.argv[3:]
with zipfile.ZipFile(jar) as z:
    for entry in z.namelist():
        base = os.path.basename(entry)
        if entry.startswith("BOOT-INF/lib/") and any(base.startswith(n + "-") for n in names):
            with open(os.path.join(out, base), "wb") as fh:
                fh.write(z.read(entry))
PY
  else
    echo "Resolving Kafka client jars with Maven into $LIB …"
    MVN="$(command -v mvn || echo "$ROOT/ingestion-service/mvnw")"
    "$MVN" -q -f "$ROOT/ingestion-service/pom.xml" dependency:copy-dependencies \
      -DincludeArtifactIds="${JARS// /,}" -DoutputDirectory="$LIB"
  fi
fi

RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/loadtest/results/$RUN_ID"
mkdir -p "$OUT"
echo "Run $RUN_ID: ${RATE} events/s for ${DURATION}s (${THREADS} producer threads, batch ${BATCH}), API ${API_RATE} req/s"

START="$(date +%s)"
"$JMETER" -n -t "$ROOT/loadtest/jmeter/event-processing-load.jmx" \
  -Juser.classpath="$LIB" -Jbootstrap="$KAFKA_BOOTSTRAP" \
  -Jdiagnostic_host="$DIAGNOSTIC_HOST" -Jforecasting_host="$FORECASTING_HOST" \
  -Jdiagnostic_port="$DIAGNOSTIC_PORT" -Jforecasting_port="$FORECASTING_PORT" \
  -Jrate="$RATE" -Jduration="$DURATION" -Jproducer_threads="$THREADS" -Jbatch="$BATCH" \
  -Japi_rate="$API_RATE" -Japi_threads="$API_THREADS" -Japi_max_ms="$API_MAX_MS" -Jrun_id="jm-$RUN_ID" \
  -l "$OUT/results.jtl" -j "$OUT/jmeter.log" -e -o "$OUT/report"

echo "Load finished; waiting 40 s for consumers and the archiver to drain…"
sleep 40
END="$(date +%s)"

set +e
python3 "$ROOT/loadtest/analyze.py" --jtl "$OUT/results.jtl" --start "$START" --end "$END" \
  --rate "$RATE" --batch "$BATCH" --duration "$DURATION" --api-max-ms "$API_MAX_MS" \
  --prometheus "$PROMETHEUS_URL" --out "$OUT/summary.md"
status=$?
set -e
echo
echo "HTML dashboard: $OUT/report/index.html"
exit $status
