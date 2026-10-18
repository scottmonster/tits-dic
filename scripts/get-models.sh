#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WHISPER_DIR="${PROJECT_ROOT}/whisper.cpp"
MODELS_DIR="${PROJECT_ROOT}/models/ggml"
ACFT_SCRIPT="${SCRIPT_DIR}/get-acft-models.sh"
MODELS=(tiny tiny.en base base.en small small.en)

FORCE_DOWNLOAD=0
DOWNLOAD_ACFT=0
ALL_MODELS=0
selected_models=()

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
Usage: ./scripts/get-models.sh [--model NAME] [--all-models] [--force|-f] [--with-acft]

Downloads ggml Whisper models into models/ggml.

Options:
	--model NAME   Download one model. Repeatable. Defaults to all supported models.
	--all-models   Download all supported models.
	--force, -f    Re-download and replace existing model files.
	--with-acft    Also run ./scripts/get-acft-models.sh after Whisper downloads.
	--help, -h     Show this help text.
EOF
}

normalized_model_name() {
	local model_name="$1"

	echo "${model_name//_/.}"
}

base_model_filename() {
	local model_name="$1"

	echo "$(normalized_model_name "$model_name").bin"
}

canonical_model_name() {
	local raw_name="$1"

	case "$raw_name" in
		tiny|tiny.en|base|base.en|small|small.en)
			echo "$raw_name"
			;;
		tiny_en)
			echo "tiny.en"
			;;
		base_en)
			echo "base.en"
			;;
		small_en)
			echo "small.en"
			;;
		*)
			die "unsupported model: ${raw_name}"
			;;
	esac
}

add_selected_model() {
	local model_name="$1"
	local existing_name

	for existing_name in "${selected_models[@]:-}"; do
		if [[ "$existing_name" == "$model_name" ]]; then
			return
		fi
	done

	selected_models+=("$model_name")
}

finalize_selected_models() {
	local model_name

	if [[ "$ALL_MODELS" -eq 1 ]]; then
		for model_name in "${MODELS[@]}"; do
			add_selected_model "$model_name"
		done
	fi

	if [[ "${#selected_models[@]}" -eq 0 ]]; then
		for model_name in "${MODELS[@]}"; do
			add_selected_model "$model_name"
		done
	fi
}

parse_args() {
	while [[ $# -gt 0 ]]; do
		case "$1" in
			--model)
				[[ $# -ge 2 ]] || die "$1 requires a value"
				add_selected_model "$(canonical_model_name "$2")"
				shift
				;;
			--all-models)
				ALL_MODELS=1
				;;
			--force|-f)
				FORCE_DOWNLOAD=1
				;;
			--with-acft)
				DOWNLOAD_ACFT=1
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

	finalize_selected_models
}

require_whisper_repo() {
	[[ -d "${WHISPER_DIR}/.git" ]] || die "whisper.cpp repo not found: ${WHISPER_DIR}"
	[[ -x "${WHISPER_DIR}/models/download-ggml-model.sh" ]] || die "missing whisper downloader: ${WHISPER_DIR}/models/download-ggml-model.sh"
}

download_whisper_models() {
	local model_name
	local source_file
	local target_file

	mkdir -p "$MODELS_DIR"

	pushd "$WHISPER_DIR" >/dev/null
	for model_name in "${selected_models[@]}"; do
		source_file="models/ggml-${model_name}.bin"
		target_file="${MODELS_DIR}/$(base_model_filename "$model_name")"

		if [[ -f "$target_file" && "$FORCE_DOWNLOAD" -eq 0 ]]; then
			log "Skipping existing model $(basename "$target_file")"
			continue
		fi

		log "Downloading model ${model_name}"
		./models/download-ggml-model.sh "$model_name"
		[[ -f "$source_file" ]] || die "missing downloaded model: ${source_file}"
		mv -f "$source_file" "$target_file"
	done
	popd >/dev/null
}

download_acft_models() {
	if [[ "$DOWNLOAD_ACFT" -ne 1 ]]; then
		return 0
	fi
	[[ -x "$ACFT_SCRIPT" ]] || die "ACFT script not found or not executable: ${ACFT_SCRIPT}"

	log "Running ${ACFT_SCRIPT}"
	"$ACFT_SCRIPT"
}

main() {
	parse_args "$@"
	require_whisper_repo
	download_whisper_models
	download_acft_models
	log "Done"
}

main "$@"









