.PHONY: install up down reset pipeline trends dag k8s-up k8s-job k8s-down test lint fmt logs

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

trends:
	uv run geostate trends --city $(CITY)

dag:  ## trigger the Airflow DAG
	docker compose exec airflow airflow dags unpause listings_pipeline
	docker compose exec airflow airflow dags trigger listings_pipeline

k8s-up:  ## kind cluster + image + wiring to the databases (idempotent)
	./scripts/k8s-up.sh

k8s-job:  ## run the whole pipeline once as a Kubernetes Job
	kubectl -n geostate create -f k8s/job-pipeline.yaml

k8s-down:
	kind delete cluster --name geostate

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
