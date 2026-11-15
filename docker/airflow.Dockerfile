# Airflow + the geostate package installed in its OWN virtualenv.
# Airflow and the pipeline never share dependencies (pandas, pydantic, psycopg...), so upgrading
# one cannot break the other. Tasks call the `geostate` CLI from that venv: the same contract a
# KubernetesPodOperator will use later with a standalone geostate image.
FROM apache/airflow:3.3.2-python3.12

USER root
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/opt/geostate-venv \
    UV_PYTHON=/usr/local/bin/python \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /opt/geostate-src
# dependencies first (cached layer), project code last
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable \
    && mkdir -p /opt/geostate/data \
    && chown -R airflow: /opt/geostate /opt/geostate-venv

USER airflow
WORKDIR /opt/airflow
ENV GEOSTATE_BIN=/opt/geostate-venv/bin/geostate
