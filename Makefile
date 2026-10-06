# AI-Driven Event Processing Architecture — developer entry points.
#   make up PROFILES="llm neo4j"    # optional compose profiles
#   make test | lint | k8s-validate

SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

COMPOSE_FILE  := infra/docker-compose.yml
PROFILES      ?=
PROFILE_FLAGS := $(foreach p,$(PROFILES),--profile $(p))
COMPOSE       := docker compose -f $(COMPOSE_FILE) $(PROFILE_FLAGS)
PY_SERVICES   := forecasting-service diagnostic-service
PYTHON        ?= python3
KUBECONFORM_VERSION ?= v0.8.0

# Use ./mvnw when the wrapper exists, otherwise a system mvn.
MVN := $(if $(wildcard ingestion-service/mvnw),./mvnw,mvn)
# The service targets Java 17; use a Homebrew JDK 17 when present, else the default JDK.
JAVA17_HOME ?= $(firstword $(wildcard /opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home /usr/local/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home))

.PHONY: help up down logs ps build test test-java test-python lint compose-validate produce k8s-validate

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up: ## Build + start the stack and wait for health (PROFILES="llm neo4j")
	./scripts/setup-local.sh $(foreach p,$(PROFILES),--profile $(p))

down: ## Stop the stack and remove volumes (all profiles)
	./scripts/setup-local.sh --down

logs: ## Follow logs (SERVICE=name for one service)
	$(COMPOSE) logs -f $(SERVICE)

ps: ## Show container status
	$(COMPOSE) ps -a

build: ## Build all service images (INSTALL_ML=true for TFT deps)
	$(COMPOSE) build

test: test-java test-python ## Run all three test suites

test-java: ## ingestion-service tests (Maven)
	cd ingestion-service && $(if $(JAVA17_HOME),JAVA_HOME=$(JAVA17_HOME) )$(MVN) -B verify

test-python: ## forecasting + diagnostic tests (pytest; needs requirements-dev.txt installed)
	@for s in $(PY_SERVICES); do echo "== $$s"; (cd $$s && $(PYTHON) -m pytest -q); done

lint: compose-validate ## ruff + mypy for Python services, shellcheck, compose config
	@for s in $(PY_SERVICES); do echo "== $$s"; (cd $$s && $(PYTHON) -m ruff check . && $(PYTHON) -m mypy app); done
	@if command -v shellcheck >/dev/null; then shellcheck scripts/*.sh; else echo "shellcheck not installed; running bash -n only"; for f in scripts/*.sh; do bash -n "$$f"; done; fi

prom-test: ## promtool: check + unit-test Prometheus alert/recording rules
	docker run --rm -v $(CURDIR)/infra:/infra -w /infra --entrypoint promtool prom/prometheus:v2.52.0 check rules prometheus/rules/*.yml
	docker run --rm -v $(CURDIR)/infra:/infra -w /infra --entrypoint promtool prom/prometheus:v2.52.0 test rules prometheus/rules/tests/*.yml

compose-validate: ## Validate docker-compose for all profiles
	docker compose -f $(COMPOSE_FILE) --profile llm --profile neo4j config -q

produce: ## Publish sample events to raw-events (ARGS="..." passed through)
	./scripts/produce-events.sh $(ARGS)

k8s-validate: ## kubeconform (strict) on infra/k8s, raw + rendered kustomization (infra/)
	@if command -v kubeconform >/dev/null; then KC="kubeconform"; \
	else KC="docker run --rm -i -v $(CURDIR)/infra/k8s:/k8s:ro ghcr.io/yannh/kubeconform:$(KUBECONFORM_VERSION)"; fi; \
	if command -v kubeconform >/dev/null; then $$KC -strict -summary -skip Kustomization infra/k8s; \
	else $$KC -strict -summary -skip Kustomization /k8s; fi; \
	kubectl kustomize infra | $$KC -strict -summary -
