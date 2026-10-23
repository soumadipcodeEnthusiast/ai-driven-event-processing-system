#!/usr/bin/env bash
# ci/jenkins/run-local.sh — run the repo's Jenkinsfile on a local Jenkins in Docker.
#
#   ci/jenkins/run-local.sh              snapshot the working tree, (re)start Jenkins, run a build
#   ci/jenkins/run-local.sh --load-test  … and run the JMeter stage (needs the stack up + JMETER_HOME)
#   ci/jenkins/run-local.sh --no-build   snapshot + start Jenkins only
#   ci/jenkins/run-local.sh --stop       stop and remove the Jenkins container
#
# Jenkins builds a *snapshot clone* of the current working tree (including
# uncommitted changes) under $JENKINS_DIR — the repo itself is never committed
# to or modified. UI: http://127.0.0.1:9095 (local only, no login).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
JENKINS_DIR="${JENKINS_DIR:-$HOME/.cache/ai-event-jenkins}"
IMAGE="ai-event-processor/jenkins:local"
NAME="ai-event-jenkins"
PORT="${JENKINS_PORT:-9095}"
URL="http://127.0.0.1:${PORT}"
JOB="ai-event-processor"

info() { printf '\033[0;32m[INFO]\033[0m  %s\n' "$*"; }
die()  { printf '\033[0;31m[ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

if [[ "${1:-}" == "--stop" ]]; then
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  info "Stopped $NAME (state kept in $JENKINS_DIR)."
  exit 0
fi

# ── 1. Snapshot the working tree into a throwaway git repo ───────────────────
snapshot="$JENKINS_DIR/snapshot"
TF_CACHE="$HOME/.terraform.d/plugin-cache"   # shared with `make tf-validate` on the host
mkdir -p "$JENKINS_DIR/home" "$JENKINS_DIR/m2" "$JENKINS_DIR/pip-cache" "$TF_CACHE"
if [[ ! -d "$snapshot/.git" ]]; then
  git init -q -b ci-snapshot "$snapshot"
  git -C "$snapshot" config user.email "ci@localhost"
  git -C "$snapshot" config user.name "local-ci"
fi
info "Snapshotting $REPO_ROOT (tracked + untracked, honouring .gitignore)…"
python3 - "$REPO_ROOT" "$snapshot" <<'PY'
import os, shutil, subprocess, sys
src, dst = sys.argv[1], sys.argv[2]
files = {f for f in subprocess.run(
    ["git", "-C", src, "ls-files", "-co", "--exclude-standard", "-z"],
    check=True, capture_output=True).stdout.decode().split("\0") if f and os.path.isfile(os.path.join(src, f))}
old = {f for f in subprocess.run(
    ["git", "-C", dst, "ls-files", "-z"], check=True, capture_output=True).stdout.decode().split("\0") if f}
for f in old - files:                      # deleted in the working tree
    os.remove(os.path.join(dst, f))
for f in files:
    target = os.path.join(dst, f)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    shutil.copy2(os.path.join(src, f), target)
print(f"  {len(files)} files")
PY
git -C "$snapshot" add -A
git -C "$snapshot" commit -q --allow-empty -m "snapshot of $(cd "$REPO_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo working-tree) + local changes ($(date -u +%FT%TZ))"
info "Snapshot commit: $(git -C "$snapshot" rev-parse --short HEAD)"

# Seed Maven's cache once from the host so ./mvnw doesn't download everything.
if [[ -d "$HOME/.m2" && ! -e "$JENKINS_DIR/m2/.seeded" ]]; then
  info "Seeding Maven cache from ~/.m2 (first run only)…"
  rsync -a "$HOME/.m2/" "$JENKINS_DIR/m2/" && touch "$JENKINS_DIR/m2/.seeded"
fi

