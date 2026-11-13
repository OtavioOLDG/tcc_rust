#!/usr/bin/env bash

set -euo pipefail

DURATION="${DURATION:-300}"
OUTPUT="${OUTPUT:-docker-metrics.csv}"
CONTAINER="${CONTAINER:-}"

if ! [[ "$DURATION" =~ ^[1-9][0-9]*$ ]]; then
    echo "DURATION deve ser um número inteiro positivo." >&2
    exit 1
fi

if [[ -z "$CONTAINER" ]]; then
    echo "CONTAINER deve ser informado." >&2
    exit 1
fi

container_pid="$(
    sudo docker inspect \
        --format '{{.State.Pid}}' \
        "$CONTAINER"
)"

if ! [[ "$container_pid" =~ ^[1-9][0-9]*$ ]]; then
    echo "Não foi possível obter o PID do container." >&2
    exit 1
fi

cgroup_relative="$(
    awk -F: '$1 == "0" { print $3; exit }' \
        "/proc/$container_pid/cgroup"
)"

if [[ -z "$cgroup_relative" ]]; then
    echo "Não foi possível localizar o cgroup v2 do container." >&2
    exit 1
fi

CGROUP_PATH="/sys/fs/cgroup${cgroup_relative}"

if [[ ! -r "$CGROUP_PATH/cpu.stat" ||
      ! -r "$CGROUP_PATH/memory.current" ]]; then
    echo "Arquivos necessários do cgroup v2 não estão disponíveis." >&2
    exit 1
fi

CPU_COUNT="$(getconf _NPROCESSORS_ONLN)"

if ! [[ "$CPU_COUNT" =~ ^[1-9][0-9]*$ ]]; then
    echo "Não foi possível determinar a quantidade de CPUs." >&2
    exit 1
fi

read_cpu_usage_usec() {
    awk '
        $1 == "usage_usec" {
            print $2
            exit
        }
    ' "$CGROUP_PATH/cpu.stat"
}

read_memory_bytes() {
    cat "$CGROUP_PATH/memory.current"
}

bytes_to_mib() {
    local bytes="$1"

    awk -v bytes="$bytes" '
        BEGIN {
            printf "%.2f", bytes / 1048576
        }
    '
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

echo "timestamp,sample,interval_ms,cpu_container_percent,ram_container_mib,ram_container_observed_peak_mib" > "$OUTPUT"

previous_cpu_usec="$(read_cpu_usage_usec)"

start_time_ns="$(date +%s%N)"
previous_time_ns="$start_time_ns"
deadline_ns=$((start_time_ns + DURATION * 1000000000))
next_target_ns=$((start_time_ns + 1000000000))

peak_memory_bytes=0
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

    if [[ ! -r "$CGROUP_PATH/cpu.stat" ||
          ! -r "$CGROUP_PATH/memory.current" ]]; then
        echo "O container terminou durante a coleta." >&2
        exit 1
    fi

    current_cpu_usec="$(read_cpu_usage_usec)"
    delta_cpu_usec=$((current_cpu_usec - previous_cpu_usec))

    interval_ns=$((current_time_ns - previous_time_ns))
    interval_ms=$((interval_ns / 1000000))

    cpu_percent="$(
        awk \
            -v cpu_usec="$delta_cpu_usec" \
            -v interval_ns="$interval_ns" \
            -v cpus="$CPU_COUNT" '
            BEGIN {
                if (interval_ns <= 0 || cpus <= 0) {
                    printf "0.00"
                } else {
                    interval_usec = interval_ns / 1000

                    printf "%.2f",
                        100 * cpu_usec / (interval_usec * cpus)
                }
            }
        '
    )"

    memory_bytes="$(read_memory_bytes)"

    if (( memory_bytes > peak_memory_bytes )); then
        peak_memory_bytes="$memory_bytes"
    fi

    ram_mib="$(bytes_to_mib "$memory_bytes")"
    ram_peak_mib="$(bytes_to_mib "$peak_memory_bytes")"

    timestamp="$(date +'%Y-%m-%dT%H:%M:%S.%3N%:z')"

    sample=$((sample + 1))

    printf '%s,%d,%d,%s,%s,%s\n' \
        "$timestamp" \
        "$sample" \
        "$interval_ms" \
        "$cpu_percent" \
        "$ram_mib" \
        "$ram_peak_mib" >> "$OUTPUT"

    previous_cpu_usec="$current_cpu_usec"
    previous_time_ns="$current_time_ns"

    next_target_ns=$((next_target_ns + 1000000000))

    now_ns="$(date +%s%N)"
    while (( next_target_ns <= now_ns )); do
        next_target_ns=$((next_target_ns + 1000000000))
    done
done