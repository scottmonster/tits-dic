#!/usr/bin/env bash

set -Eeuo pipefail

# SET AND DETECT VARIABLES NEEDED

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WHISPER_REPO_URL="https://github.com/ggml-org/whisper.cpp.git"
WHISPER_DIR="${PROJECT_ROOT}/whisper.cpp"
ADD_FLAGS_SCRIPT="${PROJECT_ROOT}/add_flags.sh"
BUILD_DIR=""
BUILD_STATE_FILE=""
BACKEND=""
CLEAN_BUILD=0
ADD_FLAGS=0
BUILD_ALL=0

log() {
  echo "[INFO] $*"
}

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

usage() {
  cat <<'EOF'
Usage: ./build-whisper.sh [--clean|-c] [--add-flags|-af] [--all|--cpu|--openblas|--blas|--cuda]
EOF
}

set_build_paths() {
  BUILD_DIR="${WHISPER_DIR}/build-${BACKEND}"
  BUILD_STATE_FILE="${BUILD_DIR}/.build-backend"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --clean|-c)
        CLEAN_BUILD=1
        ;;
      --add-flags|-af)
        ADD_FLAGS=1
        ;;
      --all)
        BUILD_ALL=1
        BACKEND="cpu"
        ;;
      --cpu)
        BACKEND="cpu"
        ;;
      --openblas|--blas)
        BACKEND="openblas"
        ;;
      --cuda)
        BACKEND="cuda"
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        usage
        die "unknown argument: $1"
        ;;
    esac
    shift
  done

  if [[ "$BUILD_ALL" -eq 1 ]]; then
    return
  fi

  [[ -n "$BACKEND" ]] || {
    usage
    die "one backend is required"
  }
}

run_selected_build() {
  BACKEND="$1"
  set_build_paths
  log "Selected backend: ${BACKEND}"
  install_deps

  case "$BACKEND" in
    cpu)
      build_cpu
      ;;
    openblas)
      build_openblas
      ;;
    cuda)
      build_cuda
      ;;
  esac
}

pick_apt_frontend() {
  if command -v nala &>/dev/null; then
    echo "nala"
  else
    echo "apt-get"
  fi
}

install_deps() {
  local apt_cmd
  local -a packages
  local -a missing_packages
  packages=(git ca-certificates build-essential cmake pkg-config pciutils)

  case "$BACKEND" in
    openblas)
      packages+=(libopenblas-dev)
      ;;
    cuda)
      packages+=(nvidia-cuda-toolkit)
      ;;
  esac

  missing_packages=()
  missing_deps missing_packages "${packages[@]}"
  if [[ "${#missing_packages[@]}" -eq 0 ]]; then
    log "Dependencies already installed"
    return
  fi

  apt_cmd="$(pick_apt_frontend)"
  log "Installing dependencies with ${apt_cmd}"
  sudo "$apt_cmd" update
  sudo "$apt_cmd" install -y "${missing_packages[@]}"
}

missing_deps() {
  local -n missing_ref="$1"
  shift
  local pkg

  for pkg in "$@"; do
    case "$pkg" in
      git)
        command -v git &>/dev/null || missing_ref+=(git)
        ;;
      ca-certificates)
        [[ -f /etc/ssl/certs/ca-certificates.crt ]] || missing_ref+=(ca-certificates)
        ;;
      build-essential)
        command -v cc &>/dev/null || missing_ref+=(build-essential)
        ;;
      cmake)
        command -v cmake &>/dev/null || missing_ref+=(cmake)
        ;;
      pkg-config)
        command -v pkg-config &>/dev/null || missing_ref+=(pkg-config)
        ;;
      pciutils)
        command -v lspci &>/dev/null || missing_ref+=(pciutils)
        ;;
      libopenblas-dev)
        if ! command -v pkg-config &>/dev/null || ! pkg-config --exists openblas; then
          missing_ref+=(libopenblas-dev)
        fi
        ;;
      nvidia-cuda-toolkit)
        command -v nvcc &>/dev/null || missing_ref+=(nvidia-cuda-toolkit)
        ;;
    esac
  done
}

