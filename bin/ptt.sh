#!/usr/bin/env bash
# Bind start to key press and stop to key release. Warm once with daemon.
set -euo pipefail
umask 077
repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python_bin=${TITSDIC_PYTHON:-"$repo_dir/.venv-phonon/bin/python"}
if [[ -z ${TITSDIC_PYTHON:-} && ! -x $python_bin ]]; then
    python_bin=$repo_dir/build/asr-compare/env-phonon/bin/python
fi
if [[ ! -x $python_bin ]]; then
    printf 'Dictation environment not found: %s\nRun bash install.sh first, or set TITSDIC_PYTHON.\n' "$python_bin" >&2
    exit 1
fi
runtime_dir=${XDG_RUNTIME_DIR:-/tmp/titsdic-$UID}/titsdic
socket_path=$runtime_dir/ptt.sock
export CUDA_VISIBLE_DEVICES='' FERMION_DEVICE=cpu OMP_WAIT_POLICY=PASSIVE
export PYTHONUNBUFFERED=1
command=${1:-help}
if (( $# )); then shift; fi
case "$command" in
    help|-h|--help)
        cat <<'EOF'
Usage: bin/ptt.sh daemon [--device INPUT] [--buffer-length SECONDS]
                         [--output-mode sentences|final] [--chunk-words N] [--dry-run]
       bin/ptt.sh start | stop | toggle | cancel | status | shutdown
       bin/ptt.sh report [--output FILE]
       bin/ptt.sh serve [options]

Run daemon once to load Phonon-2 on CPU and keep the microphone ready.
Keep 1.25 seconds of recent audio in memory before each press by default.
Use --buffer-length N to change it, or 0 to open the mic only on press.
Bind start to your PTT key's press event and stop to its release event.
Keep the target application focused throughout recording and typing. Settled
sentences are typed at pauses; release sends the remaining tail. --chunk-words
defaults to 40 (0 uses punctuation only). --output-mode final types after release.
cancel discards unsent text; shutdown exits.
serve runs in the foreground. Logs: $XDG_RUNTIME_DIR/titsdic/ptt.log
Use --dry-run to print transcripts without keyboard output.
report collects local troubleshooting details without capturing or typing.
EOF
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
    start|stop|toggle|cancel|status|shutdown)
        exec "$python_bin" "$repo_dir/dictation.py" command "$command" --socket "$socket_path" "$@"
        ;;
    *) printf 'Unknown PTT command: %s. Run bin/ptt.sh help.\n' "$command" >&2; exit 2 ;;
esac
