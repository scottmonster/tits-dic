#!/usr/bin/env bash
TONE_VOLUME_PERCENT=${TONE_VOLUME_PERCENT:-50} # 50 = half the original tone amplitude.
# Timing starts at Bash entry; capture held keys before preparing playback.
start_us=${EPOCHREALTIME/./}
set -Eeuo pipefail
export TITSDIC_RUN_ID=${TITSDIC_RUN_ID:-"hotkey-$start_us-$$"}

usage() {
    printf 'Usage: %s [--release all|any] [--log FILE] [--device PULSE_SINK] [--sound-only]\n' "$0"
    printf 'Launch from a shortcut while its key(s) are held.\n'
    printf 'Start dictation and play a tone; release stops capture, completes typing, and plays the exit tone.\n'
    printf 'First run bin/ptt.sh daemon to keep Phonon-2 loaded. --sound-only disables dictation.\n'
    printf 'TONE_VOLUME_PERCENT at the top controls tone amplitude (default: 50).\n'
    printf 'Default: all original keys; --release any ends on the first release.\n'
    printf 'Timings go to stderr and build/sound-key-test/timing.log, in milliseconds since script entry.\n'
    printf 'Player times include the 180 ms tone; they do not measure physical speaker onset.\n'
}
release=all
log_file=''
audio_device=''
dictation_enabled=1
while (( $# )); do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --sound-only) dictation_enabled=0; shift ;;
        --release|--log|--device)
            if (( $# < 2 )); then usage >&2; exit 2; fi
            case "$1" in
                --release) release=$2 ;;
                --log) log_file=$2 ;;
                --device) audio_device=$2 ;;
            esac
            shift 2
            ;;
        *) usage >&2; exit 2 ;;
    esac
done
if [[ ! $release =~ ^(all|any)$ ]]; then usage >&2; exit 2; fi
if [[ ! $TONE_VOLUME_PERCENT =~ ^(0|[1-9][0-9]?|100)$ ]]; then
    printf 'TONE_VOLUME_PERCENT must be an integer from 0 to 100.\n' >&2
    exit 2
fi
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
log_file=${log_file:-"$script_dir/../build/sound-key-test/timing.log"}
sound_dir=$script_dir/../res/sound-test
ptt_command=${PTT_COMMAND:-"$script_dir/ptt.sh"}

# Snapshot before log-file I/O or any playback work. The monitor retains releases
# during setup and playback, including a quick release followed by a new press.
watch_launched_us=${EPOCHREALTIME/./}
coproc KEY_WATCH { exec python3 "$script_dir/../scripts/watch-held-keys.py" --release "$release" --timings 2>&1; }
watch_pid=$KEY_WATCH_PID
exec {watch_fd}<&"${KEY_WATCH[0]}"
start_played=0
release_observed_us=''
watch_release_received=0
log_fd=''
ptt_started=0
start_player_pid=''

