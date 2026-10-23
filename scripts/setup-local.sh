#!/usr/bin/env bash
# scripts/setup-local.sh
#
# Bootstrap the AI-Driven Event Processing local stack (infra/docker-compose.yml)
# and wait until every service reports healthy.
#
# There are no optional compose profiles any more (the former `llm` and
# `neo4j` profiles were removed with diagnostic-service's rescoping to
# anomaly detection — see docs/adr/0008); MinIO (REQ-028 local S3 stand-in)
# is now always part of the stack.
#
# Usage:
#   ./scripts/setup-local.sh [--no-build] [--logs] [--timeout SECS]
#   ./scripts/setup-local.sh --down
#
# Options:
#   --no-build      Don't rebuild service images
#   --logs          Follow logs once the stack is healthy
#   --timeout SECS  Max seconds to wait for health (default 300)
#   --down          Tear down containers, networks and volumes
#   -h, --help      Show this help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${PROJECT_ROOT}/infra/docker-compose.yml"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

info()  { echo -e "${GREEN}[INFO]${RESET}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }

usage() { sed -n '3,20p' "$0" | sed 's/^# \{0,1\}//'; }

# ─── Parse flags ──────────────────────────────────────────────────────────────
ACTION="up"
FOLLOW_LOGS=false
BUILD_FLAG="--build"
TIMEOUT=300

while [[ $# -gt 0 ]]; do
  case "$1" in
    --down)      ACTION="down" ;;
    --logs)      FOLLOW_LOGS=true ;;
    --no-build)  BUILD_FLAG="" ;;
    --timeout)
      [[ $# -ge 2 && "$2" =~ ^[0-9]+$ ]] || { error "--timeout needs a number of seconds"; exit 1; }
      shift; TIMEOUT="$1"
      ;;
    -h|--help)   usage; exit 0 ;;
    *)           error "Unknown argument: $1"; usage; exit 1 ;;
  esac
  shift
done

# ─── Prerequisites ────────────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  error "Required command 'docker' not found. Install Docker and retry."
  exit 1
fi

# Prefer the `docker compose` plugin; fall back to the standalone binary.
if docker compose version &>/dev/null; then
  COMPOSE=(docker compose)
elif command -v docker-compose &>/dev/null; then
  COMPOSE=(docker-compose)
else
  error "Neither 'docker compose' nor 'docker-compose' is available."
  exit 1
fi

if ! docker info &>/dev/null; then
  error "The Docker daemon is not running. Start Docker and retry."
  exit 1
fi

COMPOSE+=(-f "${COMPOSE_FILE}")

# ─── Tear-down ────────────────────────────────────────────────────────────────
if [[ "$ACTION" == "down" ]]; then
  info "Tearing down the local stack…"
  "${COMPOSE[@]}" down --volumes --remove-orphans
  info "Stack removed."
  exit 0
fi

# ─── Bring up ─────────────────────────────────────────────────────────────────
info "Starting AI-Driven Event Processor local stack"
info "Compose file: ${COMPOSE_FILE}"

# After an unclean stop (Docker quit, laptop sleep), ZooKeeper still holds the
# old broker registration until its session expires (~20 s), and Kafka exits
# with NodeExistsException. Wait it out and try once more.
# shellcheck disable=SC2086  # BUILD_FLAG is intentionally empty or one word
if ! "${COMPOSE[@]}" up -d ${BUILD_FLAG}; then
  if "${COMPOSE[@]}" logs --since 5m kafka 2>/dev/null | grep -q NodeExistsException; then
    info "Kafka hit a stale ZooKeeper registration from the last run; retrying in 30 s…"
    sleep 30
    "${COMPOSE[@]}" up -d
  else
    error "docker compose up failed; see: ${COMPOSE[*]} logs"
    exit 1
  fi
fi

# ─── Wait for health ──────────────────────────────────────────────────────────
# Long-running services with a healthcheck; one-shot jobs (kafka-init,
# minio-init, ollama-pull) are checked separately for a successful exit.
WAIT_FOR=(zookeeper kafka prometheus grafana minio neo4j ollama ingestion-service diagnostic-service forecasting-service event-archiver demo-console)

container_health() {
  local cid
  cid="$("${COMPOSE[@]}" ps -q "$1" 2>/dev/null || true)"
  [[ -n "$cid" ]] || { echo "missing"; return; }
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$cid" 2>/dev/null || echo "missing"
}

info "Waiting up to ${TIMEOUT}s for services to become healthy…"
deadline=$(( $(date +%s) + TIMEOUT ))
while :; do
  pending=()
  for svc in "${WAIT_FOR[@]}"; do
    status="$(container_health "$svc")"
    case "$status" in
      healthy|running) ;;
      *) pending+=("${svc}=${status}") ;;
    esac
  done
  if [[ ${#pending[@]} -eq 0 ]]; then
    break
  fi
  if (( $(date +%s) >= deadline )); then
    error "Timed out waiting for: ${pending[*]}"
    error "Inspect with: ${COMPOSE[*]} logs <service>"
    exit 1
  fi
  sleep 5
done

for job in kafka-init minio-init ollama-pull; do
  job_cid="$("${COMPOSE[@]}" ps -a -q "$job" 2>/dev/null || true)"
  if [[ -n "$job_cid" ]] && [[ "$(docker inspect -f '{{.State.ExitCode}}' "$job_cid")" != "0" ]]; then
    error "$job failed; see: ${COMPOSE[*]} logs $job"
    exit 1
  fi
done

info "All services healthy."
echo
info "Endpoints:"
info "  Demo console (start here):  http://localhost:9099   (live flow, Kafka, guided demo)"
info "  Grafana (REQ-034 dashboard): http://localhost:9030   (anonymous viewer; admin/admin)"
info "  Prometheus:                http://localhost:9090"
info "  ingestion health:          http://localhost:9080/actuator/health"
info "  forecasting health:        http://localhost:9081/health   (alerts: /alerts)"
info "  diagnostic health:         http://localhost:9082/health   (anomaly detection)"
info "  event-archiver metrics:    http://localhost:9083/metrics  (Kafka → S3/MinIO)"
info "  MinIO console:             http://localhost:9001  (API: localhost:9000)"
info "  Neo4j browser (GraphRAG):  http://localhost:9074  (neo4j / localdevpassword)"
info "  Ollama (LLM):              http://localhost:9434"
info "  Kafka (host):              localhost:9092"
echo
info "Send sample events:  make produce   (or ./scripts/produce-events.sh)"
info "Train the TFT:       make train-tft  (needs ~3 h of metric history)"
info "Stop & remove:       ./scripts/setup-local.sh --down"

if [[ "$FOLLOW_LOGS" == "true" ]]; then
  "${COMPOSE[@]}" logs -f
fi
