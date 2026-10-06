#!/usr/bin/env bash
# scripts/setup-local.sh
#
# Bootstrap the AI-Driven Event Processing local stack (infra/docker-compose.yml)
# and wait until every service reports healthy.
#
# Usage:
#   ./scripts/setup-local.sh [--profile llm|neo4j]... [--no-build] [--logs] [--timeout SECS]
#   ./scripts/setup-local.sh --down
#
# Options:
#   --profile NAME  Enable an optional compose profile (repeatable):
#                     llm    Ollama + one-shot model pull (LLM_MODEL, default llama3)
#                     neo4j  Neo4j graph DB (also export GRAPH_BACKEND=neo4j to use it)
#   --no-build      Don't rebuild service images
#   --logs          Follow logs once the stack is healthy
#   --timeout SECS  Max seconds to wait for health (default 300)
#   --down          Tear down containers, networks and volumes (all profiles)
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
PROFILES=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --down)      ACTION="down" ;;
    --logs)      FOLLOW_LOGS=true ;;
    --no-build)  BUILD_FLAG="" ;;
    --profile)
      [[ $# -ge 2 ]] || { error "--profile needs a value (llm|neo4j)"; exit 1; }
      shift
      case "$1" in
        llm|neo4j) PROFILES+=("$1") ;;
        *) error "Unknown profile '$1' (expected llm or neo4j)"; exit 1 ;;
      esac
      ;;
    --profile=*)
      p="${1#--profile=}"
      case "$p" in
        llm|neo4j) PROFILES+=("$p") ;;
        *) error "Unknown profile '$p' (expected llm or neo4j)"; exit 1 ;;
      esac
      ;;
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
for p in "${PROFILES[@]+"${PROFILES[@]}"}"; do
  COMPOSE+=(--profile "$p")
done

# ─── Tear-down ────────────────────────────────────────────────────────────────
if [[ "$ACTION" == "down" ]]; then
  info "Tearing down the local stack (all profiles)…"
  "${COMPOSE[@]}" --profile llm --profile neo4j down --volumes --remove-orphans
  info "Stack removed."
  exit 0
fi

# ─── Bring up ─────────────────────────────────────────────────────────────────
info "Starting AI-Driven Event Processor local stack"
info "Compose file: ${COMPOSE_FILE}"
if [[ ${#PROFILES[@]} -gt 0 ]]; then
  info "Profiles:     ${PROFILES[*]}"
fi

# shellcheck disable=SC2086  # BUILD_FLAG is intentionally empty or one word
"${COMPOSE[@]}" up -d ${BUILD_FLAG}

# ─── Wait for health ──────────────────────────────────────────────────────────
# Long-running services with a healthcheck; one-shot jobs (kafka-init,
# ollama-pull) are checked separately for a successful exit.
WAIT_FOR=(zookeeper kafka prometheus grafana ingestion-service diagnostic-service forecasting-service)
for p in "${PROFILES[@]+"${PROFILES[@]}"}"; do
  case "$p" in
    llm)   WAIT_FOR+=(ollama) ;;
    neo4j) WAIT_FOR+=(neo4j) ;;
  esac
done

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

kinit_cid="$("${COMPOSE[@]}" ps -a -q kafka-init 2>/dev/null || true)"
if [[ -n "$kinit_cid" ]] && [[ "$(docker inspect -f '{{.State.ExitCode}}' "$kinit_cid")" != "0" ]]; then
  error "kafka-init failed; see: ${COMPOSE[*]} logs kafka-init"
  exit 1
fi

info "All services healthy."
echo
info "Endpoints:"
info "  Grafana (REQ-K dashboard): http://localhost:3000   (anonymous viewer; admin/admin)"
info "  Prometheus:                http://localhost:9090"
info "  ingestion health:          http://localhost:8080/actuator/health"
info "  forecasting health:        http://localhost:8081/health   (alerts: /alerts)"
info "  diagnostic health:         http://localhost:8082/health   (playbooks: /playbooks)"
info "  Kafka (host):              localhost:9092"
for p in "${PROFILES[@]+"${PROFILES[@]}"}"; do
  case "$p" in
    llm)
      info "  Ollama:                    http://localhost:11434"
      warn "  The model pull (ollama-pull) may still be running; until it finishes,"
      warn "  playbooks use the rule-based fallback. Follow: ${COMPOSE[*]} logs -f ollama-pull"
      ;;
    neo4j)
      info "  Neo4j browser:             http://localhost:7474  (bolt :7687)"
      ;;
  esac
done
echo
info "Send sample events:  make produce   (or ./scripts/produce-events.sh)"
info "Stop & remove:       ./scripts/setup-local.sh --down"

if [[ "$FOLLOW_LOGS" == "true" ]]; then
  "${COMPOSE[@]}" logs -f
fi
