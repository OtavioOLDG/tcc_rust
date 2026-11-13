#!/usr/bin/env bash

set -euo pipefail

DURATION="${DURATION:-300}"
OUTPUT="${OUTPUT:-vm-metrics.csv}"

if ! [[ "$DURATION" =~ ^[1-9][0-9]*$ ]]; then
    echo "DURATION deve ser um número inteiro positivo." >&2
    exit 1
fi

read_cpu() {
    read -r _ user nice system idle iowait irq softirq steal _ < /proc/stat

    local total=$((user + nice + system + idle + iowait + irq + softirq + steal))
    local idle_total=$((idle + iowait))

    printf '%s %s\n' "$total" "$idle_total"
}

read_ram_mib() {
    awk '
        /^MemTotal:/     { total = $2 }
        /^MemAvailable:/ { available = $2 }
        END {
            printf "%.2f", (total - available) / 1024
        }
    ' /proc/meminfo
}

sleep_until_ns() {
    local target_ns="$1"
    local now_ns
    local remaining_ns

    while true; do
        now_ns="$(date +%s%N)"
        remaining_ns=$((target_ns - now_ns))

        if (( remaining_ns <= 0 )); then
            break
        fi

        sleep "$(
            awk -v ns="$remaining_ns" '
                BEGIN {
                    printf "%.6f", ns / 1000000000
                }
            '
        )"
    done
}

echo "timestamp,sample,interval_ms,cpu_vm_percent,ram_vm_mib" > "$OUTPUT"

read -r previous_total previous_idle < <(read_cpu)

start_time_ns="$(date +%s%N)"
previous_time_ns="$start_time_ns"
deadline_ns=$((start_time_ns + DURATION * 1000000000))
next_target_ns=$((start_time_ns + 1000000000))

sample=0

while true; do
    now_ns="$(date +%s%N)"

    if (( now_ns >= deadline_ns )); then
        break
    fi

    sleep_until_ns "$next_target_ns"

    current_time_ns="$(date +%s%N)"

    if (( current_time_ns > deadline_ns )); then
        break
    fi

    read -r current_total current_idle < <(read_cpu)

    delta_total=$((current_total - previous_total))
    delta_idle=$((current_idle - previous_idle))
    interval_ns=$((current_time_ns - previous_time_ns))
    interval_ms=$((interval_ns / 1000000))

    cpu_percent="$(
        awk -v total="$delta_total" -v idle="$delta_idle" '
            BEGIN {
                if (total <= 0) {
                    printf "0.00"
                } else {
                    printf "%.2f", 100 * (total - idle) / total
                }
            }
        '
    )"

    ram_mib="$(read_ram_mib)"
    timestamp="$(date +'%Y-%m-%dT%H:%M:%S.%3N%:z')"

    sample=$((sample + 1))

    printf '%s,%d,%d,%s,%s\n' \
        "$timestamp" \
        "$sample" \
        "$interval_ms" \
        "$cpu_percent" \
        "$ram_mib" >> "$OUTPUT"

    previous_total="$current_total"
    previous_idle="$current_idle"
    previous_time_ns="$current_time_ns"

    next_target_ns=$((next_target_ns + 1000000000))

    # Se a VM atrasar mais de um período inteiro, não tenta
    # gerar várias amostras atrasadas de uma vez.
    now_ns="$(date +%s%N)"
    while (( next_target_ns <= now_ns )); do
        next_target_ns=$((next_target_ns + 1000000000))
    done
done