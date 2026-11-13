#!/usr/bin/env bash

set -euo pipefail

RUST_VERSION="1.98.0"
WASM_TARGET="wasm32-wasip2"

SPIN_VERSION="4.1.0"
SPIN_SHA256="aeeb892f94cf861f5302546faa0bdaa82cc4be30539621299746a6dc011f3b5a"
SPIN_ARCHIVE="spin-v${SPIN_VERSION}-linux-amd64.tar.gz"
SPIN_URL="https://github.com/spinframework/spin/releases/download/v${SPIN_VERSION}/${SPIN_ARCHIVE}"

VAGRANT_HOME="/home/vagrant"

echo "=== Provisionamento WASM: início ==="

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
    --proto-redir '=https' \
    --tlsv1.2 \
    --fail \
    --silent \
    --show-error \
    --location \
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

echo "Instalando target WASI ${WASM_TARGET}..."

sudo -u vagrant -H \
  "${VAGRANT_HOME}/.cargo/bin/rustup" target add \
  "${WASM_TARGET}" \
  --toolchain "${RUST_VERSION}"

echo "Verificando instalação do Spin ${SPIN_VERSION}..."

if [ -x /usr/local/bin/spin ] && \
   /usr/local/bin/spin --version | grep -q "^spin ${SPIN_VERSION}"; then

  echo "Spin ${SPIN_VERSION} já está instalado."

else

  SPIN_TMP_DIR="$(mktemp -d)"

  cleanup() {
    rm -rf "${SPIN_TMP_DIR}"
  }

  trap cleanup EXIT

  echo "Baixando Spin de release oficial..."

  curl \
    --proto '=https' \
    --proto-redir '=https' \
    --tlsv1.2 \
    --fail \
    --silent \
    --show-error \
    --location \
    "${SPIN_URL}" \
    --output "${SPIN_TMP_DIR}/${SPIN_ARCHIVE}"

  echo "Verificando SHA-256..."

  echo "${SPIN_SHA256}  ${SPIN_TMP_DIR}/${SPIN_ARCHIVE}" \
    | sha256sum --check --strict

  echo "SHA-256 válido."

  echo "Extraindo release verificada..."

  tar \
    --extract \
    --gzip \
    --file "${SPIN_TMP_DIR}/${SPIN_ARCHIVE}" \
    --directory "${SPIN_TMP_DIR}"

  if [ ! -f "${SPIN_TMP_DIR}/spin" ]; then
    echo "ERRO: binário Spin não encontrado no arquivo verificado."
    exit 1
  fi

  echo "Instalando binário Spin..."

  install \
    --owner=root \
    --group=root \
    --mode=0755 \
    "${SPIN_TMP_DIR}/spin" \
    /usr/local/bin/spin
fi

echo "Versões instaladas:"

sudo -u vagrant -H \
  "${VAGRANT_HOME}/.cargo/bin/rustc" --version

sudo -u vagrant -H \
  "${VAGRANT_HOME}/.cargo/bin/cargo" --version

echo "Targets instalados:"

sudo -u vagrant -H \
  "${VAGRANT_HOME}/.cargo/bin/rustup" target list --installed \
  --toolchain "${RUST_VERSION}"

echo "Versão do Spin:"

sudo -u vagrant -H \
  /usr/local/bin/spin --version

echo "=== Provisionamento WASM: concluído ==="