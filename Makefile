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
