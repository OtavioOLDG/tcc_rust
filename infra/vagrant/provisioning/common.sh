#!/usr/bin/env bash

set -euo pipefail

echo "=== Common provisioning: início ==="

echo "Hostname:"
hostname

echo "Sistema operacional:"
cat /etc/os-release

echo "Kernel:"
uname -r

echo "CPU disponível:"
nproc

echo "Memória disponível:"
free -h

echo "=== Common provisioning: concluído ==="