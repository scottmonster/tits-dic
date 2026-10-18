#!/usr/bin/env bash

set -euo pipefail

declare -a device_keys=()
declare -A device_index_by_key=()
declare -A display_name=()
declare -A descriptions=()
declare -A alsa_hw_paths=()
declare -A alsa_api_paths=()
declare -A pulse_names=()
declare -A card_names=()
declare -A alsa_names=()
declare -A bit_depths=()
declare -A sample_rates=()
declare -A channels=()
declare -A formats=()
declare -A drivers=()
declare -A source_backends=()

trim() {
	local value="${1-}"
	value="${value#${value%%[![:space:]]*}}"
	value="${value%${value##*[![:space:]]}}"
	printf '%s' "$value"
}

normalize_list() {
	local value
	value=$(trim "${1-}")
	if [[ -z "$value" ]]; then
		return
	fi
	value=${value#[}
	value=${value%]}
	value=${value#(}
	value=${value%)}
	printf '%s' "$value" | tr -s ' ' '\n' | sed '/^$/d' | awk 'BEGIN { first = 1 } { if (!first) printf ", "; printf "%s", $0; first = 0 }'
}

normalize_range_or_list() {
	local value
	value=$(trim "${1-}")
	if [[ -z "$value" ]]; then
		return
	fi
	if [[ "$value" =~ ^\[([^[:space:]]+)[[:space:]]+([^[:space:]]+)\]$ ]]; then
		printf '%s-%s' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
		return
	fi
	if [[ "$value" =~ ^\(([^[:space:]]+)[[:space:]]+([^[:space:]]+)\)$ ]]; then
		printf '%s-%s' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
		return
	fi
	normalize_list "$value"
}

merge_value() {
	local -n target_array="$1"
	local key="$2"
	local value
	value=$(trim "${3-}")
	if [[ -z "$value" ]]; then
		return
	fi
	if [[ -z "${target_array[$key]:-}" ]]; then
		target_array[$key]="$value"
		return
	fi
	if [[ ", ${target_array[$key]}, " == *", ${value}, "* ]]; then
		return
	fi
	target_array[$key]="${target_array[$key]}, ${value}"
}

register_device() {
	local key="$1"
	local name
	name=$(trim "${2-}")
	if [[ -z "${device_index_by_key[$key]+x}" ]]; then
		device_index_by_key[$key]="${#device_keys[@]}"
		device_keys+=("$key")
	fi
	if [[ -n "$name" && -z "${display_name[$key]:-}" ]]; then
		display_name[$key]="$name"
	fi
}

format_to_bit_depth() {
	local format="$1"
	case "$format" in
		FLOAT64*|FLOAT64_*) printf '64 float' ;;
		FLOAT*|FLOAT_*) printf '32 float' ;;
		S*|U*)
			if [[ "$format" =~ ([0-9]+) ]]; then
				printf '%s' "${BASH_REMATCH[1]}"
			fi
			;;
		*) ;;
	esac
}

set_bits_from_formats_if_missing() {
	local key="$1"
	local raw="$2"
	local token bit
	if [[ -n "${bit_depths[$key]:-}" ]]; then
		return
	fi
	for token in $raw; do
		bit=$(format_to_bit_depth "$token")
		if [[ -n "$bit" ]]; then
			merge_value bit_depths "$key" "$bit"
		fi
	done
}

inspect_alsa_hw() {
	local key="$1"
	local hw_path="$2"
	local hw_output line value

	hw_output=$(arecord -D "$hw_path" --dump-hw-params -d 1 -f S16_LE -r 48000 -c 2 /dev/null 2>&1 || true)
	while IFS= read -r line; do
		case "$line" in
			FORMAT:*)
				value=$(trim "${line#FORMAT:}")
				formats[$key]=$(normalize_list "$value")
				set_bits_from_formats_if_missing "$key" "$value"
				;;
			SAMPLE_BITS:*)
				bit_depths[$key]=$(normalize_range_or_list "${line#SAMPLE_BITS:}")
				;;
			RATE:*)
				sample_rates[$key]=$(normalize_range_or_list "${line#RATE:}")
				;;
			CHANNELS:*)
				channels[$key]=$(normalize_range_or_list "${line#CHANNELS:}")
				;;
		esac
	done <<< "$hw_output"
}

