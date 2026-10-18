#!/usr/bin/env bash

set -Eeuo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
log_file="${script_dir}/full-bench.log"

exec > >(tee -a "$log_file") 2>&1

backends=(
  "cpu"
  "openblas"
  "cuda"
)

die() {
  echo "[ERROR] $*" >&2
  exit 1
}

shopt -s nullglob
models=("${script_dir}"/models/ggml/*.bin)
shopt -u nullglob

[[ ${#models[@]} -gt 0 ]] || die "no model files found under ${script_dir}/models/ggml"

failures=0

printf '=== full bench started: %s ===\n' "$(date '+%Y-%m-%d %H:%M:%S')"

for backend in "${backends[@]}"; do
  cmd="${script_dir}/whisper.cpp/build-${backend}/bin/whisper-bench"

  if [[ ! -x "$cmd" ]]; then
    echo "[WARN] skipping ${backend}: missing benchmark binary at ${cmd}"
    continue
  fi

  printf '\n=== backend: %s ===\n' "$backend"

  for model in "${models[@]}"; do
    bench_args=("$cmd" -m "$model")

    if [[ "$backend" != "cuda" ]]; then
      bench_args=("$cmd" --no-gpu -m "$model")
    fi

    # printf '\n--- model: %s ---\n' "$(basename "$model")"
    printf '\n---- backend: %s -- model: %s ----\n' "$backend" "$(basename "$model")"
    if ! "${bench_args[@]}"; then
      echo "[WARN] benchmark failed for backend=${backend} model=$(basename "$model")"
      failures=1
    fi
  done
done

printf '\n=== full bench finished: %s ===\n' "$(date '+%Y-%m-%d %H:%M:%S')"

exit "$failures"