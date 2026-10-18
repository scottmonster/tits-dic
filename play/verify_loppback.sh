#!/usr/bin/env bash
set -euo pipefail

TIME=5
file="/home/scott/Documents/tits-dic/tst/short.wav"
RATE=16000
CHANNELS=1
WRITE_DEVICE="plughw:Loopback,0,0"
READ_DEVICE="plughw:Loopback,1,0"

cleanup() {
  pkill -f speaker-test 2>/dev/null || true
  pkill -f arecord 2>/dev/null || true
  pkill -f aplay 2>/dev/null || true
  pkill -f ffmpeg 2>/dev/null || true
  sleep 1
}

start_test() {
  setsid "$@" &
  LAST_PID=$!

  (
    sleep "$TIME"
    kill -TERM -- -"$LAST_PID" 2>/dev/null || true
  ) &
}

trap cleanup EXIT

wait_for_test() {
  wait "$1" 2>/dev/null || true
}

echo "=== TEST 1: speaker-test loopback ==="
cleanup
(
  start_test speaker-test -t sine -f 440 -r "$RATE" -c "$CHANNELS" -D "$WRITE_DEVICE"
  writer=$LAST_PID

  start_test bash -c "
    arecord -D '$READ_DEVICE' -f S16_LE -r '$RATE' -c '$CHANNELS' | \
      aplay -q -f S16_LE -r '$RATE' -c '$CHANNELS'
  "
  reader=$LAST_PID

  wait_for_test "$writer"
  kill -TERM -"$reader" 2>/dev/null || true
  wait_for_test "$reader"
)
sleep 1

echo "=== TEST 2: ffmpeg sine loopback ==="
cleanup
(
  start_test ffmpeg -hide_banner -loglevel error -nostats \
    -re -f lavfi -i "sine=frequency=440:sample_rate=$RATE" \
    -ac "$CHANNELS" -ar "$RATE" -f alsa "$WRITE_DEVICE"
  writer=$LAST_PID

  start_test bash -c "
    ffmpeg -hide_banner -loglevel error -nostats \
      -f alsa -ar '$RATE' -ac '$CHANNELS' -i '$READ_DEVICE' -f wav - | aplay -q
  "
  reader=$LAST_PID

  wait_for_test "$writer"
  kill -TERM -"$reader" 2>/dev/null || true
  wait_for_test "$reader"
)
sleep 1

echo "=== TEST 3: file -> loopback -> play ==="
cleanup
(
  start_test ffmpeg -hide_banner -loglevel error -nostats \
    -re -i "$file" \
    -ac "$CHANNELS" -ar "$RATE" -sample_fmt s16 \
    -f alsa "$WRITE_DEVICE"
  writer=$LAST_PID

  start_test bash -c "
    ffmpeg -hide_banner -loglevel error -nostats \
      -f alsa -ar '$RATE' -ac '$CHANNELS' -i '$READ_DEVICE' -f wav - | aplay -q
  "
  reader=$LAST_PID

  wait_for_test "$writer"
  kill -TERM -"$reader" 2>/dev/null || true
  wait_for_test "$reader"
)