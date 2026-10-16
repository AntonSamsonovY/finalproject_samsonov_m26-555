.PHONY: install run lint test build

install:
	uv sync

run:
	uv run valutatrade-hub

lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest

build:
	uv build
