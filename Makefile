UV := uv
COMPOSE := docker compose --env-file .env -f infrastructure/docker/compose.yaml
.PHONY: setup test lint typecheck format check dev infra-up infra-down compose-validate hooks
setup:
	$(UV) sync --locked
hooks:
	$(UV) run --locked pre-commit install
test:
	$(UV) run --locked pytest
lint:
	$(UV) run --locked ruff check .
typecheck:
	$(UV) run --locked mypy
format:
	$(UV) run --locked ruff format .
check: lint typecheck test
	$(UV) run --locked ruff format --check .
dev:
	$(UV) run --locked uvicorn atlas_api.main:create_app --factory --reload --reload-dir apps --reload-dir packages --host 127.0.0.1
infra-up:
	$(COMPOSE) up -d --wait --wait-timeout 180
infra-down:
	$(COMPOSE) down
compose-validate:
	$(COMPOSE) config --quiet

.PHONY: sim-bootstrap sim-migrate sim-generate sim-reset sim-integration
sim-bootstrap:
	$(UV) run --locked python scripts/bootstrap_simulator.py
sim-migrate:
	$(UV) run --locked alembic upgrade head
	$(UV) run --locked python scripts/bootstrap_simulator.py --grant-reader
sim-generate:
	$(UV) run --locked atlas-sim generate --profile small --seed 42
sim-reset:
	$(UV) run --locked atlas-sim reset
sim-integration:
	$(UV) run --locked python scripts/test_simulator_integration.py

.PHONY: mcp-demo mcp-integration mcp-schemas
mcp-demo:
	$(UV) run --locked atlas-mcp-client
mcp-integration:
	$(UV) run --locked python scripts/test_simulator_integration.py
mcp-schemas:
	$(UV) run --locked python scripts/export_mcp_schemas.py

.PHONY: test-all
test-all:
	$(UV) run --locked python scripts/test_simulator_integration.py --all
