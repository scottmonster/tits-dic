#!/usr/bin/env bash

set -euo pipefail



# PULSE AUDIO IS NOT ALLOWED IN THIS FILE!!! MUST USE ffmpeg -f alsa -i kiyo



devices=(
"NAME=kiyo \
ALSA_INPUT=kiyo \
FMT=s16le \
RATE=48000 \
CHANNELS=2 \
SAMPLE_FMT=s16"

"NAME=kitty \
ALSA_INPUT=kitty \
FMT=s16le \
RATE=48000 \
CHANNELS=2 \
SAMPLE_FMT=s16"

"NAME=wh \
ALSA_INPUT= \
FMT=float32le \
RATE=48000 \
CHANNELS=1 \
SAMPLE_FMT="
)

DEFAULT_DEVICE="kiyo"
DEFAULT_DURATION=""
DEFAULT_OUTPUT_FILE="record_out.wav"

usage() {
	cat <<'EOF'
Usage: record.sh [-d device] [-t seconds] [output_file]

Options:
	-d, --device   Input device name. Default: kiyo
	-t, --time     Recording duration in seconds. If omitted, record until canceled
  -h, --help     Show this help

Devices:
  kiyo
  kitty
  wh

Example:
	record.sh -t 30 sample.wav
	record.sh sample.wav
	record.sh
EOF
}

device_name="$DEFAULT_DEVICE"
duration="$DEFAULT_DURATION"
output_file="$DEFAULT_OUTPUT_FILE"

while [[ $# -gt 0 ]]; do
	case "$1" in
		-d|--device)
			if [[ $# -lt 2 ]]; then
				echo "Missing value for $1" >&2
				usage >&2
				exit 1
			fi
			device_name="$2"
			shift 2
			;;
		-t|--time)
			if [[ $# -lt 2 ]]; then
				echo "Missing value for $1" >&2
				usage >&2
				exit 1
			fi
			duration="$2"
			shift 2
			;;
		-h|--help)
			usage
			exit 0
			;;
		--)
			shift
			break
			;;
		-*)
			echo "Unknown option: $1" >&2
			usage >&2
			exit 1
			;;
		*)
			if [[ -n "$output_file" ]]; then
				echo "Only one output file may be provided" >&2
				usage >&2
				exit 1
			fi
			output_file="$1"
			shift
			;;
	esac
done

if [[ -z "$output_file" && $# -gt 0 ]]; then
	output_file="$1"
	shift
fi

if [[ $# -gt 0 ]]; then
	echo "Unexpected arguments: $*" >&2
	usage >&2
	exit 1
fi

if [[ -n "$duration" ]] && ! [[ "$duration" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
	echo "Duration must be a positive number: $duration" >&2
	exit 1
fi

selected_device=""
for device in "${devices[@]}"; do
	eval "$device"
	if [[ "$NAME" == "$device_name" ]]; then
		selected_device="$device"
		break
	fi
done

if [[ -z "$selected_device" ]]; then
	echo "Unknown device: $device_name" >&2
	usage >&2
	exit 1
fi

eval "$selected_device"

if [[ -z "$ALSA_INPUT" ]]; then
	echo "Selected device does not define an ALSA input: $device_name" >&2
	exit 1
fi

output_dir=$(dirname "$output_file")
if [[ "$output_dir" != "." ]]; then
	mkdir -p "$output_dir"
fi

ffmpeg_args=(
	-f alsa
	-i "$ALSA_INPUT"
)

if [[ -n "$duration" ]]; then
	ffmpeg_args+=( -t "$duration" )
fi

if [[ -n "$CHANNELS" ]]; then
	ffmpeg_args+=( -ac "$CHANNELS" )
fi

if [[ -n "$RATE" ]]; then
	ffmpeg_args+=( -ar "$RATE" )
fi

if [[ -n "$SAMPLE_FMT" ]]; then
	ffmpeg_args+=( -sample_fmt "$SAMPLE_FMT" )
fi

ffmpeg_args+=( -y "$output_file" )

if [[ -n "$duration" ]]; then
	echo "Recording for $duration seconds. Press q to stop early."
else
	echo "Recording until canceled."
fi

exec ffmpeg "${ffmpeg_args[@]}"



