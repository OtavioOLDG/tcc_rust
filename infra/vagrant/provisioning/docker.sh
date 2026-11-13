#!/usr/bin/env bash

set -euo pipefail

DOCKER_VERSION="5:29.7.2-1~ubuntu.22.04~jammy"
CONTAINERD_VERSION="2.3.4-1~ubuntu.22.04~jammy"
BUILDX_VERSION="0.37.0-1~ubuntu.22.04~jammy"

echo "=== Provisionamento DOCKER: início ==="

echo "Instalando dependências necessárias..."

apt-get update

apt-get install -y --no-install-recommends \
  ca-certificates \
  curl

echo "Configurando repositório oficial do Docker..."

install -m 0755 -d /etc/apt/keyrings

curl \
  --proto '=https' \
  --tlsv1.2 \
  --fail \
  --silent \
  --show-error \
  https://download.docker.com/linux/ubuntu/gpg \
  --output /etc/apt/keyrings/docker.asc

chmod 0644 /etc/apt/keyrings/docker.asc

cat > /etc/apt/sources.list.d/docker.sources <<'EOF'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: jammy
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
EOF

apt-get update

echo "Instalando Docker Engine ${DOCKER_VERSION}..."
echo "containerd ${CONTAINERD_VERSION}"
echo "Buildx ${BUILDX_VERSION}"

apt-get install -y --no-install-recommends \
  docker-ce="${DOCKER_VERSION}" \
  docker-ce-cli="${DOCKER_VERSION}" \
  containerd.io="${CONTAINERD_VERSION}" \
  docker-buildx-plugin="${BUILDX_VERSION}"

echo "Versões instaladas:"

docker --version
containerd --version
docker buildx version

echo "Status do serviço Docker:"

systemctl is-active docker

echo "=== Provisionamento DOCKER: concluído ==="