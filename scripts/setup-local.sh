#!/usr/bin/env bash
# scripts/setup-local.sh
#
# Bootstrap helper for the AI-Driven Event Processing Architecture local stack.
# Runs: docker-compose up --build for all services + infrastructure.
#
# Usage:
#   chmod +x scripts/setup-local.sh
#   ./scripts/setup-local.sh [--down] [--logs]
#
# Options:
#   --down    Tear down and remove all containers + volumes
#   --logs    Follow logs after bringing services up (default: detached)

set -euo pipefail

# ─── Resolve project root ─────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${PROJECT_ROOT}/infra/docker-compose.yml"

# ─── Colour helpers ───────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
RESET='\033[0m'

info()  { echo -e "${GREEN}[INFO]${RESET}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
error() { echo -e "${RED}[ERROR]${RESET} $*" >&2; }

# ─── Parse flags ──────────────────────────────────────────────────────────────
ACTION="up"
FOLLOW_LOGS=false

for arg in "$@"; do
  case $arg in
    --down)  ACTION="down" ;;
    --logs)  FOLLOW_LOGS=true ;;
    --help)
      echo "Usage: $(basename "$0") [--down] [--logs]"
      exit 0
      ;;
    *)
      error "Unknown argument: $arg"
      exit 1
      ;;
  esac
done

# ─── Prerequisite checks ──────────────────────────────────────────────────────
check_command() {
  if ! command -v "$1" &>/dev/null; then
    error "Required command '$1' not found. Please install it and retry."
    exit 1
  fi
}

check_command docker
check_command docker compose 2>/dev/null || check_command docker-compose

# Prefer the docker compose plugin; fall back to docker-compose binary
if docker compose version &>/dev/null 2>&1; then
  COMPOSE_CMD="docker compose"
else
  COMPOSE_CMD="docker-compose"
fi

# ─── Tear-down ────────────────────────────────────────────────────────────────
if [[ "$ACTION" == "down" ]]; then
  info "Tearing down the local stack…"
  ${COMPOSE_CMD} -f "${COMPOSE_FILE}" down --volumes --remove-orphans
  info "Stack removed."
  exit 0
fi

# ─── Bring up ─────────────────────────────────────────────────────────────────
info "Starting AI-Driven Event Processor local stack…"
info "Compose file: ${COMPOSE_FILE}"
info ""
info "Services:"
info "  ● Zookeeper          :2181"
info "  ● Kafka              :9092"
info "  ● Prometheus         :9090"
info "  ● ingestion-service  :8080  (Spring Boot)"
info "  ● forecasting-service:8081  (FastAPI / TFT stub)"
info "  ● diagnostic-service :8082  (FastAPI / GraphRAG stub)"
info ""
warn "NOTE: All application services will start but stub methods will raise"
warn "      errors at runtime — this is expected in the scaffold phase."
info ""

BUILD_FLAG="--build"

if [[ "$FOLLOW_LOGS" == "true" ]]; then
  ${COMPOSE_CMD} -f "${COMPOSE_FILE}" up ${BUILD_FLAG}
else
  ${COMPOSE_CMD} -f "${COMPOSE_FILE}" up ${BUILD_FLAG} -d
  info ""
  info "Stack is up. Useful commands:"
  info "  Follow all logs:    ${COMPOSE_CMD} -f infra/docker-compose.yml logs -f"
  info "  Stop & remove:      ./scripts/setup-local.sh --down"
  info "  Prometheus UI:      http://localhost:9090"
  info "  ingestion health:   http://localhost:8080/actuator/health"
  info "  forecasting health: http://localhost:8081/health"
  info "  diagnostic health:  http://localhost:8082/health"
fi
