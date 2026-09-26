# GPU Native Infra -- convenience targets.
#
# Beginners:  ./install.sh && ./start.sh
# Everyone else: make install && make start

SHELL := /bin/bash
.DEFAULT_GOAL := help

.PHONY: help install setup start stop restart reset clean logs test health update demo \
        poc poc-stop mvp mvp-stop mvp-reset status selftest selftest-poc selftest-mvp \
        backend-test frontend-build lint

help: ## Show this help
	@echo "GPU Native Infra"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Build and start the simulated POC
	@./install.sh

setup: ## Interactive configuration wizard
	@./setup.sh

start: ## Start the POC
	@./start.sh

stop: ## Stop the POC (data preserved)
	@./stop.sh

restart: ## Restart the POC
	@./restart.sh

reset: ## Delete all POC data and reset the simulation
	@./reset.sh

clean: ## Remove containers, volumes and generated configuration
	@./clean.sh

logs: ## Tail backend + frontend logs (make logs SERVICE=postgres)
	@./logs.sh $(SERVICE)

health: ## Health report
	@./health.sh

update: ## git pull, rebuild, restart
	@./update.sh

demo: ## Prerequisites -> install -> start -> health -> seed
	@./demo.sh

status: ## Show whether the POC or the MVP is running
	@./status.sh

# --- the two execution modes -------------------------------------------------

poc: ## Start the fully simulated POC
	@./startpoc.sh

poc-stop: ## Stop the POC
	@./stoppoc.sh

mvp: ## Start the real local MVP (kind + Flux)
	@./startmvp.sh

mvp-stop: ## Stop the MVP application (keeps the kind cluster)
	@./stopmvp.sh

mvp-reset: ## Reset MVP data (keeps the kind cluster)
	@./resetmvp.sh

# --- tests ---------------------------------------------------------------------

test: backend-test ## Run the backend test-suite

selftest: ## End-to-end self test against the running POC
	@./selftest.sh

selftest-poc: ## Alias for selftest
	@./selftest-poc.sh

selftest-mvp: ## End-to-end self test against the running MVP
	@./selftest-mvp.sh

backend-test: ## pytest (uses .venv if present, otherwise the backend container)
	@if [ -x .venv/bin/python ]; then \
		cd backend && ../.venv/bin/python -m pytest; \
	elif command -v python3 >/dev/null 2>&1 && python3 -c 'import fastapi' 2>/dev/null; then \
		cd backend && python3 -m pytest; \
	else \
		docker compose -p gpuinfra-poc run --rm --no-deps \
			-e DATABASE_URL= -e REDIS_URL= -e POLICY_ENGINE=internal \
			backend sh -c 'pip install -q pytest pytest-asyncio && cd /app/backend && python -m pytest'; \
	fi

frontend-build: ## Typecheck and build the UI
	@cd frontend && npm install --no-audit --no-fund && npm run build
