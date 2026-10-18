#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WHISPER_DIR="${PROJECT_ROOT}/whisper.cpp"
MODELS_DIR="${PROJECT_ROOT}/models/ggml"
WANT_Q5=1
WANT_Q8=1
FORCE_QUANT=0
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
Usage: ./scripts/quant-models.sh [--model NAME] [--quant|-q 5|8|58|85|5,8|8,5] [--force|-f]

Quantizes base .bin models under models/ggml.

Options:
	--model NAME   Quantize one base model. Repeatable. Defaults to all detected base models.
	--quant, -q    Quant levels to build. Defaults to both q5 and q8.
	--force, -f    Rebuild quantized outputs even if they already exist.
	--help, -h     Show this help text.
EOF
}

parse_quant_arg() {
	local raw_value="$1"
	local compact_value

	[[ -n "$raw_value" ]] || die "--quant requires a value"

	if [[ "$raw_value" =~ [^58,\|[:space:]] ]]; then
		die "invalid --quant value: ${raw_value}"
	fi

	compact_value="${raw_value//[^58]/}"
	[[ -n "$compact_value" ]] || die "invalid --quant value: ${raw_value}"

	WANT_Q5=0
	WANT_Q8=0

	case "$compact_value" in
		*5*) WANT_Q5=1 ;;
	esac

	case "$compact_value" in
		*8*) WANT_Q8=1 ;;
	esac
}

want_quant_level() {
	local quant_level="$1"

	case "$quant_level" in
		q5)
			[[ "$WANT_Q5" -eq 1 ]]
			;;
		q8)
			[[ "$WANT_Q8" -eq 1 ]]
			;;
		*)
			die "unsupported quant level: ${quant_level}"
			;;
	esac
}

canonical_model_name() {
	local raw_name="$1"
	local model_name="$raw_name"

	model_name="${model_name%.bin}"

	case "$model_name" in
		*.[Qq][58]|*.[Qq][58]_0|*.q5|*.q8|*.q5_0|*.q8_0)
			die "quantized model should not be passed as a base model: ${raw_name}"
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
		acft.tiny_en)
			echo "acft.tiny.en"
			;;
		acft.base_en)
			echo "acft.base.en"
			;;
		acft.small_en)
			echo "acft.small.en"
			;;
		*)
			echo "$model_name"
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

parse_args() {
	while [[ $# -gt 0 ]]; do
		case "$1" in
			--model)
				[[ $# -ge 2 ]] || die "$1 requires a value"
				add_selected_model "$(canonical_model_name "$2")"
				shift
				;;
			--quant|-q)
				[[ $# -ge 2 ]] || die "$1 requires a value"
				parse_quant_arg "$2"
				shift
				;;
			--force|-f)
				FORCE_QUANT=1
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

find_quantize_bin() {
	local candidate

	for candidate in \
		"$WHISPER_DIR/build-cpu/bin/quantize" \
		"$WHISPER_DIR/build-cpu/bin/whisper-quantize" \
		"$WHISPER_DIR/build-openblas/bin/quantize" \
		"$WHISPER_DIR/build-openblas/bin/whisper-quantize" \
		"$WHISPER_DIR/build-cuda/bin/quantize" \
		"$WHISPER_DIR/build-cuda/bin/whisper-quantize" \
		"$WHISPER_DIR/quantize"; do
		if [[ -x "$candidate" ]]; then
			echo "$candidate"
			return 0
		fi
	done

	die "could not find quantize binary in whisper.cpp build output"
}

detect_default_models() {
	local candidate
	local model_name

	if [[ "${#selected_models[@]}" -gt 0 ]]; then
		return
	fi

	shopt -s nullglob
	for candidate in "${MODELS_DIR}"/*.bin; do
		model_name="$(basename "$candidate" .bin)"
		case "$model_name" in
			*.q5|*.q8|*.q5_0|*.q8_0)
				continue
				;;
		esac
		add_selected_model "$(canonical_model_name "$model_name")"
	done
	shopt -u nullglob

	[[ "${#selected_models[@]}" -gt 0 ]] || die "no quantizable base models found in ${MODELS_DIR}"
}

quantize_one() {
	local quantize_bin="$1"
	local model_name="$2"
	local quant_level="$3"
	local input_file="${MODELS_DIR}/${model_name}.bin"
	local output_file="${MODELS_DIR}/${model_name}.${quant_level}.bin"
	local scheme="${quant_level}_0"

	[[ -f "$input_file" ]] || die "missing base model: ${input_file}"

	if [[ -f "$output_file" && "$FORCE_QUANT" -eq 0 ]]; then
		log "Skipping existing $(basename "$output_file")"
		return
	fi

	log "Quantizing $(basename "$input_file") -> $(basename "$output_file")"
	"$quantize_bin" "$input_file" "$output_file" "$scheme"
}

quantize_models() {
	local quantize_bin="$1"
	local model_name

	for model_name in "${selected_models[@]}"; do
		want_quant_level q5 && quantize_one "$quantize_bin" "$model_name" q5
		want_quant_level q8 && quantize_one "$quantize_bin" "$model_name" q8
	done
}

main() {
	local quantize_bin

	parse_args "$@"
	[[ -d "$MODELS_DIR" ]] || die "models dir not found: ${MODELS_DIR}"
	detect_default_models
	quantize_bin="$(find_quantize_bin)"
	quantize_models "$quantize_bin"
	log "Done"
}

main "$@"