latest_release_tag() {
  git ls-remote --refs --tags "$WHISPER_REPO_URL" \
    | awk -F/ '{print $3}' \
    | grep -E '^v?[0-9]+(\.[0-9]+)*$' \
    | sort -V \
    | tail -n1
}

clone_or_update_whisper() {
  local release_tag
  local current_tag

  if [[ ! -d "${WHISPER_DIR}/.git" ]]; then
    log "Cloning whisper.cpp"
    git clone "$WHISPER_REPO_URL" "$WHISPER_DIR"
  fi

  release_tag="$(latest_release_tag || true)"
  if [[ -n "$release_tag" ]]; then
    current_tag="$(git -C "$WHISPER_DIR" describe --tags --exact-match 2>/dev/null || true)"
    if [[ "$current_tag" == "$release_tag" ]]; then
      log "whisper.cpp already at latest release: ${release_tag}"
      return
    fi

    log "Fetching tags for whisper.cpp"
    git -C "$WHISPER_DIR" fetch --tags origin
    log "Checking out latest release: ${release_tag}"
    git -c advice.detachedHead=false -C "$WHISPER_DIR" checkout "$release_tag"
    log "whisper.cpp is now on ${release_tag}"
    return
  fi

  read -r -p "No release tag found. Use master instead? [y/N] " reply
  if [[ "$reply" =~ ^[Yy]$ ]]; then
    log "Checking out master"
    git -C "$WHISPER_DIR" checkout master
    git -C "$WHISPER_DIR" pull --ff-only origin master
    log "whisper.cpp updated on master"
    return
  fi

  die "no release tag available and master was declined"
}

clean_build_dir() {
  if [[ "$CLEAN_BUILD" -eq 1 ]]; then
    log "Removing build dir"
    rm -rf "$BUILD_DIR"
  fi
}

has_whisper_cli_bin() {
  local candidate

  for candidate in \
    "$BUILD_DIR/bin/whisper-cli" \
    "$BUILD_DIR/bin/main"; do
    if [[ -x "$candidate" ]]; then
      return 0
    fi
  done

  return 1
}

build_common() {
  local extra_args="$1"
  # -DWHISPER_BUILD_EXAMPLES=OFF -- this would prevent build/bin/* from being builrt
  local common_args="-DWHISPER_BUILD_TESTS=OFF -DWHISPER_SDL2=ON"

  if should_skip_build; then
    log "Skipping build for ${BACKEND}; existing build matches"
    return
  fi

  clean_build_dir
  log "Configuring ${BACKEND} build in ${BUILD_DIR}"
  cmake -S "$WHISPER_DIR" -B "$BUILD_DIR" $common_args $extra_args
  log "Building ${BACKEND} targets"
  cmake --build "$BUILD_DIR" -j"$(nproc)"
  mkdir -p "$BUILD_DIR"
  echo "$BACKEND" > "$BUILD_STATE_FILE"
  log "Saved build state for ${BACKEND}"
}

built_backend() {
  if [[ -f "$BUILD_STATE_FILE" ]]; then
    cat "$BUILD_STATE_FILE"
    return 0
  fi

  [[ -f "$BUILD_DIR/CMakeCache.txt" ]] || return 1

  if grep -Eq '^GGML_CUDA(:BOOL)?=(ON|1)$' "$BUILD_DIR/CMakeCache.txt"; then
    echo "cuda"
    return 0
  fi

  if grep -Eq '^GGML_BLAS(:BOOL)?=(ON|1)$' "$BUILD_DIR/CMakeCache.txt"; then
    echo "openblas"
    return 0
  fi

  echo "cpu"
}

should_skip_build() {
  local current_backend

  [[ "$CLEAN_BUILD" -eq 0 ]] || return 1
  has_whisper_cli_bin || return 1
  current_backend="$(built_backend 2>/dev/null || true)"
  [[ "$current_backend" == "$BACKEND" ]]
}