collect_alsa_devices() {
	local line card_id card_label card_desc device_id device_label device_desc key name hw_path

	if ! command -v arecord >/dev/null 2>&1; then
		return
	fi

	while IFS= read -r line; do
		if [[ "$line" =~ ^card[[:space:]]+([0-9]+):[[:space:]]+([^[]+)[[:space:]]+\[(.*)\],[[:space:]]+device[[:space:]]+([0-9]+):[[:space:]]+([^[]+)[[:space:]]+\[(.*)\]$ ]]; then
			card_id="${BASH_REMATCH[1]}"
			card_label=$(trim "${BASH_REMATCH[2]}")
			card_desc=$(trim "${BASH_REMATCH[3]}")
			device_id="${BASH_REMATCH[4]}"
			device_label=$(trim "${BASH_REMATCH[5]}")
			device_desc=$(trim "${BASH_REMATCH[6]}")
			key="alsa:${card_id}:${device_id}"
			name="$device_desc"
			if [[ -z "$name" ]]; then
				name="$card_desc"
			fi
			register_device "$key" "$name"
			descriptions[$key]="$card_desc / $device_desc"
			card_names[$key]="$card_label"
			alsa_names[$key]="$device_label"
			hw_path="hw:${card_id},${device_id}"
			alsa_hw_paths[$key]="$hw_path"
			merge_value source_backends "$key" "ALSA"
			inspect_alsa_hw "$key" "$hw_path"
		fi
	done < <(arecord --list-devices 2>/dev/null || true)
}

finalize_pulse_source() {
	local pulse_name="$1"
	local pulse_desc="$2"
	local pulse_driver="$3"
	local pulse_spec="$4"
	local pulse_monitor="$5"
	local pulse_card="$6"
	local pulse_device="$7"
	local pulse_api_path="$8"
	local pulse_card_name="$9"
	local pulse_alsa_name="${10}"
	local pulse_resolution_bits="${11}"
	local key name fmt rate channel_count

	if [[ -z "$pulse_name" || "$pulse_monitor" != "n/a" ]]; then
		return
	fi

	if [[ -n "$pulse_card" && -n "$pulse_device" ]]; then
		key="alsa:${pulse_card}:${pulse_device}"
	else
		key="pulse:${pulse_name}"
	fi

	name="$pulse_desc"
	if [[ -z "$name" ]]; then
		name="$pulse_name"
	fi

	register_device "$key" "$name"
	merge_value source_backends "$key" "Pulse/PipeWire"
	merge_value drivers "$key" "$pulse_driver"
	pulse_names[$key]="$pulse_name"
	if [[ -n "$pulse_desc" ]]; then
		descriptions[$key]="$pulse_desc"
	fi
	if [[ -n "$pulse_api_path" ]]; then
		alsa_api_paths[$key]="$pulse_api_path"
	fi
	if [[ -n "$pulse_card_name" ]]; then
		card_names[$key]="$pulse_card_name"
	fi
	if [[ -n "$pulse_alsa_name" ]]; then
		alsa_names[$key]="$pulse_alsa_name"
	fi
	if [[ -n "$pulse_resolution_bits" && -z "${bit_depths[$key]:-}" ]]; then
		bit_depths[$key]="$pulse_resolution_bits"
	fi
	if [[ -z "${alsa_hw_paths[$key]:-}" && -n "$pulse_card" && -n "$pulse_device" ]]; then
		alsa_hw_paths[$key]="hw:${pulse_card},${pulse_device}"
	fi

	if [[ "$pulse_spec" =~ ^([^[:space:]]+)[[:space:]]+([0-9]+)ch[[:space:]]+([0-9]+)Hz$ ]]; then
		fmt="${BASH_REMATCH[1]}"
		channel_count="${BASH_REMATCH[2]}"
		rate="${BASH_REMATCH[3]}"
		if [[ -z "${formats[$key]:-}" ]]; then
			formats[$key]="$fmt"
		fi
		if [[ -z "${channels[$key]:-}" ]]; then
			channels[$key]="$channel_count"
		fi
		if [[ -z "${sample_rates[$key]:-}" ]]; then
			sample_rates[$key]="$rate"
		fi
		if [[ -z "${bit_depths[$key]:-}" ]]; then
			bit_depths[$key]="$(format_to_bit_depth "$fmt")"
		fi
	fi
}

collect_pulse_sources() {
	local line pulse_name="" pulse_desc="" pulse_driver="" pulse_spec="" pulse_monitor="n/a"
	local pulse_card="" pulse_device="" pulse_api_path="" pulse_card_name="" pulse_alsa_name="" pulse_resolution_bits=""

	if ! command -v pactl >/dev/null 2>&1; then
		return
	fi

	while IFS= read -r line; do
		if [[ "$line" =~ ^Source[[:space:]]+# ]]; then
			finalize_pulse_source "$pulse_name" "$pulse_desc" "$pulse_driver" "$pulse_spec" "$pulse_monitor" "$pulse_card" "$pulse_device" "$pulse_api_path" "$pulse_card_name" "$pulse_alsa_name" "$pulse_resolution_bits"
			pulse_name=""
			pulse_desc=""
			pulse_driver=""
			pulse_spec=""
			pulse_monitor="n/a"
			pulse_card=""
			pulse_device=""
			pulse_api_path=""
			pulse_card_name=""
			pulse_alsa_name=""
			pulse_resolution_bits=""
			continue
		fi

		if [[ "$line" =~ ^[[:space:]]+Name:[[:space:]]+(.*)$ ]]; then
			pulse_name=$(trim "${BASH_REMATCH[1]}")
		elif [[ "$line" =~ ^[[:space:]]+Description:[[:space:]]+(.*)$ ]]; then
			pulse_desc=$(trim "${BASH_REMATCH[1]}")
		elif [[ "$line" =~ ^[[:space:]]+Driver:[[:space:]]+(.*)$ ]]; then
			pulse_driver=$(trim "${BASH_REMATCH[1]}")
		elif [[ "$line" =~ ^[[:space:]]+Sample[[:space:]]+Specification:[[:space:]]+(.*)$ ]]; then
			pulse_spec=$(trim "${BASH_REMATCH[1]}")
		elif [[ "$line" =~ ^[[:space:]]+Monitor[[:space:]]+of[[:space:]]+Sink:[[:space:]]+(.*)$ ]]; then
			pulse_monitor=$(trim "${BASH_REMATCH[1]}")
		elif [[ "$line" =~ ^[[:space:]]+alsa\.card[[:space:]]*=[[:space:]]*\"([^\"]*)\"$ ]]; then
			pulse_card=$(trim "${BASH_REMATCH[1]}")
		elif [[ "$line" =~ ^[[:space:]]+alsa\.device[[:space:]]*=[[:space:]]*\"([^\"]*)\"$ ]]; then
			pulse_device=$(trim "${BASH_REMATCH[1]}")
		elif [[ "$line" =~ ^[[:space:]]+api\.alsa\.path[[:space:]]*=[[:space:]]*\"([^\"]*)\"$ ]]; then
			pulse_api_path=$(trim "${BASH_REMATCH[1]}")
		elif [[ "$line" =~ ^[[:space:]]+alsa\.card_name[[:space:]]*=[[:space:]]*\"([^\"]*)\"$ ]]; then
			pulse_card_name=$(trim "${BASH_REMATCH[1]}")
		elif [[ "$line" =~ ^[[:space:]]+alsa\.name[[:space:]]*=[[:space:]]*\"([^\"]*)\"$ ]]; then
			pulse_alsa_name=$(trim "${BASH_REMATCH[1]}")
		elif [[ "$line" =~ ^[[:space:]]+alsa\.resolution_bits[[:space:]]*=[[:space:]]*\"([^\"]*)\"$ ]]; then
			pulse_resolution_bits=$(trim "${BASH_REMATCH[1]}")
		fi
	done < <(pactl list sources 2>/dev/null || true)

	finalize_pulse_source "$pulse_name" "$pulse_desc" "$pulse_driver" "$pulse_spec" "$pulse_monitor" "$pulse_card" "$pulse_device" "$pulse_api_path" "$pulse_card_name" "$pulse_alsa_name" "$pulse_resolution_bits"
}

print_menu() {
	local index key label
	printf 'Detected microphones:\n\n'
	for index in "${!device_keys[@]}"; do
		key="${device_keys[$index]}"
		label="${display_name[$key]:-${pulse_names[$key]:-${alsa_hw_paths[$key]:-$key}}}"
		printf '%2d) %s\n' "$((index + 1))" "$label"
	done
	printf '\n'
}

print_device_info() {
	local key="$1"
	printf 'Name: %s\n' "${display_name[$key]:-Unknown}"
	printf 'Description: %s\n' "${descriptions[$key]:-Unknown}"
	printf 'Source backends: %s\n' "${source_backends[$key]:-Unknown}"
	printf 'Driver: %s\n' "${drivers[$key]:-Unknown}"
	printf 'Card name: %s\n' "${card_names[$key]:-Unknown}"
	printf 'ALSA device name: %s\n' "${alsa_names[$key]:-Unknown}"
	printf 'ALSA hw path: %s\n' "${alsa_hw_paths[$key]:-Unknown}"
	printf 'ALSA path: %s\n' "${alsa_api_paths[$key]:-${alsa_hw_paths[$key]:-Unknown}}"
	printf 'Pulse name: %s\n' "${pulse_names[$key]:-Unavailable}"
	printf 'Bit depth available: %s\n' "${bit_depths[$key]:-Unknown}"
	printf 'Sample rates available: %s\n' "${sample_rates[$key]:-Unknown}"
	printf 'Channels available: %s\n' "${channels[$key]:-Unknown}"
	printf 'Formats available: %s\n' "${formats[$key]:-Unknown}"
}

main() {
	local selection key

	collect_alsa_devices
	collect_pulse_sources

	if [[ "${#device_keys[@]}" -eq 0 ]]; then
		echo 'No microphones were detected from ALSA or Pulse/PipeWire.' >&2
		exit 1
	fi

	print_menu
	while true; do
		read -r -p 'Select a microphone by number: ' selection
		if [[ "$selection" =~ ^[0-9]+$ ]] && (( selection >= 1 && selection <= ${#device_keys[@]} )); then
			break
		fi
		echo 'Invalid selection.' >&2
	done

	key="${device_keys[$((selection - 1))]}"
	echo
	print_device_info "$key"
}

main "$@"
