#!/usr/bin/env bash
set -Eeuo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "${script_dir}/.." && pwd)"
script_name="$(basename "${BASH_SOURCE[0]}")"
wcpp_dir="${project_root}/whisper.cpp"
odir="${project_root}/whisper"

work_dir="${project_root}/convert_models"
venv_dir="${work_dir}/.venv"

models_src_dir="${project_root}/models/hf"
models_out_dir="${project_root}/models/ggml"

converter="${wcpp_dir}/models/convert-h5-to-ggml.py"

MODEL_PREFIX="${MODEL_PREFIX:-}"
model_prefix="${MODEL_PREFIX:+${MODEL_PREFIX}-}"

force_all=0
force_venv=0
force_download=0
force_convert=0
download_only=0
all_models=0
rm_work_dir=0
assume_yes=0

selected_models=()
all_supported_models=(
  base.en
  base
  small.en
  small
  tiny.en
  tiny
)

warn() {
  echo "[WARN] $*"
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

require_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    die "missing required command: $cmd"
  fi
}

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    die "missing required file: $path"
  fi
}

require_dir() {
  local path="$1"
  if [[ ! -d "$path" ]]; then
    die "missing required directory: $path"
  fi
}

log() {
  echo "[INFO] $*"
}

usage() {
  cat <<EOF
Usage: ./scripts/${script_name} [--model NAME] [--all-models] [--download-only]
                           [--force] [--force-venv] [--force-download] [--force-convert]
                           [--rm-work-dir --yes]

  --model NAME      Process one model. Repeatable. Supported: ${all_supported_models[*]}
  --all-models      Process all supported models.
  --download-only   Download selected models without converting them.
  --force           Rebuild every stage.
  --force-venv      Recreate the virtual environment and reinstall packages.
  --force-download  Re-download model files even if they appear complete.
  --force-convert   Rebuild ggml output even if it already exists.
  --rm-work-dir     Remove ${work_dir} and exit. Requires --yes.
  --yes             Confirm destructive actions.
EOF
}

canonical_model_name() {
  local raw_name="$1"

  case "$raw_name" in
    base|base.en|small|small.en|tiny|tiny.en)
      echo "$raw_name"
      ;;
    acft-whisper-base)
      echo "base"
      ;;
    acft-whisper-base.en)
      echo "base.en"
      ;;
    acft-whisper-small)
      echo "small"
      ;;
    acft-whisper-small.en)
      echo "small.en"
      ;;
    acft-whisper-tiny)
      echo "tiny"
      ;;
    acft-whisper-tiny.en)
      echo "tiny.en"
      ;;
    *)
      die "unsupported model: ${raw_name}"
      ;;
  esac
}

add_selected_model() {
  local canonical_name="$1"
  local existing_name

  for existing_name in "${selected_models[@]:-}"; do
    if [[ "$existing_name" == "$canonical_name" ]]; then
      return
    fi
  done

  selected_models+=("$canonical_name")
}

model_repo_id() {
  local model_name="$1"
  echo "futo-org/acft-whisper-${model_name}"
}

model_local_name() {
  local model_name="$1"
  echo "acft-whisper-${model_name}"
}

model_output_name() {
  local model_name="$1"
  echo "${model_prefix}acft.${model_name}.bin"
}

finalize_selected_models() {
  local model_name

  if (( all_models )); then
    for model_name in "${all_supported_models[@]}"; do
      add_selected_model "$model_name"
    done
  fi

  if [[ ${#selected_models[@]} -eq 0 ]]; then
    for model_name in "${all_supported_models[@]}"; do
      add_selected_model "$model_name"
    done
  fi
}

handle_rm_work_dir() {
  if (( ! rm_work_dir )); then
    return
  fi

  if (( ! assume_yes )); then
    die "--rm-work-dir requires --yes"
  fi

  if (( all_models || download_only || force_all || force_venv || force_download || force_convert || ${#selected_models[@]} > 0 )); then
    die "--rm-work-dir cannot be combined with other actions"
  fi

  if [[ -d "${work_dir}" ]]; then
    rm -rf "${work_dir}"
    log "removed work dir: ${work_dir}"
  else
    log "work dir already absent: ${work_dir}"
  fi

  exit 0
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
        all_models=1
        ;;
      --download-only)
        download_only=1
        ;;
      --force)
        force_all=1
        ;;
      --force-venv)
        force_venv=1
        ;;
      --force-download)
        force_download=1
        ;;
      --force-convert)
        force_convert=1
        ;;
      --rm-work-dir)
        rm_work_dir=1
        ;;
      --yes)
        assume_yes=1
        ;;
      -h|--help)
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

  if (( force_all )); then
    force_venv=1
    force_download=1
    force_convert=1
  fi

  handle_rm_work_dir
  finalize_selected_models
}

