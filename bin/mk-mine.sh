#!/usr/bin/env bash

set -euo pipefail

countdown=3
force_new=0
tty_input="/dev/tty"
TEXT_WRAP_WIDTH=100

KIYO_DEVICE="kiyo"
KIYO_RATE="48000"
KIYO_CHANNELS="2"
KIYO_SAMPLE_FMT="s16"





usage() {
	cat <<'EOF'
Usage: mk-mine.sh [-f|--force-new]

Options:
	-f, --force-new   Record a new orig.wav even if one already exists
	-h, --help        Show this help
EOF
}

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
while [[ $# -gt 0 ]]; do
	case "$1" in
		-f|--force-new)
			force_new=1
			shift
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			echo "Unknown option: $1" >&2
			usage >&2
			exit 1
			;;
	esac
done

repo_root=$(cd -- "$script_dir/.." && pwd)
text_root="$repo_root/data/mine/text"
audio_root="$repo_root/data/mine"
lengths=(short medium long)
active_pid=""
record_retry_status=10

if [[ ! -r "$tty_input" ]]; then
	echo "mk-mine.sh requires an interactive terminal" >&2
	exit 1
fi

cleanup_active_recording() {
	if [[ -n "$active_pid" ]] && kill -0 "$active_pid" 2>/dev/null; then
		kill -INT "$active_pid" 2>/dev/null || true
		wait "$active_pid" || true
	fi
	active_pid=""
}

trap cleanup_active_recording EXIT INT TERM

print_prompt_text() {
	local text_file="$1"

	if [[ "$TEXT_WRAP_WIDTH" -gt 0 ]]; then
		fold -s -w "$TEXT_WRAP_WIDTH" "$text_file"
	else
		cat "$text_file"
	fi
}

countdown_run() {
	local remaining
	for ((remaining = countdown; remaining >= 1; remaining--)); do
		printf '\rStarting in %s... ' "$remaining"
		sleep 1
	done
	printf '\n'
}

wait_for_start() {
	local reply
	while true; do
		read -r -n 1 -p "Press s to start, q to quit: " reply < "$tty_input"
		echo
		case "$reply" in
			s|S)
				return 0
				;;
			q|Q)
				echo "Stopping."
				exit 0
				;;
		esac
	done
}




record_kiyo() {
	local output_file="$1"
	local reply
	local stop_requested=0
	local retry_requested=0
	local ffmpeg_status=0

	ffmpeg \
		-hide_banner \
		-loglevel error \
		-nostats \
		-f alsa \
		-i "$KIYO_DEVICE" \
		-ac "$KIYO_CHANNELS" \
		-ar "$KIYO_RATE" \
		-sample_fmt "$KIYO_SAMPLE_FMT" \
		-y \
		"$output_file" \
		< /dev/null &
	active_pid=$!

	echo "Recording started: $output_file"
	echo "Press s to stop, r to retry."

	while kill -0 "$active_pid" 2>/dev/null; do
		if read -r -n 1 -t 0.2 reply < "$tty_input"; then
			echo
			case "$reply" in
				s|S)
					stop_requested=1
					kill -INT "$active_pid"
					break
					;;
				r|R)
					stop_requested=1
					retry_requested=1
					kill -INT "$active_pid"
					break
					;;
			q|Q)
					stop_requested=1
					kill -INT "$active_pid"
					wait "$active_pid" || true
					active_pid=""
					echo "Stopping."
					exit 0
					;;
			esac
		fi
	done

	if [[ -n "$active_pid" ]]; then
		if ! wait "$active_pid"; then
			ffmpeg_status=$?
			if [[ "$stop_requested" -eq 0 ]]; then
				active_pid=""
				return "$ffmpeg_status"
			fi
		fi
		active_pid=""
	fi

	if [[ "$retry_requested" -eq 1 ]]; then
		rm -f -- "$output_file"
		echo "Discarded recording."
		return "$record_retry_status"
	fi
}

for length in "${lengths[@]}"; do
	length_dir="$text_root/$length"
	if [[ ! -d "$length_dir" ]]; then
		continue
	fi

	while IFS= read -r -d '' text_file; do
		base_name=$(basename "$text_file" .txt)
		output_dir="$audio_root/$length/$base_name"
		output_file="$output_dir/orig.wav"

		if [[ -f "$output_file" && "$force_new" -eq 0 ]]; then
			echo "Skipping $length/$base_name: $output_file already exists"
			continue
		fi

		mkdir -p "$output_dir"

		while true; do
			echo
			echo "=== $length/$base_name ==="
			print_prompt_text "$text_file"
			echo

			wait_for_start
			countdown_run

			record_status=0
			record_kiyo "$output_file" || record_status=$?
			if [[ "$record_status" -eq 0 ]]; then
				break
			fi

			if [[ "$record_status" -eq "$record_retry_status" ]]; then
				continue
			fi

			exit "$record_status"
		done
	done < <(find "$length_dir" -maxdepth 1 -type f -name '*.txt' -print0 | sort -z)
done





