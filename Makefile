.PHONY: install up down reset pipeline test lint fmt logs

CITY ?= Madrid

install:
	uv sync

up:
	docker compose up -d --wait

down:
	docker compose down

reset:  ## drop the database volume
	docker compose down -v

pipeline:
	uv run geostate run --city $(CITY)

test:
	uv run pytest -q

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src

fmt:
	uv run ruff format .
	uv run ruff check --fix .

logs:
	docker compose logs -f