ensure_whisper_repo() {
  if [[ -d "${odir}" ]]; then
    log "reusing whisper repo: ${odir}"
    return
  fi

  require_cmd git
  odir="${work_dir}/whisper"

  if [[ -d "${odir}" ]]; then
    log "reusing fallback whisper repo: ${odir}"
    return
  fi

  log "cloning whisper repo into ${odir}"
  git clone https://github.com/openai/whisper.git "${odir}"
}

venv_is_ready() {
  "${venv_dir}/bin/python" - <<'EOF' >/dev/null 2>&1
import importlib.util
import sys

required = ["huggingface_hub", "torch", "numpy", "transformers", "safetensors"]
missing = [name for name in required if importlib.util.find_spec(name) is None]
sys.exit(0 if not missing else 1)
EOF
}

ensure_venv() {
  require_cmd python3

  if (( force_venv )) && [[ -d "${venv_dir}" ]]; then
    log "recreating virtual environment: ${venv_dir}"
    rm -rf "${venv_dir}"
  fi

  if [[ ! -x "${venv_dir}/bin/python" ]]; then
    log "creating virtual environment: ${venv_dir}"
    python3 -m venv "${venv_dir}"
  else
    log "reusing virtual environment: ${venv_dir}"
  fi

  # shellcheck disable=SC1091
  source "${venv_dir}/bin/activate"

  if (( force_venv )) || ! venv_is_ready; then
    log "installing Python dependencies"
    python -m pip install -U pip
    python -m pip install \
      huggingface_hub \
      torch \
      numpy \
      transformers \
      safetensors
  else
    log "reusing installed Python dependencies"
  fi
}

model_has_weights() {
  local src_dir="$1"

  compgen -G "${src_dir}/*.safetensors" >/dev/null || [[ -f "${src_dir}/pytorch_model.bin" ]]
}

model_dir_is_ready() {
  local src_dir="$1"

  [[ -f "${src_dir}/config.json" ]] \
    && [[ -f "${src_dir}/vocab.json" ]] \
    && model_has_weights "${src_dir}"
}

ensure_hf_model() {
  local repo_id="$1"
  local local_name="$2"
  local dst_dir="${models_src_dir}/${local_name}"

  require_cmd hf

  if (( ! force_download )) && model_dir_is_ready "${dst_dir}"; then
    log "reusing model download: ${dst_dir}"
    return
  fi

  log "retrieving ${repo_id} -> ${dst_dir}"

  hf download "${repo_id}" \
    --local-dir "${dst_dir}"

  if ! model_dir_is_ready "${dst_dir}"; then
    die "downloaded model is incomplete: ${dst_dir}"
  fi
}

ensure_added_tokens() {
  local src_dir="$1"

  if [[ ! -f "${src_dir}/added_tokens.json" ]]; then
    echo '{}' > "${src_dir}/added_tokens.json"
  fi
}

ensure_ggml_model() {
  local src_name="$1"
  local out_name="$2"

  local src_dir="${models_src_dir}/${src_name}"
  local tmp_out_dir="${work_dir}/${out_name}.tmp"
  local final_out="${models_out_dir}/${out_name}"

  require_dir "${src_dir}"

  if (( ! force_convert )) && [[ -s "${final_out}" ]]; then
    rm -rf "${tmp_out_dir}"
    log "reusing ggml output: ${final_out}"
    return
  fi

  ensure_added_tokens "${src_dir}"

  rm -rf "${tmp_out_dir}"
  mkdir -p "${tmp_out_dir}"

  log "creating ${final_out}"

  python "${converter}" \
    "${src_dir}" \
    "${odir}" \
    "${tmp_out_dir}"

  require_file "${tmp_out_dir}/ggml-model.bin"

  mv -f "${tmp_out_dir}/ggml-model.bin" "${final_out}"
  rm -rf "${tmp_out_dir}"
}

parse_args "$@"

mkdir -p "${work_dir}" "${models_src_dir}" "${models_out_dir}"
cd "${work_dir}"

ensure_whisper_repo
require_file "${converter}"
require_dir "${odir}"

ensure_venv

for model_name in "${selected_models[@]}"; do
  ensure_hf_model "$(model_repo_id "$model_name")" "$(model_local_name "$model_name")"
done

if (( ! download_only )); then
  for model_name in "${selected_models[@]}"; do
    ensure_ggml_model "$(model_local_name "$model_name")" "$(model_output_name "$model_name")"
  done
fi

log "Done"