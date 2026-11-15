"""GeoEstate listings pipeline.

Orchestration only: every task runs one `geostate <step> --city <city>` command. The logic
lives in the tested `geostate` package, installed in its own virtualenv (see
docker/airflow.Dockerfile). Every step is idempotent, so retries and re-runs are safe.

    stage -> core -> marts -> clickhouse      (one chain per city)

Error policy:
- infrastructure errors (DB down, network) -> retried with exponential backoff;
- quality-gate failures (exit code 2) -> fail immediately, no retry: retrying bad data
  does not fix it, a human has to look. The map keeps serving the last good version.
"""

from __future__ import annotations

import os
import subprocess

import pendulum
from airflow.exceptions import AirflowFailException
from airflow.sdk import dag, task

GEOSTATE_BIN = os.environ.get("GEOSTATE_BIN", "geostate")
CITIES = ["Madrid"]  # idealista18 also ships Barcelona and Valencia
STEPS = ["stage", "core", "marts", "clickhouse"]
EXIT_QUALITY_GATE = 2


@task(retries=2, retry_delay=pendulum.duration(seconds=30), retry_exponential_backoff=True)
def run_step(step: str, city: str) -> None:
    cmd = [GEOSTATE_BIN, step, "--city", city]
    print("running:", " ".join(cmd), flush=True)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout is not None
    for line in proc.stdout:  # stream the step's logs into the Airflow task log
        print(line, end="", flush=True)
    code = proc.wait()
    if code == EXIT_QUALITY_GATE:
        raise AirflowFailException(f"quality gate failed for {city}/{step}: not retrying")
    if code != 0:
        raise RuntimeError(f"{city}/{step} exited with code {code}")


@dag(
    dag_id="listings_pipeline",
    schedule="@monthly",  # the source is refreshed monthly in production
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,  # runs rewrite the same tables: never overlap
    tags=["geostate", "listings"],
    doc_md=__doc__,
)
def listings_pipeline() -> None:
    for city in CITIES:
        previous = None
        for step in STEPS:
            current = run_step.override(task_id=f"{step}_{city.lower()}")(step=step, city=city)
            if previous is not None:
                previous >> current
            previous = current


listings_pipeline()
