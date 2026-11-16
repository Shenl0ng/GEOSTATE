"""GeoEstate listings pipeline on Kubernetes.

Same steps and contract as `listings_pipeline`, but every task runs in its OWN pod
(KubernetesPodOperator) from the standalone `geostate` image:

    stage -> core -> marts -> clickhouse      (one pod per step)

Why pods instead of running inside the Airflow worker:
- isolation: the pipeline's dependencies never touch Airflow's;
- resources per step: `stage` parses an R file with pandas (~540 MiB peak, measured),
  the other steps push work to SQL (< 200 MiB). Each pod requests what it needs;
- scaling: the cluster schedules pods, Airflow only orchestrates.

Error policy is unchanged: infrastructure failures are retried with backoff, a quality-gate
failure (exit code 2) fails immediately without retry.
"""

from __future__ import annotations

import os
from typing import Any

import pendulum
from airflow.exceptions import AirflowException, AirflowFailException
from airflow.providers.cncf.kubernetes.operators.pod import KubernetesPodOperator
from airflow.sdk import dag
from kubernetes.client import models as k8s

CITIES = ["Madrid"]
STEPS = ["stage", "core", "marts", "clickhouse"]
EXIT_QUALITY_GATE = 2
NAMESPACE = "geostate"
IMAGE = os.environ.get("GEOSTATE_IMAGE", "geostate:local")
KUBECONFIG = os.environ.get("GEOSTATE_KUBECONFIG")  # None -> in-cluster config


def resources(memory_request: str, memory_limit: str, cpu: str) -> k8s.V1ResourceRequirements:
    return k8s.V1ResourceRequirements(
        requests={"cpu": cpu, "memory": memory_request},
        limits={"memory": memory_limit},  # no CPU limit: avoids throttling, memory is the risk
    )


# Sized from the measured peak memory of each step (cgroup memory.peak).
STEP_RESOURCES = {
    "stage": resources("640Mi", "768Mi", "250m"),
    "core": resources("192Mi", "320Mi", "100m"),
    "marts": resources("192Mi", "320Mi", "100m"),
    "clickhouse": resources("192Mi", "320Mi", "100m"),
}


def exit_code(pod: k8s.V1Pod | None, container: str) -> int | None:
    statuses = (pod.status.container_statuses if pod and pod.status else None) or []
    for status in statuses:
        if status.name == container and status.state and status.state.terminated:
            return int(status.state.terminated.exit_code)
    return None


class GeostatePodOperator(KubernetesPodOperator):
    """Pod operator that turns the CLI's quality-gate exit code into a no-retry failure."""

    def cleanup(self, pod: Any, remote_pod: Any, *args: Any, **kwargs: Any) -> None:
        try:
            super().cleanup(pod, remote_pod, *args, **kwargs)
        except AirflowException as err:
            if exit_code(remote_pod, self.base_container_name) == EXIT_QUALITY_GATE:
                raise AirflowFailException(f"quality gate failed in {self.task_id}") from err
            raise


@dag(
    dag_id="listings_pipeline_k8s",
    schedule="@monthly",
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    is_paused_upon_creation=True,  # both DAGs write the same tables: enable one of them
    tags=["geostate", "listings", "kubernetes"],
    doc_md=__doc__,
    default_args={
        "retries": 2,
        "retry_delay": pendulum.duration(seconds=30),
        "retry_exponential_backoff": True,
    },
)
def listings_pipeline_k8s() -> None:
    env_from = [
        k8s.V1EnvFromSource(config_map_ref=k8s.V1ConfigMapEnvSource(name="geostate-config")),
        k8s.V1EnvFromSource(secret_ref=k8s.V1SecretEnvSource(name="geostate-db")),
    ]
    for city in CITIES:
        previous = None
        for step in STEPS:
            current = GeostatePodOperator(
                task_id=f"{step}_{city.lower()}",
                name=f"geostate-{step}-{city.lower()}",
                namespace=NAMESPACE,
                image=IMAGE,
                image_pull_policy="IfNotPresent",
                arguments=[step, "--city", city],  # the image ENTRYPOINT is `geostate`
                env_from=env_from,
                container_resources=STEP_RESOURCES[step],
                security_context=k8s.V1PodSecurityContext(run_as_non_root=True, run_as_user=10001),
                labels={"app": "geostate", "step": step, "city": city.lower()},
                config_file=KUBECONFIG,
                in_cluster=KUBECONFIG is None,
                get_logs=True,  # pod logs stream into the Airflow task log
                on_finish_action="delete_pod",
                startup_timeout_seconds=300,
            )
            if previous is not None:
                previous >> current
            previous = current


listings_pipeline_k8s()