check_nvidia() {
  local args=""
  if lspci | grep -qi nvidia; then
    log "NVIDIA GPU detected"

    if ! command -v nvidia-smi &>/dev/null; then
      warn "NVIDIA GPU present but drivers not installed (missing nvidia-smi)"
      warn "Install drivers via your package manager (e.g., apt, dnf, pacman)"
      return 1
    fi

    log "NVIDIA drivers detected"

    if ! command -v nvcc &>/dev/null; then
      warn "CUDA toolkit not installed (missing nvcc)"
      warn "Search your package manager for 'cuda' or install from:"
      warn "       https://docs.nvidia.com/cuda/cuda-installation-guide-linux/"
      warn "       https://developer.nvidia.com/cuda-downloads"
      return 1
    fi

    log "CUDA toolkit detected"

    args+=" -DGGML_CUDA=1"
    log "Adding build arg: -DGGML_CUDA=1"

    # Detect compute capability
    local cc
    cc=$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | head -n1)

    if [[ "$cc" == "8.6" ]]; then
      args+=" -DCMAKE_CUDA_ARCHITECTURES=86"
      log "Detected compute capability 8.6 (Ampere)"
      log "Adding build arg: -DCMAKE_CUDA_ARCHITECTURES=86"
    else
      log "Compute capability: ${cc:-unknown}"
      log "Skipping explicit CUDA arch (letting CMake auto-detect)"
    fi

  else
    log "No NVIDIA GPU detected"
    return 1
  fi

  echo "$args"
}

check_openblas() {
  local args=""

  # pkg-config is the most reliable detection method
  if command -v pkg-config &>/dev/null; then
    if pkg-config --exists openblas; then
      log "OpenBLAS detected (pkg-config)"

      args+=" -DGGML_BLAS=1 -DGGML_BLAS_VENDOR=OpenBLAS"
      log "Adding build args: -DGGML_BLAS=1 -DGGML_BLAS_VENDOR=OpenBLAS"
    else
      warn "OpenBLAS not found via pkg-config"
      warn "Install via your package manager (search: openblas, libopenblas-dev, openblas-devel)"
      return 1
    fi

  else
    warn "pkg-config not installed; falling back to manual detection"

    # Fallback: look for common library names
    if ldconfig -p 2>/dev/null | grep -qi openblas; then
      log "OpenBLAS detected (ldconfig)"

      args+=" -DGGML_BLAS=1 -DGGML_BLAS_VENDOR=OpenBLAS"
      log "Adding build args: -DGGML_BLAS=1 -DGGML_BLAS_VENDOR=OpenBLAS"
    else
      warn "OpenBLAS not detected"
      warn "Install OpenBLAS via your package manager:"
      warn "       Debian/Ubuntu: libopenblas-dev"
      warn "       Fedora/RHEL: openblas-devel"
      warn "       Arch: openblas"
      return 1
    fi
  fi

  echo "$args"
}

# build functions needed
build_cpu() {
  build_common "-DGGML_BLAS=OFF -DGGML_CUDA=OFF"
}

build_openblas() {
  local build_args
  build_args="$(check_openblas)" || die "OpenBLAS requested but not detected"
  build_common "$build_args -DGGML_CUDA=OFF"
}

build_cuda() {
  local build_args
  build_args="$(check_nvidia)" || die "CUDA requested but NVIDIA/CUDA was not detected"
  build_common "$build_args -DGGML_BLAS=OFF"
}

main() {
  parse_args "$@"
  clone_or_update_whisper

  if [[ "$ADD_FLAGS" -eq 1 ]]; then
    [[ -f "$ADD_FLAGS_SCRIPT" ]] || die "add-flags script not found: $ADD_FLAGS_SCRIPT"
    bash "$ADD_FLAGS_SCRIPT" "$WHISPER_DIR"
  fi

  if [[ "$BUILD_ALL" -eq 1 ]]; then
    run_selected_build cpu
    run_selected_build openblas
    run_selected_build cuda
  else
    run_selected_build "$BACKEND"
  fi

  log "Done"
}

main "$@"
# 