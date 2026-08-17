.PHONY: bootstrap check format lint sync test typecheck

bootstrap: sync
	git config core.hooksPath .githooks

sync:
	uv sync --all-groups

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check .

typecheck:
	uv run mypy src tests

test:
	uv run pytest

check: lint typecheck test
