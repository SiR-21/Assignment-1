#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
bash compile.sh
java -cp build exp01.CorrectnessTest
