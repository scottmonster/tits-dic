#!/usr/bin/env bash
# Bind this script to one shortcut: tap once to start, again to stop.
set -euo pipefail
umask 077
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# Lifecycle commands must work even when the dictation environment is broken.
case "${1:-}" in
    update|uninstall) exec python3 "$repo_dir/scripts/manage-install.py" "$@" ;;
esac
python_bin=${TITSDIC_PYTHON:-"$repo_dir/.venv-phonon/bin/python"}
if [[ -z ${TITSDIC_PYTHON:-} && ! -x $python_bin ]]; then
    python_bin=$repo_dir/build/asr-compare/env-phonon/bin/python
fi
# Recording controls need only the standard library. Skip site initialization
# and the full runtime/config imports, and send the command before other setup.
case "${1:-toggle}" in
    start|stop|toggle|cancel|status|shutdown|confirm)
        command=${1:-toggle}
        if (( $# )); then shift; fi
        [[ -x $python_bin ]] || python_bin=python3
        exec "$python_bin" -S "$repo_dir/dictation_control.py" "$command" "$@"
        ;;
esac
if [[ ! -x $python_bin ]]; then
    printf 'Dictation environment not found: %s\nRun bash install.sh first, or set TITSDIC_PYTHON.\n' "$python_bin" >&2
    exit 1
fi
runtime_dir=${XDG_RUNTIME_DIR:-/tmp/titsdic-$UID}/titsdic
socket_path=$runtime_dir/ptt.sock
export CUDA_VISIBLE_DEVICES='' FERMION_DEVICE=cpu OMP_WAIT_POLICY=PASSIVE
export PYTHONUNBUFFERED=1
command=${1:-toggle}
if (( $# )); then shift; fi
case "$command" in
    help|-h|--help)
        cat <<'EOF'
Usage: bin/tap-to-talk.sh [toggle]
       bin/tap-to-talk.sh daemon [--device INPUT] [--buffer-length SECONDS]
                         [--output-mode sentences|final] [--chunk-words N] [--dry-run]
       bin/tap-to-talk.sh start | stop | toggle | cancel | status | shutdown
       bin/tap-to-talk.sh report [--output FILE]
       bin/tap-to-talk.sh serve [options]
       bin/tap-to-talk.sh config [path|init|show] [--config FILE]
       bin/tap-to-talk.sh update [--skip-system-packages] [--skip-typing-setup] [--no-start]
                               [--completion-shell auto|bash|zsh|both|none] [--completion-rc PATH]
       bin/tap-to-talk.sh uninstall

Run daemon once to load Phonon-2 on CPU and keep the microphone ready.
Keep 1.25 seconds of recent audio in memory before each press by default.
Use --buffer-length N to change it, or 0 to open the mic only on press.
Bind this script without arguments to one keyboard shortcut.
Tap once to start recording; tap again to stop. No arguments means toggle.
The daemon tracks recording state; wait for processing to finish before starting again.
Explicit start and stop commands are also available.
Keep the target application focused throughout recording and typing. Settled
sentences are typed at pauses; stopping sends the remaining tail. --chunk-words
defaults to 40 (0 uses punctuation only). --output-mode final types after stopping.
cancel discards unsent text; shutdown exits.
serve runs in the foreground. Logs: $XDG_RUNTIME_DIR/titsdic/ptt.log
Use --dry-run to print transcripts without keyboard output.
report collects local troubleshooting details without capturing or typing.
config prints the settings path; init creates defaults without overwriting.
serve/daemon read ~/.config/titsdic/config.toml (or $XDG_CONFIG_HOME/titsdic/config.toml).
Use --config FILE to select another file, or --no-config to ignore settings.
update fetches master and reruns the installer; it requires a clean checkout.
uninstall removes the service, launchers and shell completion, keeping the checkout, settings and data.
EOF
        ;;
    config)
        exec "$python_bin" "$repo_dir/settings.py" "$@"
        ;;
    serve)
        exec "$python_bin" "$repo_dir/dictation.py" serve --socket "$socket_path" "$@"
        ;;
    report)
        exec "$python_bin" "$repo_dir/scripts/collect-diagnostics.py" "$@"
        ;;
    daemon)
        if "$python_bin" "$repo_dir/dictation.py" command status --socket "$socket_path" >/dev/null 2>&1; then
            printf 'PTT daemon is already running.\n'
            exit 0
        fi
        mkdir -p -- "$runtime_dir"
        if [[ -L "$runtime_dir" || ! -O "$runtime_dir" || $(stat -c %a -- "$runtime_dir") != 700 ]]; then
            printf 'PTT runtime directory must be owned by you with mode 0700: %s\n' "$runtime_dir" >&2
            exit 1
        fi
        printf '\n--- PTT daemon launch %(%F %T %z)T ---\n' -1 >>"$runtime_dir/ptt.log"
        nohup setsid "$python_bin" "$repo_dir/dictation.py" serve --socket "$socket_path" "$@" >>"$runtime_dir/ptt.log" 2>&1 </dev/null &
        daemon_pid=$!
        for (( attempt=0; attempt<300; attempt++ )); do
            if "$python_bin" "$repo_dir/dictation.py" command status --socket "$socket_path" 2>/dev/null; then exit 0; fi
            if ! kill -0 "$daemon_pid" 2>/dev/null; then
                cat -- "$runtime_dir/ptt.log" >&2
                exit 1
            fi
            sleep .1
        done
        printf 'PTT startup timed out; inspect %s/ptt.log\n' "$runtime_dir" >&2
        exit 1
        ;;
    *) printf 'Unknown PTT command: %s. Run bin/tap-to-talk.sh help.\n' "$command" >&2; exit 2 ;;
esac
