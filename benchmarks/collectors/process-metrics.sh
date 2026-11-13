#!/usr/bin/env bash

set -euo pipefail

DURATION="${DURATION:-300}"
OUTPUT="${OUTPUT:-process-metrics.csv}"
PID="${PID:-}"

if ! [[ "$DURATION" =~ ^[1-9][0-9]*$ ]]; then
    echo "DURATION deve ser um número inteiro positivo." >&2
    exit 1
fi

if ! [[ "$PID" =~ ^[1-9][0-9]*$ ]]; then
    echo "PID deve ser informado e deve ser um número inteiro positivo." >&2
    exit 1
fi

if [[ ! -r "/proc/$PID/stat" || ! -r "/proc/$PID/status" ]]; then
    echo "Processo $PID não existe ou não pode ser lido." >&2
    exit 1
fi

CLK_TCK="$(getconf CLK_TCK)"
CPU_COUNT="$(getconf _NPROCESSORS_ONLN)"

if ! [[ "$CLK_TCK" =~ ^[1-9][0-9]*$ ]]; then
    echo "Não foi possível determinar CLK_TCK." >&2
    exit 1
fi

if ! [[ "$CPU_COUNT" =~ ^[1-9][0-9]*$ ]]; then
    echo "Não foi possível determinar a quantidade de CPUs." >&2
    exit 1
fi

read_process_cpu() {
    local stat_line
    local remaining

    stat_line="$(cat "/proc/$PID/stat")"
    remaining="${stat_line##*) }"

    set -- $remaining

    printf '%s\n' "$((${12} + ${13}))"
}

read_process_memory_kib() {
    awk '
        /^VmRSS:/ { rss = $2 }
        /^VmHWM:/ { hwm = $2 }
        END {
            if (rss == "") rss = 0
            if (hwm == "") hwm = 0
            printf "%d %d\n", rss, hwm
        }
    ' "/proc/$PID/status"
}

kib_to_mib() {
    local kib="$1"

    awk -v kib="$kib" '
        BEGIN {
            printf "%.2f", kib / 1024
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

echo "timestamp,sample,interval_ms,cpu_workload_percent,ram_workload_mib,ram_workload_observed_peak_mib,ram_workload_hwm_mib" > "$OUTPUT"

previous_process="$(read_process_cpu)"

start_time_ns="$(date +%s%N)"
previous_time_ns="$start_time_ns"
deadline_ns=$((start_time_ns + DURATION * 1000000000))
next_target_ns=$((start_time_ns + 1000000000))

peak_rss_kib=0
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

    if [[ ! -r "/proc/$PID/stat" || ! -r "/proc/$PID/status" ]]; then
        echo "Processo $PID terminou durante a coleta." >&2
        exit 1
    fi

    current_process="$(read_process_cpu)"
    delta_process=$((current_process - previous_process))

    interval_ns=$((current_time_ns - previous_time_ns))
    interval_ms=$((interval_ns / 1000000))

    cpu_percent="$(
        awk \
            -v ticks="$delta_process" \
            -v clk="$CLK_TCK" \
            -v interval_ns="$interval_ns" \
            -v cpus="$CPU_COUNT" '
            BEGIN {
                if (interval_ns <= 0 || clk <= 0 || cpus <= 0) {
                    printf "0.00"
                } else {
                    elapsed_seconds = interval_ns / 1000000000
                    process_seconds = ticks / clk

                    printf "%.2f",
                        100 * process_seconds / (elapsed_seconds * cpus)
                }
            }
        '
    )"

    read -r rss_kib hwm_kib < <(read_process_memory_kib)

    if (( rss_kib > peak_rss_kib )); then
        peak_rss_kib="$rss_kib"
    fi

    ram_mib="$(kib_to_mib "$rss_kib")"
    ram_peak_mib="$(kib_to_mib "$peak_rss_kib")"
    ram_hwm_mib="$(kib_to_mib "$hwm_kib")"

    timestamp="$(date +'%Y-%m-%dT%H:%M:%S.%3N%:z')"

    sample=$((sample + 1))

    printf '%s,%d,%d,%s,%s,%s,%s\n' \
        "$timestamp" \
        "$sample" \
        "$interval_ms" \
        "$cpu_percent" \
        "$ram_mib" \
        "$ram_peak_mib" \
        "$ram_hwm_mib" >> "$OUTPUT"

    previous_process="$current_process"
    previous_time_ns="$current_time_ns"

    next_target_ns=$((next_target_ns + 1000000000))

    now_ns="$(date +%s%N)"
    while (( next_target_ns <= now_ns )); do
        next_target_ns=$((next_target_ns + 1000000000))
    done
done