# ── 2. Start Jenkins ─────────────────────────────────────────────────────────
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -t "$IMAGE" "$REPO_ROOT/ci/jenkins"
if ! docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  info "Starting Jenkins on $URL …"
  # JENKINS_HOME is mounted at the same path as on the host, so pipeline steps
  # that run `docker run -v "$PWD":…` through the host's Docker socket resolve
  # workspace paths correctly.
  extra=()
  if [[ -n "${JMETER_HOME:-}" ]]; then
    extra+=(-v "$JMETER_HOME:/opt/jmeter:ro" -e JMETER_HOME=/opt/jmeter)
  fi
  docker run -d --name "$NAME" --user root "${extra[@]+"${extra[@]}"}" \
    -p "127.0.0.1:${PORT}:8080" \
    -e JENKINS_HOME="$JENKINS_DIR/home" \
    -e REPO_URL="file:///repo" \
    -e TF_PLUGIN_CACHE="$TF_CACHE" \
    -e KAFKA_BOOTSTRAP=kafka:29092 -e INGESTION_URL=http://ingestion-service:8080 \
    -e DIAGNOSTIC_HOST=diagnostic-service -e FORECASTING_HOST=forecasting-service \
    -e DIAGNOSTIC_PORT=8082 -e FORECASTING_PORT=8081 \
    -e PROMETHEUS_URL=http://prometheus:9090 \
    -e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
    -e JAVA_OPTS="-Djenkins.install.runSetupWizard=false -Dhudson.plugins.git.GitSCM.ALLOW_LOCAL_CHECKOUT=true" \
    -v "$JENKINS_DIR/home:$JENKINS_DIR/home" \
    -v "$snapshot:/repo:ro" \
    -v "$JENKINS_DIR/m2:/root/.m2" \
    -v "$JENKINS_DIR/pip-cache:/root/.cache/pip" \
    -v /var/run/docker.sock:/var/run/docker.sock \
    "$IMAGE" >/dev/null
fi
# Join the compose network (when the stack is up) so the load-test stage can
# reach kafka:29092, ingestion-service:8080, … by service name.
if docker network inspect ai-event-processor_event-net >/dev/null 2>&1; then
  docker network connect ai-event-processor_event-net "$NAME" 2>/dev/null || true
fi
for _ in $(seq 1 90); do
  curl -sf "$URL/job/$JOB/api/json" >/dev/null 2>&1 && break
  sleep 2
done
curl -sf "$URL/job/$JOB/api/json" >/dev/null || die "Jenkins did not come up; see: docker logs $NAME"
info "Jenkins is up: $URL/job/$JOB/"

[[ "${1:-}" == "--no-build" ]] && exit 0
LOAD_TEST=false
[[ "${1:-}" == "--load-test" ]] && LOAD_TEST=true

# ── 3. Trigger a build and follow it ─────────────────────────────────────────
jar="$(mktemp)"; trap 'rm -f "$jar"' EXIT
crumb="$(curl -sf -c "$jar" "$URL/crumbIssuer/api/json" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["crumbRequestField"]+":"+d["crumb"])')"
next="$(curl -sf "$URL/job/$JOB/api/json?tree=nextBuildNumber" | python3 -c 'import json,sys; print(json.load(sys.stdin)["nextBuildNumber"])')"
curl -sf -b "$jar" -H "$crumb" -X POST "$URL/job/$JOB/buildWithParameters" \
  --data "RUN_LOAD_TEST=$LOAD_TEST" --data "LOAD_RATE=${LOAD_RATE:-2000}" --data "LOAD_DURATION=${LOAD_DURATION:-60}" >/dev/null
info "Triggered build #$next: $URL/job/$JOB/$next/console"

until curl -sf "$URL/job/$JOB/$next/api/json?tree=building,result" 2>/dev/null \
      | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if not d["building"] and d["result"] else 1)' 2>/dev/null; do
  docker ps --format '{{.Names}}' | grep -qx "$NAME" || die "Jenkins container stopped while build #$next was running"
  sleep 10
done

# Per-stage summary: pipeline-stage-view's REST API, else stage markers from the console.
summary="$(mktemp)"
if curl -sf "$URL/job/$JOB/$next/wfapi/describe" -o "$summary" 2>/dev/null; then
  python3 - "$summary" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"\nBuild #{d['id']}: {d['status']} in {d['durationMillis'] / 60000:.1f} min")
for s in d["stages"]:
    print(f"  {s['status']:<14}{s['durationMillis'] / 1000:>6.0f}s  {s['name']}")
PY
else
  echo; echo "Stages:"
  curl -sf "$URL/job/$JOB/$next/consoleText" | grep -E '^\[Pipeline\] \{ \(|^Stage ".*" skipped|^Finished:' | sed 's/^\[Pipeline\] { (/  ▸ /; s/)$//'
fi
rm -f "$summary"
result="$(curl -sf "$URL/job/$JOB/$next/api/json?tree=result" | python3 -c 'import json,sys; print(json.load(sys.stdin)["result"])')"
info "Console: $URL/job/$JOB/$next/console"
[[ "$result" == "SUCCESS" ]]
