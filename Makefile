# AI-Driven Event Processing Architecture — developer entry points.
#   make test | lint | prom-test | tf-validate | k8s-validate
#
# There are no optional compose profiles any more (the former `llm` and
# `neo4j` profiles were removed with diagnostic-service's rescoping to
# anomaly detection — see docs/adr/0008).

SHELL := /usr/bin/env bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

COMPOSE_FILE  := infra/docker-compose.yml
COMPOSE       := docker compose -f $(COMPOSE_FILE)
PY_SERVICES   := forecasting-service diagnostic-service
PYTHON        ?= python3
KUBECONFORM_VERSION ?= v0.8.0
KUBECONFORM_CACHE ?= $(HOME)/.cache/kubeconform
# Matches infra/terraform var.kubernetes_version.
K8S_VERSION   ?= 1.29.0
TERRAFORM_IMAGE ?= hashicorp/terraform:1.9.8
# Provider downloads (~100 MB for AWS) are cached across runs.
TF_PLUGIN_CACHE ?= $(HOME)/.terraform.d/plugin-cache
TERRAFORM     := docker run --rm -v $(CURDIR)/infra/terraform:/tf -v $(TF_PLUGIN_CACHE):/tf-cache \
                   -e TF_PLUGIN_CACHE_DIR=/tf-cache -w /tf $(TERRAFORM_IMAGE)

# Use ./mvnw when the wrapper exists, otherwise a system mvn.
MVN := $(if $(wildcard ingestion-service/mvnw),./mvnw,mvn)
# The service targets Java 17; use a Homebrew JDK 17 when present, else the default JDK.
JAVA17_HOME ?= $(firstword $(wildcard /opt/homebrew/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home /usr/local/opt/openjdk@17/libexec/openjdk.jdk/Contents/Home))

.PHONY: help up down logs ps build demo console train-tft test test-java test-python test-storage lint compose-validate produce k8s-validate prom-test tf-validate train

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

up: ## Build + start the stack and wait for health
	./scripts/setup-local.sh

down: ## Stop the stack and remove volumes
	./scripts/setup-local.sh --down

logs: ## Follow logs (SERVICE=name for one service)
	$(COMPOSE) logs -f $(SERVICE)

ps: ## Show container status
	$(COMPOSE) ps -a

build: ## Build all service images (INSTALL_ML=true for tensorflow/scikit-learn)
	$(COMPOSE) build

test: test-java test-python test-storage ## Run all test suites

test-java: ## ingestion-service tests (Maven)
	cd ingestion-service && $(if $(JAVA17_HOME),JAVA_HOME=$(JAVA17_HOME) )$(MVN) -B verify

test-python: ## forecasting + diagnostic tests (pytest; needs requirements-dev.txt installed)
	@for s in $(PY_SERVICES); do echo "== $$s"; (cd $$s && $(PYTHON) -m pytest -q); done

test-storage: ## event-archiver / S3 archive client tests (pytest; needs storage/requirements-dev.txt)
	cd storage && $(PYTHON) -m pytest -q

lint: compose-validate ## ruff + mypy for Python services, shellcheck, compose config
	@for s in $(PY_SERVICES); do echo "== $$s"; (cd $$s && $(PYTHON) -m ruff check . && $(PYTHON) -m mypy app); done
	@echo "== storage"; cd storage && $(PYTHON) -m ruff check . && $(PYTHON) -m mypy archive_client archiver.py
	@if command -v shellcheck >/dev/null; then shellcheck scripts/*.sh; else echo "shellcheck not installed; running bash -n only"; for f in scripts/*.sh; do bash -n "$$f"; done; fi

prom-test: ## promtool: check + unit-test Prometheus alert/recording rules
	@# The globs must expand inside the container (relative to /infra), hence sh -c.
	docker run --rm -v $(CURDIR)/infra:/infra -w /infra --entrypoint sh prom/prometheus:v2.52.0 -c 'promtool check rules prometheus/rules/*.yml'
	docker run --rm -v $(CURDIR)/infra:/infra -w /infra --entrypoint sh prom/prometheus:v2.52.0 -c 'promtool test rules prometheus/rules/tests/*.yml'
	docker run --rm -v $(CURDIR)/infra:/infra -w /infra --entrypoint promtool prom/prometheus:v2.52.0 check config prometheus.yml

tf-validate: ## terraform fmt -check + validate (no AWS credentials needed)
	@mkdir -p $(TF_PLUGIN_CACHE)
	$(TERRAFORM) fmt -check -recursive
	$(TERRAFORM) init -backend=false -input=false >/dev/null
	$(TERRAFORM) validate

train-tft: ## Train the Temporal Fusion Transformer on local Prometheus history in Docker, then reload forecasting (DAYS=2, EPOCHS=6, BATCHES=60; ~15 min on CPU)
	$(COMPOSE) run --rm --no-deps -v $(CURDIR)/forecasting-service/models:/out forecasting-service \
		python -m training.train_tft --prometheus-url http://prometheus:9090 --days $(or $(DAYS),2) \
		--epochs $(or $(EPOCHS),6) --max-batches $(or $(BATCHES),60) --output /out/tft.ckpt --version $(or $(VERSION),tft-$$(date +%Y%m%d-%H%M))
	$(COMPOSE) restart forecasting-service

train: ## Train the forecasting model from local Prometheus (VERSION=..., DAYS=7)
	cd forecasting-service && $(PYTHON) -m training.train_forecast_model --prometheus-url http://localhost:9090 \
		--days $(or $(DAYS),7) --output models/forecast_model.json --version $(or $(VERSION),forecast-$$(date +%Y.%m.%d))

compose-validate: ## Validate docker-compose
	docker compose -f $(COMPOSE_FILE) config -q

demo: ## Guided live demo, step by step (stack must be up: make up)
	./scripts/demo.sh

console: ## Open the demo console in the browser (http://localhost:9099)
	@open http://localhost:$${CONSOLE_HOST_PORT:-9099} 2>/dev/null || xdg-open http://localhost:$${CONSOLE_HOST_PORT:-9099} 2>/dev/null || echo "Open http://localhost:$${CONSOLE_HOST_PORT:-9099}"

produce: ## Publish sample events to raw-events (ARGS="..." passed through)
	./scripts/produce-events.sh $(ARGS)

k8s-validate: ## kubeconform (strict, k8s $(K8S_VERSION)) on infra/k8s, raw + rendered kustomization
	@# Schemas are cached and fetched serially: parallel fetches from
	@# raw.githubusercontent.com get connection resets on some networks.
	@mkdir -p $(KUBECONFORM_CACHE)
	@if command -v kubeconform >/dev/null; then KC="kubeconform -cache $(KUBECONFORM_CACHE)"; K8S=infra/k8s; \
	else KC="docker run --rm -i -v $(CURDIR)/infra/k8s:/k8s:ro -v $(KUBECONFORM_CACHE):/cache ghcr.io/yannh/kubeconform:$(KUBECONFORM_VERSION) -cache /cache"; K8S=/k8s; fi; \
	FLAGS="-strict -summary -n 1 -kubernetes-version $(K8S_VERSION) -skip Kustomization"; \
	$$KC $$FLAGS $$K8S; \
	kubectl kustomize infra | $$KC $$FLAGS -
