#!/usr/bin/env bash
# Run as your desktop user, including when streamed through curl | bash.
set -Eeuo pipefail
trap 'printf "Installation failed at line %s. Fix the error above and rerun.\n" "$LINENO" >&2' ERR

usage() {
    cat <<'EOF'
Usage: bash install.sh [options]
       curl -fsSL RAW_INSTALL_URL | bash -s -- --repo GITHUB_REPO_URL

Without --repo, install the checkout containing this script in place.
With --repo, clone/update it in ~/.local/share/titsdic (or --install-dir).

  --repo URL                 Git repository to clone (HTTPS or SSH)
  --ref BRANCH_OR_TAG        Revision (default: repository default branch)
  --install-dir PATH         Checkout location; requires --repo
  --skip-system-packages     System dependencies are already installed
  --skip-typing-setup        Keep your existing ydotoold/uinput setup
  --no-start                 Enable autostart, but do not start dictation now
  -h, --help                 Show this help

Automatic system packages: Debian/Ubuntu/Pop!_OS with apt.
Other Linux distributions: install the README prerequisites yourself and use
--skip-system-packages. A working systemd user session is required.
No desktop shortcuts or physical keyboard permissions are configured.
EOF
}
die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
repo_url=''
ref=''
install_dir=''
skip_packages=0
skip_typing=0
start_now=1
while (( $# )); do
    case "$1" in
        --repo|--ref|--install-dir)
            (( $# >= 2 )) && [[ -n $2 && $2 != -* ]] || die "$1 needs a value"
            case "$1" in
                --repo) repo_url=$2 ;;
                --ref) ref=$2 ;;
                --install-dir) install_dir=$2 ;;
            esac
            shift 2 ;;
        --skip-system-packages) skip_packages=1; shift ;;
        --skip-typing-setup) skip_typing=1; shift ;;
        --no-start) start_now=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1 (see --help)" ;;
    esac
done
[[ $(uname -s) == Linux ]] || die 'This installer requires Linux.'
(( EUID != 0 )) || die 'Run as your desktop user, without sudo. Only system setup uses sudo.'
command -v systemctl >/dev/null || die 'systemd is required.'
systemctl --user show-environment >/dev/null || die 'Run from a terminal in your logged-in desktop session (systemd --user is unavailable).'
[[ -z $install_dir || -n $repo_url ]] || die '--install-dir requires --repo.'
[[ $ref != -* ]] || die 'Invalid --ref.'

# Resolve local mode before doing any installation. A streamed script has no
# reliable source directory; it must be given its repository explicitly.
if [[ -z $repo_url ]]; then
    script_path=${BASH_SOURCE[0]:-}
    [[ -n $script_path && -f $script_path ]] || die 'When piping to bash, pass --repo https://github.com/OWNER/REPO.git.'
    install_dir=$(cd -- "$(dirname -- "$script_path")" && pwd -P)
    [[ -f $install_dir/dictation.py && -f $install_dir/requirements-dictation.txt ]] || die 'Run the install.sh from a complete checkout, or pass --repo.'
else
    install_dir=${install_dir:-${XDG_DATA_HOME:-$HOME/.local/share}/titsdic}
fi
[[ $install_dir != *$'\n'* && $install_dir != *$'\r'* ]] || die 'Install paths cannot contain newlines.'

as_root() {
    command -v sudo >/dev/null || die 'sudo is required for system setup; install prerequisites manually and use the skip options.'
    # sudo reads its password from the terminal, never the piped script.
    sudo "$@"
}

if (( ! skip_packages )); then
    command -v apt-get >/dev/null || die 'Automatic packages require apt. See README prerequisites and --skip-system-packages.'
    printf 'Installing system dependencies (sudo may ask for your password)...\n'
    as_root apt-get update </dev/null
    packages=(ca-certificates curl git ffmpeg libportaudio2 libsndfile1
              ydotool libglib2.0-bin pulseaudio-utils python3)
    # Older distributions package the daemon separately; newer ones bundle it.
    if apt-cache show ydotoold >/dev/null 2>&1; then packages+=(ydotoold); fi
    as_root apt-get install -y "${packages[@]}" </dev/null
fi
for dependency in curl git ffmpeg python3 ydotool ydotoold; do
    command -v "$dependency" >/dev/null || die "Missing $dependency. Install the README prerequisites first."
done

