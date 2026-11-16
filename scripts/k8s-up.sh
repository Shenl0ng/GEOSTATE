#!/usr/bin/env bash
# Create (or update) the local kind cluster and wire it to the docker-compose databases.
# Idempotent: safe to run again, e.g. after `docker compose up` recreated a container.
set -euo pipefail

CLUSTER=geostate
NS=geostate
cd "$(dirname "$0")/.."

if ! kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  kind create cluster --name "$CLUSTER" --config k8s/kind-config.yaml
fi
kubectl config use-context "kind-$CLUSTER" >/dev/null

echo "==> image"
docker build -q -f docker/geostate.Dockerfile -t geostate:local . >/dev/null
kind load docker-image geostate:local --name "$CLUSTER"

echo "==> compose with the kind overlay (databases + Airflow join the kind network)"
mkdir -p .kube-airflow
kind get kubeconfig --name "$CLUSTER" --internal > .kube-airflow/config
chmod 644 .kube-airflow/config  # read by the airflow user inside the container
docker compose -f docker-compose.yml -f docker-compose.k8s.yml up -d --wait >/dev/null
ip_of() { docker inspect -f '{{(index .NetworkSettings.Networks "kind").IPAddress}}' "$1"; }
PG_IP=$(ip_of geostate-postgis-1)
CH_IP=$(ip_of geostate-clickhouse-1)

echo "==> manifests"
kubectl apply -f k8s/base.yaml >/dev/null
kubectl -n "$NS" create secret generic geostate-db \
  --from-literal=GEOSTATE_PG_DSN="postgresql://geostate:geostate@postgis:5432/geostate" \
  --from-literal=GEOSTATE_CH_PASSWORD="geostate" \
  --dry-run=client -o yaml | kubectl apply -f - >/dev/null

# Services without selector -> EndpointSlices pointing at the external databases.
kubectl apply -f - >/dev/null <<EOF
apiVersion: discovery.k8s.io/v1
kind: EndpointSlice
metadata:
  name: postgis-external
  namespace: $NS
  labels: { kubernetes.io/service-name: postgis }
addressType: IPv4
ports: [{ name: postgres, port: 5432, protocol: TCP }]
endpoints: [{ addresses: ["$PG_IP"] }]
---
apiVersion: discovery.k8s.io/v1
kind: EndpointSlice
metadata:
  name: clickhouse-external
  namespace: $NS
  labels: { kubernetes.io/service-name: clickhouse }
addressType: IPv4
ports: [{ name: http, port: 8123, protocol: TCP }]
endpoints: [{ addresses: ["$CH_IP"] }]
EOF

echo "ready: postgis -> $PG_IP, clickhouse -> $CH_IP"
