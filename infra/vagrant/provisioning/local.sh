#!/usr/bin/env bash

set -euo pipefail

RUST_VERSION="1.98.0"
VAGRANT_HOME="/home/vagrant"

echo "=== Provisionamento LOCAL: início ==="

echo "Instalando dependências necessárias..."
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates \
  curl \
  build-essential

echo "Instalando rustup para o usuário vagrant..."

if [ ! -x "${VAGRANT_HOME}/.cargo/bin/rustup" ]; then
  RUSTUP_SCRIPT="$(mktemp)"

  curl \
    --proto '=https' \
    --tlsv1.2 \
    --fail \
    --silent \
    --show-error \
    https://sh.rustup.rs \
    --output "${RUSTUP_SCRIPT}"

  chown vagrant:vagrant "${RUSTUP_SCRIPT}"
  chmod 700 "${RUSTUP_SCRIPT}"

  sudo -u vagrant -H \
    sh "${RUSTUP_SCRIPT}" \
    -y \
    --profile minimal \
    --default-toolchain none

  rm -f "${RUSTUP_SCRIPT}"
fi

echo "Instalando Rust ${RUST_VERSION}..."

sudo -u vagrant -H \
  "${VAGRANT_HOME}/.cargo/bin/rustup" toolchain install "${RUST_VERSION}" \
  --profile minimal \
  --component rustfmt \
  --component clippy

sudo -u vagrant -H \
  "${VAGRANT_HOME}/.cargo/bin/rustup" default "${RUST_VERSION}"

echo "Versões instaladas:"

sudo -u vagrant -H \
  "${VAGRANT_HOME}/.cargo/bin/rustc" --version

sudo -u vagrant -H \
  "${VAGRANT_HOME}/.cargo/bin/cargo" --version

echo "=== Provisionamento LOCAL: concluído ==="