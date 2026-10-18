#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BUILD_SCRIPT="${SCRIPT_DIR}/build-whisper.sh"
GET_MODELS_SCRIPT="${SCRIPT_DIR}/get-models.sh"
GET_ACFT_SCRIPT="${SCRIPT_DIR}/get-acft-models.sh"
QUANT_MODELS_SCRIPT="${SCRIPT_DIR}/quant-models.sh"
MODELS_DIR="${PROJECT_ROOT}/models/ggml"

FORCE_DOWNLOAD=0
FORCE_ACFT=0
FORCE_QUANT=0
QUANT_ARG=""

log() {
	echo "[INFO] $*"
}

die() {
	echo "[ERROR] $*" >&2
	exit 1
}

on_error() {
	local exit_code="$1"
	local line_no="$2"
	echo "[ERROR] Command failed with exit ${exit_code} at line ${line_no}: ${BASH_COMMAND}" >&2
	exit "$exit_code"
}

trap 'on_error $? $LINENO' ERR

usage() {
	cat <<'EOF'
Usage: ./scripts/do-erythang.sh [--force-download] [--force-acft] [--force-quant] [--quant|-q 5|8|58|85|5,8|8,5]

Runs the full local setup flow:
  1. build-whisper.sh --all
  2. get-models.sh --all-models
  3. get-acft-models.sh --all-models
  4. quant-models.sh over every base model in models/ggml

Options:
	--force-download  Re-download standard Whisper models.
	--force-acft      Force ACFT download and conversion stages.
	--force-quant     Rebuild quantized outputs.
	--quant, -q       Quant levels to build. Defaults to both q5 and q8.
	--help, -h        Show this help text.
EOF
}

parse_args() {
	while [[ $# -gt 0 ]]; do
		case "$1" in
			--force-download)
				FORCE_DOWNLOAD=1
				;;
			--force-acft)
				FORCE_ACFT=1
				;;
			--force-quant)
				FORCE_QUANT=1
				;;
			--quant|-q)
				[[ $# -ge 2 ]] || die "$1 requires a value"
				QUANT_ARG="$2"
				shift
				;;
			--help|-h)
				usage
				exit 0
				;;
			*)
				usage >&2
				die "unknown argument: $1"
				;;
		esac
		shift
	done
}

require_scripts() {
	[[ -f "$BUILD_SCRIPT" ]] || die "missing script: ${BUILD_SCRIPT}"
	[[ -f "$GET_MODELS_SCRIPT" ]] || die "missing script: ${GET_MODELS_SCRIPT}"
	[[ -f "$GET_ACFT_SCRIPT" ]] || die "missing script: ${GET_ACFT_SCRIPT}"
	[[ -f "$QUANT_MODELS_SCRIPT" ]] || die "missing script: ${QUANT_MODELS_SCRIPT}"
}

run_builds() {
	log "Building whisper.cpp for cpu, openblas, and cuda"
	bash "$BUILD_SCRIPT" --all
}

run_standard_model_downloads() {
	local -a cmd

	cmd=("$GET_MODELS_SCRIPT" --all-models)
	if [[ "$FORCE_DOWNLOAD" -eq 1 ]]; then
		cmd+=(--force)
	fi

	log "Downloading standard Whisper models into ${MODELS_DIR}"
	bash "${cmd[@]}"
}

run_acft_model_downloads() {
	local -a cmd

	cmd=("$GET_ACFT_SCRIPT" --all-models)
	if [[ "$FORCE_ACFT" -eq 1 ]]; then
		cmd+=(--force)
	fi

	log "Downloading and converting ACFT models into ${MODELS_DIR}"
	bash "${cmd[@]}"
}

run_quantization() {
	local -a cmd

	cmd=("$QUANT_MODELS_SCRIPT")
	if [[ -n "$QUANT_ARG" ]]; then
		cmd+=(--quant "$QUANT_ARG")
	fi
	if [[ "$FORCE_QUANT" -eq 1 ]]; then
		cmd+=(--force)
	fi

	log "Quantizing all base models found under ${MODELS_DIR}"
	bash "${cmd[@]}"
}

main() {
	parse_args "$@"
	require_scripts
	run_builds
	run_standard_model_downloads
	run_acft_model_downloads
	run_quantization
	log "Done"
}

main "$@"