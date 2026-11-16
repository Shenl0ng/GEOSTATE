# Standalone image for the pipeline steps. Each Kubernetes pod runs ONE step:
#   geostate <stage|core|marts|clickhouse> --city Madrid
# Multi-stage: build tools stay in the builder, the runtime only gets the virtualenv.

# ---- builder ----
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.5 /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project      # dependencies: cached layer
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable             # the project itself

# ---- runtime ----
FROM python:3.12-slim
RUN useradd --create-home --uid 10001 geostate
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    GEOSTATE_DATA_DIR=/data
RUN mkdir -p /data && chown geostate: /data
USER 10001
WORKDIR /home/geostate
ENTRYPOINT ["geostate"]
CMD ["--help"]