assert_idle() {
    local status state
    if [[ -x $install_dir/bin/ptt.sh ]] && status=$(bash "$install_dir/bin/ptt.sh" status 2>/dev/null); then
        state=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])' <<<"$status")
        [[ $state == idle ]] || die 'Dictation is busy. Finish/cancel it and rerun the installer.'
    fi
}
assert_idle

if [[ -n $repo_url ]]; then
    if [[ -e $install_dir ]]; then
        [[ -d $install_dir/.git ]] || die "Already exists and is not a Git checkout: $install_dir"
        [[ $(git -C "$install_dir" remote get-url origin) == "$repo_url" ]] || die 'Existing checkout has a different origin; use a different --install-dir.'
        [[ -z $(git -C "$install_dir" status --porcelain) ]] || die 'Checkout has local changes. Commit/stash them before updating, or use a different --install-dir.'
        update_ref=$ref
        if [[ -z $update_ref ]]; then
            update_ref=$(git -C "$install_dir" symbolic-ref refs/remotes/origin/HEAD) || die 'Cannot determine default branch; pass --ref.'
            update_ref=${update_ref#refs/remotes/origin/}
        fi
        git -C "$install_dir" fetch origin "$update_ref" </dev/null
        git -C "$install_dir" merge --ff-only FETCH_HEAD </dev/null
    else
        mkdir -p -- "$(dirname -- "$install_dir")"
        clone_options=(--single-branch)
        [[ -z $ref ]] || clone_options+=(--branch "$ref")
        git clone "${clone_options[@]}" -- "$repo_url" "$install_dir" </dev/null
    fi
fi
install_dir=$(cd -- "$install_dir" && pwd -P)
[[ -f $install_dir/requirements-dictation.txt && -f $install_dir/bin/ptt.sh ]] || die 'The selected revision does not include the dictation installer/runtime.'

export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null; then
    printf 'Installing uv into ~/.local/bin...\n'
    uv_installer=$(mktemp)
    trap 'rm -f -- "$uv_installer"' EXIT
    curl --fail --silent --show-error --location https://astral.sh/uv/install.sh -o "$uv_installer"
    UV_INSTALL_DIR="$HOME/.local/bin" UV_NO_MODIFY_PATH=1 sh "$uv_installer" </dev/null
    rm -f -- "$uv_installer"
    trap - EXIT
fi

cd -- "$install_dir"
assert_idle
# A separate environment avoids the experimental Whisper dependency tree.
if [[ ! -x .venv-phonon/bin/python ]]; then
    uv venv --python 3.13 .venv-phonon </dev/null
fi
uv pip sync --python .venv-phonon/bin/python requirements-dictation.txt \
    --extra-index-url https://download.pytorch.org/whl/cpu --index-strategy unsafe-best-match </dev/null
printf 'Checking CPU runtime and downloading/caching Phonon-2 (first install needs network)...\n'
CUDA_VISIBLE_DEVICES='' FERMION_DEVICE=cpu OMP_WAIT_POLICY=PASSIVE .venv-phonon/bin/python - <<'PY'
import ffmpeg
import sounddevice
import webrtcvad
from asr_backend import PhononModel
PhononModel(n_threads=4)
print('CPU runtime and model are ready.')
PY

config_dir=${XDG_CONFIG_HOME:-$HOME/.config}
unit_dir=$config_dir/systemd/user
bin_dir=$HOME/.local/bin
mkdir -p -- "$unit_dir" "$bin_dir"

if (( ! skip_typing )); then
    # Only grant access to the virtual input device to the active local user.
    # Do not add users to the input group or grant access to physical keyboards.
    if [[ ! -f /etc/modules-load.d/titsdic-uinput.conf || ! -f /etc/udev/rules.d/70-titsdic-uinput.rules ||
          ! -r /dev/uinput || ! -w /dev/uinput ]]; then
        printf 'Enabling virtual keyboard access for the active desktop session...\n'
        as_root modprobe uinput </dev/null
        printf 'uinput\n' | as_root tee /etc/modules-load.d/titsdic-uinput.conf >/dev/null
        printf 'SUBSYSTEM=="misc", KERNEL=="uinput", TAG+="uaccess", OPTIONS+="static_node=uinput"\n' \
            | as_root tee /etc/udev/rules.d/70-titsdic-uinput.rules >/dev/null
        as_root udevadm control --reload-rules </dev/null
        as_root udevadm trigger --action=add /sys/class/misc/uinput </dev/null
        as_root udevadm settle </dev/null
    fi
    [[ -r /dev/uinput && -w /dev/uinput ]] || die 'uinput is still inaccessible. Log out/in to apply desktop device access, then rerun.'
    # Preserve any existing user service, including a custom socket setup.
    if [[ $(systemctl --user show ydotoold.service -p LoadState --value) == not-found ]]; then
        ydotoold_path=$(command -v ydotoold)
        cat >"$unit_dir/ydotoold.service" <<EOF
[Unit]
Description=ydotool virtual keyboard daemon
StartLimitIntervalSec=0

[Service]
ExecStart=$ydotoold_path
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
    fi
    systemctl --user daemon-reload
    systemctl --user enable --now ydotoold.service
    systemctl --user is-active --quiet ydotoold.service || die 'ydotoold failed. Inspect journalctl --user -u ydotoold.service.'
fi

# Write a unit with quoted/escaped absolute paths. User configuration belongs
# in systemctl --user edit titsdic.service; reruns preserve those drop-ins.
.venv-phonon/bin/python - "$install_dir" "$unit_dir/titsdic.service" <<'PY'
from pathlib import Path
import sys
root, destination = sys.argv[1:]
def quoted(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
executable = quoted(root + '/bin/ptt.sh')
Path(destination).write_text(f'''[Unit]
Description=Tell It Something Dictation (Phonon-2 CPU)
After=pipewire.service pipewire-pulse.service pulseaudio.service ydotoold.service
StartLimitIntervalSec=0

[Service]
Type=simple
WorkingDirectory={root.replace('%', '%%')}
ExecStart={executable} serve
Environment=PYTHONUNBUFFERED=1
Environment={quoted('TITSDIC_PYTHON=' + root + '/.venv-phonon/bin/python')}
Restart=always
RestartSec=3
TimeoutStopSec=30
UMask=0077

[Install]
WantedBy=default.target
''')
PY

# Shell wrappers work even if the checkout path contains spaces. Do not use
# symlinks: the launchers locate Python and source files relative to themselves.
for launcher in titsdic titsdic-ptt; do
    target=bin/dictate
    [[ $launcher != titsdic-ptt ]] || target=bin/ptt.sh
    { printf '#!/usr/bin/env bash\nexec '; printf '%q' "$install_dir/$target"; printf ' "$@"\n'; } >"$bin_dir/$launcher"
    chmod +x -- "$bin_dir/$launcher"
done

systemctl --user daemon-reload
systemctl --user enable titsdic.service
if (( start_now )); then
    # Take over an old manually launched daemon only when it is idle.
    if status=$(./bin/ptt.sh status 2>/dev/null); then
        state=$(.venv-phonon/bin/python -c 'import json,sys; print(json.load(sys.stdin)["state"])' <<<"$status")
        [[ $state == idle ]] || die 'Dictation is busy. Finish/cancel it and rerun the installer.'
        if ! systemctl --user is-active --quiet titsdic.service; then
            ./bin/ptt.sh shutdown
            # Shutdown acknowledges before socket/lock cleanup is complete.
            for (( attempt=0; attempt<50; attempt++ )); do
                ./bin/ptt.sh status >/dev/null 2>&1 || break
                sleep .1
            done
        fi
    fi
    systemctl --user restart titsdic.service
    ready=0
    for (( attempt=0; attempt<180; attempt++ )); do
        if ./bin/ptt.sh status; then ready=1; break; fi
        sleep 1
    done
    if (( ! ready )); then
        journalctl --user -u titsdic.service -n 40 --no-pager >&2
        die 'Dictation did not become ready. Fix the logged error, then systemctl --user restart titsdic.service.'
    fi
fi
printf '\nInstalled in %s\n' "$install_dir"
printf 'Autostart enabled: dictation starts when you log in after reboot and restarts if it exits.\n'
printf 'Commands: %s/titsdic and %s/titsdic-ptt\n' "$bin_dir" "$bin_dir"
printf 'Status: systemctl --user status titsdic.service\nLogs: journalctl --user -u titsdic.service -f\n'
printf 'Stop: systemctl --user stop titsdic.service\nDisable autostart: systemctl --user disable --now titsdic.service\n'
printf 'Configure your own desktop shortcuts; none were installed.\n'
