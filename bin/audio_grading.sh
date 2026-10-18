#!/usr/bin/env bash
set -euo pipefail

############################################################
# USAGE
#   ./rank_audio.sh <directory>
############################################################

dir="${1:-}"
[[ -d "$dir" ]] || { echo "Usage: $0 <dir>"; exit 1; }

log="$dir/analysis.log"
csv="$dir/results.csv"
average_csv="$dir/average_results.csv"
ranking="$dir/ranking.txt"

# mirror ALL output → console + log
exec > >(tee -a "$log") 2>&1

echo "Starting analysis: $(date)"
echo "file,profile,rms,peak,noise,dr,dc,duration,score" > "$csv"
echo "file,raw_score,normalized_score,stt_score,average_score,duration" > "$average_csv"

############################################################
# METRICS
############################################################

analyze_file() {
  local file="$1"
  local profile="$2"
  local filter_chain="astats=metadata=1:reset=0"
  local ffmpeg_args=(-nostdin -i "$file")

  case "$profile" in
    raw)
      ;;
    normalized)
      filter_chain="loudnorm=I=-23:TP=-1.5:LRA=11,${filter_chain}"
      ffmpeg_args+=(-ar 16000 -ac 1)
      ;;
    stt)
      filter_chain="dynaudnorm=f=150:g=15:p=0.9,${filter_chain}"
      ffmpeg_args+=(-ar 16000 -ac 1)
      ;;
    *)
      echo "Unknown profile: $profile" >&2
      return 1
      ;;
  esac

  ffmpeg "${ffmpeg_args[@]}" \
    -af "$filter_chain" \
    -f null - 2>&1 | awk -F': ' '
      /Channel: / {
        section = "channel"
        next
      }
      /Overall$/ {
        section = "overall"
        next
      }
      /RMS level dB/ && section == "overall" {rms=$2}
      /Peak level dB/ && section == "overall" {peak=$2}
      /Noise floor dB/ && section == "overall" {noise=$2}
      /DC offset/ && section == "overall" {dc=$2}
      /Dynamic range/ && section == "channel" {
        dr_sum += $2
        dr_count += 1
      }
      END {
        dr = ""
        if (dr_count > 0) {
          dr = sprintf("%.6f", dr_sum / dr_count)
        }
        printf "%s,%s,%s,%s,%s\n", rms, peak, noise, dr, dc
      }'
}



duration_check() {
  ffprobe -v error -show_entries format=duration \
    -of default=noprint_wrappers=1:nokey=1 "$1" 2>/dev/null || echo "0"
}

score_file() {
  local rms="$1"
  local noise="$2"
  local dr="$3"

  awk -v rms="$rms" -v noise="$noise" -v dr="$dr" '
    function is_finite(value) {
      return value != "" && value != "nan" && value != "-nan" && value != "inf" && value != "-inf"
    }
    BEGIN {
      score = 0
      used = 0

      if (is_finite(rms)) {
        score += (rms * -1)
        used += 1
      }
      if (is_finite(noise)) {
        score += (noise * -1)
        used += 1
      }
      if (is_finite(dr)) {
        score += dr
        used += 1
      }

      if (used == 0) {
        print "nan"
        exit
      }

      printf "%.2f\n", score
    }'
}

analyze_profile() {
  local file="$1"
  local profile="$2"
  local duration="$3"

  local metrics
  metrics="$(analyze_file "$file" "$profile")" || {
    echo "Failed [$profile]: $file"
    return 1
  }

  local rms peak noise dr dc score line
  IFS=',' read -r rms peak noise dr dc <<< "$metrics"
  score="$(score_file "$rms" "$noise" "$dr")"
  line="$file,$profile,$rms,$peak,$noise,$dr,$dc,$duration,$score"

  echo "[$profile] RMS=$rms PEAK=$peak NOISE=$noise DR=$dr DC=$dc DUR=$duration SCORE=$score"
  echo "$line" >> "$csv"
  echo "$line"
}

average_scores() {
  local raw_score="$1"
  local normalized_score="$2"
  local stt_score="$3"

  awk -v raw_score="$raw_score" -v normalized_score="$normalized_score" -v stt_score="$stt_score" '
    function is_finite(value) {
      return value != "" && value != "nan" && value != "-nan" && value != "inf" && value != "-inf"
    }
    BEGIN {
      total = 0
      used = 0

      if (is_finite(raw_score)) {
        total += raw_score
        used += 1
      }
      if (is_finite(normalized_score)) {
        total += normalized_score
        used += 1
      }
      if (is_finite(stt_score)) {
        total += stt_score
        used += 1
      }

      if (used == 0) {
        print "nan"
        exit
      }

      printf "%.2f\n", total / used
    }'
}

############################################################
# PROCESS FILES
############################################################

while IFS= read -r -d '' file; do
  echo
  echo "Processing: $file"

  raw_score=""
  normalized_score=""
  stt_score=""
  duration="$(duration_check "$file")"

  analyze_profile "$file" raw "$duration"
  raw_score="$(tail -n 1 "$csv" | awk -F, '{print $9}')"

  analyze_profile "$file" normalized "$duration"
  normalized_score="$(tail -n 1 "$csv" | awk -F, '{print $9}')"

  analyze_profile "$file" stt "$duration"
  stt_score="$(tail -n 1 "$csv" | awk -F, '{print $9}')"

  average_score="$(average_scores "$raw_score" "$normalized_score" "$stt_score")"
  average_line="$file,$raw_score,$normalized_score,$stt_score,$average_score,$duration"

  echo "[average] RAW=$raw_score NORMALIZED=$normalized_score STT=$stt_score AVG=$average_score DUR=$duration"
  echo "$average_line" >> "$average_csv"
  echo "$average_line"

done < <(find "$dir" -type f \( -iname "*.wav" -o -iname "*.flac" -o -iname "*.mp3" \) -print0)

############################################################
# RANKING
############################################################

echo
echo "Generating ranking..."

{
  head -n 1 "$average_csv"
  tail -n +2 "$average_csv" | sort -t, -k5 -nr
} > "$ranking"

echo
echo "==== TOP RESULTS ===="
column -t -s, "$ranking" | head -n 15

echo
echo "Done."
echo "Log: $log"
echo "CSV: $csv"
echo "Average CSV: $average_csv"
echo "Ranking: $ranking"