init_log() {
    if [[ $log_file == */* ]]; then mkdir -p -- "${log_file%/*}"; fi
    exec {log_fd}>>"$log_file"
    printf '\n--- sound-test pid=%s run=%s at %(%F %T %z)T ---\n' "$$" "$TITSDIC_RUN_ID" -1 >&"$log_fd"
    printf 'Timing log: %s\n' "$log_file" >&2
}
timing_at() {
    local label=$1 at_us=$2 elapsed line
    elapsed=$((at_us - start_us))
    printf -v line '[sound-test pid=%s run=%s] +%d.%03d ms  %s' "$$" "$TITSDIC_RUN_ID" "$((elapsed / 1000))" "$((elapsed % 1000))" "$label"
    printf '%s\n' "$line" >&2
    if [[ -n $log_fd ]]; then printf '%s\n' "$line" >&"$log_fd"; fi
}
span() {
    local label=$1 from_us=$2 to_us=$3 elapsed duration
    elapsed=$((to_us - from_us))
    printf -v duration '%d.%03d' "$((elapsed / 1000))" "$((elapsed % 1000))"
    timing_at "$label: $duration ms" "$to_us"
}
play_sound() {
    local name=$1 requested_us finished_us status=0
    requested_us=${EPOCHREALTIME/./}
    timing_at "$name playback requested" "$requested_us"
    if [[ $name == start ]]; then
        span 'script entry -> start request' "$start_us" "$requested_us"
    elif [[ -n $release_observed_us ]]; then
        span 'release observed -> exit request' "$release_observed_us" "$requested_us"
    fi
    "${player[@]}" "$sound_dir/$name.wav" >>"$log_file" 2>&1 || status=$?
    finished_us=${EPOCHREALTIME/./}
    span "$name player runtime (includes 180 ms tone)" "$requested_us" "$finished_us"
    if (( status )); then timing_at "$name playback failed (exit=$status); player error is in this log" "$finished_us"; fi
    return "$status"
}
on_error() {
    local status=$1 line=$2 command=$3
    timing_at "command failed: exit=$status line=$line command=$command" "${EPOCHREALTIME/./}"
}
ptt_call() {
    local action=$1 requested_us response status=0
    requested_us=${EPOCHREALTIME/./}
    timing_at "dictation $action requested" "$requested_us"
    response=$("$ptt_command" "$action" 2>&1) || status=$?
    span "dictation $action command" "$requested_us" "${EPOCHREALTIME/./}"
    timing_at "dictation $action response: $response" "${EPOCHREALTIME/./}"
    return "$status"
}
finish() {
    local status=$?
    trap - EXIT ERR INT TERM HUP
    if (( ! watch_release_received )); then kill "$watch_pid" 2>/dev/null || true; fi
    # Stop capture before waiting for playback. A slow Bluetooth player must not
    # keep recording after key release. Only control a session we started.
    if (( ptt_started )); then
        if (( watch_release_received )); then
            if ! ptt_call stop; then
                ptt_call cancel || true
                status=1
            fi
        else
            ptt_call cancel || true
        fi
    fi
    if [[ -n $start_player_pid ]]; then wait "$start_player_pid" || true; fi
    if (( start_played )) && [[ -f "$sound_dir/end.wav" ]]; then
        play_sound end || true
    fi
    wait "$watch_pid" 2>/dev/null || true
    timing_at "script exiting (status=$status)" "${EPOCHREALTIME/./}"
    exit "$status"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
trap 'on_error "$?" "$LINENO" "$BASH_COMMAND"' ERR
if ! IFS=$'\t' read -r -u "$watch_fd" kind capture_started_us capture_finished_us labels_ready_us held_keys; then
    status=0
    wait "$watch_pid" || status=$?
    (( status != 0 )) || status=1
    init_log
    timing_at 'script entered' "$start_us"
    timing_at 'key snapshot failed (no playback requested)' "${EPOCHREALTIME/./}"
    exit "$status"
fi
snapshot_received_us=${EPOCHREALTIME/./}
init_log
if [[ ! $kind =~ ^(HELD|EMPTY)$ || ! $capture_started_us =~ ^[0-9]+$ || ! $capture_finished_us =~ ^[0-9]+$ || ! $labels_ready_us =~ ^[0-9]+$ ]]; then
    timing_at "Invalid keyboard monitor response: $kind $capture_started_us $capture_finished_us" "${EPOCHREALTIME/./}"
    exit 1
fi
timing_at 'script entered' "$start_us"
timing_at 'key monitor launched' "$watch_launched_us"
span 'monitor launch/imports' "$watch_launched_us" "$capture_started_us"
span 'keyboard device scan' "$capture_started_us" "$capture_finished_us"
span 'key label lookup' "$capture_finished_us" "$labels_ready_us"
timing_at 'snapshot received by Bash' "$snapshot_received_us"
if [[ $kind == EMPTY ]]; then
    timing_at 'no keys held (no playback requested)' "${EPOCHREALTIME/./}"
    status=0
    wait "$watch_pid" || status=$?
    (( status != 0 )) || status=2
    exit "$status"
fi
printf 'Keys held at startup: %s\n' "$held_keys"
timing_at "keys=$held_keys release=$release dictation=$dictation_enabled" "${EPOCHREALTIME/./}"

if [[ ! -f "$sound_dir/start.wav" || ! -f "$sound_dir/end.wav" ]]; then
    printf 'Pre-generated tones are missing: %s\n' "$sound_dir" >&2
    exit 1
fi
# Cache scaled PCM, applying the same gain with every playback backend. The
# normal 50% files are reused; changing the variable builds a cache once.
if (( TONE_VOLUME_PERCENT != 100 )); then
    source_sound_dir=$sound_dir
    sound_dir=${SOUND_TONE_CACHE_DIR:-"$script_dir/../build/sound-key-test/tones"}/$TONE_VOLUME_PERCENT
    if [[ ! -f "$sound_dir/start.wav" || ! -f "$sound_dir/end.wav" \
          || "$source_sound_dir/start.wav" -nt "$sound_dir/start.wav" \
          || "$source_sound_dir/end.wav" -nt "$sound_dir/end.wav" ]]; then
        /usr/bin/python3 - "$source_sound_dir" "$sound_dir" "$TONE_VOLUME_PERCENT" 2>>"$log_file" <<'PY'
import array, os, pathlib, sys, tempfile, wave
source, target = map(pathlib.Path, sys.argv[1:3])
gain = int(sys.argv[3]) / 100
target.mkdir(parents=True, exist_ok=True)
for name in ('start', 'end'):
    with wave.open(str(source / f'{name}.wav'), 'rb') as reader:
        params = reader.getparams()
        if reader.getsampwidth() != 2:
            raise ValueError('Tone cache expects 16-bit PCM')
        samples = array.array('h', reader.readframes(reader.getnframes()))
    if sys.byteorder != 'little': samples.byteswap()
    scaled = array.array('h', (round(value * gain) for value in samples))
    if sys.byteorder != 'little': scaled.byteswap()
    fd, temporary = tempfile.mkstemp(dir=target, suffix='.wav')
    try:
        with os.fdopen(fd, 'wb') as output:
            with wave.open(output, 'wb') as writer:
                writer.setparams(params)
                writer.writeframes(scaled.tobytes())
        os.replace(temporary, target / f'{name}.wav')
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
PY
    fi
fi
if [[ -n ${SOUND_PLAYER:-} ]]; then
    player=("$SOUND_PLAYER")
elif command -v paplay >/dev/null; then
    # Default buffering can swamp a short notification. Request a small buffer.
    player=(paplay --latency-msec=20 --process-time-msec=5)
    if [[ -n $audio_device ]]; then player+=("--device=$audio_device"); fi
elif command -v pw-play >/dev/null; then
    player=(pw-play)
elif command -v aplay >/dev/null; then
    player=(aplay -q)
else
    printf 'Install paplay, pw-play, or aplay to play the sounds.\n' >&2
    exit 1
fi
if [[ -n $audio_device && ${player[0]} != paplay ]]; then
    printf '--device requires paplay.\n' >&2
    exit 1
fi
timing_at "tones ready; volume=$TONE_VOLUME_PERCENT%; player=${player[*]}" "${EPOCHREALTIME/./}"
# If the shortcut was already released during setup, don't open a late recording.
if (( dictation_enabled )); then
    if read -t 0 -u "$watch_fd"; then
        timing_at 'shortcut released during startup; capture skipped' "${EPOCHREALTIME/./}"
    else
        if ptt_call start; then
            ptt_started=1
        else
            printf 'Cannot start dictation. Run bin/ptt.sh daemon first, or use --sound-only.\n' >&2
            exit 1
        fi
    fi
fi
start_played=1
(
    trap - EXIT INT TERM HUP ERR
    play_sound start
) &
start_player_pid=$!
if ! IFS=$'\t' read -r -u "$watch_fd" result release_observed_us; then
    timing_at 'key monitor closed before release notification' "${EPOCHREALTIME/./}"
    exit 1
fi
if [[ $result != RELEASED || ! $release_observed_us =~ ^[0-9]+$ ]]; then
    timing_at "key monitor failed: $result $release_observed_us" "${EPOCHREALTIME/./}"
    exit 1
fi
watch_release_received=1
timing_at 'key release observed by monitor' "$release_observed_us"
span 'release notification received by Bash' "$release_observed_us" "${EPOCHREALTIME/./}"
