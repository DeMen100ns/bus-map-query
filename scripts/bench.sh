#!/usr/bin/env bash
# Build Release, measure one algorithm, and check every measured result batch.
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: bench.sh <dijkstra|dijkstra_baseline|astar|hpa|bihpa> [options]

Options:
  --mode optimal|any    Correctness criterion (default: HPA/BiHPA any, others optimal)
  --repetitions N       Measured batches (default: 5)
  --warmup N            Warm-up batches (default: 1)
  --threads N          Search workers; 1 keeps sequential calls (default: 1)
  --queue-capacity N   Maximum queued jobs (default: 64)
  --hpa-cluster-size M  HPA cluster side in meters
  --hpa-weight W        HPA heuristic weight > 1 (default: 1.05)
  --output-dir PATH     New directory for reports (default: automatic session)
  -h, --help            Show this help

Examples, from the BusMap root:
  ./scripts/bench.sh dijkstra
  ./scripts/bench.sh astar --threads 4
  ./scripts/bench.sh astar --mode any --threads 4
  ./scripts/bench.sh hpa --repetitions 3 --warmup 1
  ./scripts/bench.sh bihpa --threads 4

Build errors or failed correctness checks produce a nonzero exit code.
optimal/any changes the checker criterion, not the search algorithm.
EOF
}

fail() {
    printf 'Error: %s\n' "$1" >&2
    exit 2
}

if [[ $# -eq 0 ]]; then
    usage >&2
    exit 2
fi
case "$1" in
    -h|--help) usage; exit 0 ;;
    dijkstra|dijkstra_baseline|astar|hpa|bihpa) algorithm="$1"; shift ;;
    *) fail "Choose one algorithm: dijkstra, dijkstra_baseline, astar, hpa or bihpa" ;;
esac

mode=optimal
[[ "$algorithm" != hpa && "$algorithm" != bihpa ]] || mode=any
repetitions=5
warmup=1
threads=1
queue_capacity=64
output_dir=""
hpa_args=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --mode|--repetitions|--warmup|--threads|--queue-capacity|--output-dir|--hpa-cluster-size|--hpa-weight)
            [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || fail "Missing value for $1"
            case "$1" in
                --mode) mode="$2" ;;
                --repetitions) repetitions="$2" ;;
                --warmup) warmup="$2" ;;
                --threads) threads="$2" ;;
                --queue-capacity) queue_capacity="$2" ;;
                --output-dir) output_dir="$2" ;;
                --hpa-cluster-size|--hpa-weight) hpa_args+=("$1" "$2") ;;
            esac
            shift 2
            ;;
        *) fail "Unknown option: $1" ;;
    esac
done
[[ "$mode" == optimal || "$mode" == any ]] || fail "Mode must be optimal or any"
[[ "$repetitions" =~ ^[1-9][0-9]*$ ]] || fail "Repetitions must be a positive integer"
[[ "$warmup" =~ ^[0-9]+$ ]] || fail "Warmup must be a nonnegative integer"

[[ "$threads" =~ ^[1-9][0-9]*$ ]] || fail "Threads must be a positive integer"
[[ "$queue_capacity" =~ ^[1-9][0-9]*$ ]] || fail "Queue capacity must be a positive integer"

busmap_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -x "$busmap_root/.venv/bin/python" ]]; then
    busmap_python="$busmap_root/.venv/bin/python"
else
    busmap_python="$(command -v python3)" || fail "python3 was not found"
fi
command -v cmake >/dev/null 2>&1 || fail "cmake was not found"

cmake -S "$busmap_root" -B "$busmap_root/build" \
    -DCMAKE_BUILD_TYPE=Release "-DPython3_EXECUTABLE=$busmap_python"
cmake --build "$busmap_root/build" --target busmap-bench --config Release --parallel 4

busmap_binary="$busmap_root/build/busmap-bench"
if [[ ! -x "$busmap_binary" && -x "$busmap_root/build/Release/busmap-bench" ]]; then
    busmap_binary="$busmap_root/build/Release/busmap-bench"
fi
set -- "$busmap_python" "$busmap_root/scripts/benchmark.py" \
    --binary "$busmap_binary" --algorithm "$algorithm" --mode "$mode" \
    --repetitions "$repetitions" --warmup "$warmup" \
    --threads "$threads" --queue-capacity "$queue_capacity"
if [[ -n "$output_dir" ]]; then
    set -- "$@" --output-dir "$output_dir"
fi
if [[ ${#hpa_args[@]} -gt 0 ]]; then
    set -- "$@" "${hpa_args[@]}"
fi
exec "$@"
