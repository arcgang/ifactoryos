.PHONY: help install test install-planning-cockpit test-planning-cockpit dev-planning-cockpit install-line-command test-line-command dev-line-command install-quality-studio test-quality-studio dev-quality-studio install-sequence-optimizer test-sequence-optimizer dev-sequence-optimizer install-defect-genome test-defect-genome dev-defect-genome install-line-command-api test-line-command-api dev-line-command-api install-vin-digital-thread-api test-vin-digital-thread-api dev-vin-digital-thread-api install-hyperledger-fabric-api test-hyperledger-fabric-api dev-hyperledger-fabric-api up down logs ps
# Generated from the repository plan. `make help` lists the targets.
.DEFAULT_GOAL := help

install-planning-cockpit: ## install planning-cockpit
	cd apps/planning-cockpit && npm ci

test-planning-cockpit: ## test planning-cockpit
	cd apps/planning-cockpit && npm test

dev-planning-cockpit: ## run planning-cockpit locally
	cd apps/planning-cockpit && npm run dev

install-line-command: ## install line-command
	cd apps/line-command && npm ci

test-line-command: ## test line-command
	cd apps/line-command && npm test

dev-line-command: ## run line-command locally
	cd apps/line-command && npm run dev

install-quality-studio: ## install quality-studio
	cd apps/quality-studio && npm ci

test-quality-studio: ## test quality-studio
	cd apps/quality-studio && npm test

dev-quality-studio: ## run quality-studio locally
	cd apps/quality-studio && npm run dev

install-sequence-optimizer: ## install sequence-optimizer
	cd services/sequence-optimizer && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

test-sequence-optimizer: ## test sequence-optimizer
	cd services/sequence-optimizer && .venv/bin/pytest

dev-sequence-optimizer: ## run sequence-optimizer locally
	cd services/sequence-optimizer && if [ -f src/app/main.py ]; then .venv/bin/pip install uvicorn >/dev/null && .venv/bin/uvicorn app.main:app --reload --app-dir src; else echo "no server yet (src/app/main.py) — a task adds it"; fi

install-defect-genome: ## install defect-genome
	cd services/defect-genome && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

test-defect-genome: ## test defect-genome
	cd services/defect-genome && .venv/bin/pytest

dev-defect-genome: ## run defect-genome locally
	cd services/defect-genome && if [ -f src/app/main.py ]; then .venv/bin/pip install uvicorn >/dev/null && .venv/bin/uvicorn app.main:app --reload --app-dir src; else echo "no server yet (src/app/main.py) — a task adds it"; fi

install-line-command-api: ## install line-command-api
	cd services/line-command-api && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

test-line-command-api: ## test line-command-api
	cd services/line-command-api && .venv/bin/pytest

dev-line-command-api: ## run line-command-api locally
	cd services/line-command-api && if [ -f src/app/main.py ]; then .venv/bin/pip install uvicorn >/dev/null && .venv/bin/uvicorn app.main:app --reload --app-dir src; else echo "no server yet (src/app/main.py) — a task adds it"; fi

install-vin-digital-thread-api: ## install vin-digital-thread-api
	cd services/vin-digital-thread-api && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

test-vin-digital-thread-api: ## test vin-digital-thread-api
	cd services/vin-digital-thread-api && .venv/bin/pytest

dev-vin-digital-thread-api: ## run vin-digital-thread-api locally
	cd services/vin-digital-thread-api && if [ -f src/app/main.py ]; then .venv/bin/pip install uvicorn >/dev/null && .venv/bin/uvicorn app.main:app --reload --app-dir src; else echo "no server yet (src/app/main.py) — a task adds it"; fi

install-hyperledger-fabric-api: ## install hyperledger-fabric-api
	cd services/hyperledger-fabric-api && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

test-hyperledger-fabric-api: ## test hyperledger-fabric-api
	cd services/hyperledger-fabric-api && .venv/bin/pytest

dev-hyperledger-fabric-api: ## run hyperledger-fabric-api locally
	cd services/hyperledger-fabric-api && if [ -f src/app/main.py ]; then .venv/bin/pip install uvicorn >/dev/null && .venv/bin/uvicorn app.main:app --reload --app-dir src; else echo "no server yet (src/app/main.py) — a task adds it"; fi

install: install-planning-cockpit install-line-command install-quality-studio install-sequence-optimizer install-defect-genome install-line-command-api install-vin-digital-thread-api install-hyperledger-fabric-api ## install every component

test: test-planning-cockpit test-line-command test-quality-studio test-sequence-optimizer test-defect-genome test-line-command-api test-vin-digital-thread-api test-hyperledger-fabric-api ## test every component

up: ## start the local platform (containers)
	[ -f .env ] || cp .env.example .env
	docker compose up -d

down: ## stop the local platform
	docker compose down

logs: ## follow the platform logs
	docker compose logs -f

ps: ## what is running
	docker compose ps

help:
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-32s %s\n", $$1, $$2}'
