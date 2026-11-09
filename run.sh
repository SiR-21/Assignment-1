#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Usage: bash run.sh [forks=10] [blocks=8] [seed=20261109] [output-directory=data]
forks="${1:-10}"
blocks="${2:-8}"
seed="${3:-20261109}"
result_dir="${4:-data}"
if ! [[ "$forks" =~ ^[1-9][0-9]*$ && "$blocks" =~ ^[1-9][0-9]*$ ]]; then
  echo "forks and blocks must be positive integers" >&2
  exit 2
fi
if [[ -d "$result_dir" ]] && compgen -G "$result_dir/raw-fork-*.csv" > /dev/null; then
  echo "Output directory already contains raw measurements; choose a fresh directory" >&2
  exit 2
fi
mkdir -p "$result_dir"
bash compile.sh
java -cp build exp01.CorrectnessTest | tee "$result_dir/correctness.txt"
for ((fork=1; fork<=forks; fork++)); do
  java -Xms512m -Xmx512m -XX:+UseSerialGC -cp build exp01.Benchmark \
    "$result_dir" "$fork" "$blocks" "$seed"
done
