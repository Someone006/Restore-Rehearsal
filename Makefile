.PHONY: setup format lint test integration build check

setup:
	uv sync --extra dev

format:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check .

test:
	uv run pytest -m "not docker"

integration:
	uv run pytest -m docker

build:
	uv run python -m build

check: lint test build
