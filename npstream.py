#!/usr/bin/env python3

from datetime import datetime
from collections import deque
from pathlib import Path
import argparse
import json
import math
import queue
import re
import subprocess
import sys
import threading
import time
import diagnostics as diag
import user_feedback

import numpy as np
import webrtcvad
from asr_backend import DEFAULT_MODEL, load_model as Model
import ffmpeg

# ------------------------
# CONFIG
# ------------------------
ROOT = Path(__file__).resolve().parent
MODEL_PATH = DEFAULT_MODEL
ACTIVE_MODEL_PATH = MODEL_PATH
RNNOISE_MODEL = str(ROOT / "models/rnnoise/voice-recording.rnnn")

USE_TEST_FILE = True
TEST_AUDIO_FILE = str(ROOT / "tst/data/short.wav")
# TEST_AUDIO_FILE = "/home/scott/Documents/tits-dic/tst/data/002SHORT.wav"
# TEST_AUDIO_FILE = "/home/scott/Documents/tits-dic/tst/data/0000WEIRD.wav"
TYPE_OUTPUT = False
USE_FILTER = False
ASR_PROFILE = "balanced"
PAUSE_ENDPOINT_MS = 300

SOUND_DEVICE_ID = None

WAIT_AUDIO_COMPLETE = True

TARGET_SR = 16000
CHANNELS = 1

VAD_MODE = 2  # 0-3, 3 = most aggressive
FRAME_MS = 30  # WebRTC VAD supports 10, 20, or 30 ms

END_SILENCE_MS = 700  # stop utterance after this much silence
START_PADDING_MS = 150
MAX_UTTERANCE_SECONDS = 99
VAD_TARGET_RMS = 0.02
VAD_MAX_GAIN = 100.0

# ------------------------
# LOAD MODEL ONCE
# ------------------------
model: Model

# ------------------------
# VAD
# ------------------------
vad = webrtcvad.Vad(VAD_MODE)

FRAME_SAMPLES = int(TARGET_SR * FRAME_MS / 1000)
FRAME_BYTES_F32 = FRAME_SAMPLES * 4

END_SILENCE_FRAMES = max(1, END_SILENCE_MS // FRAME_MS)
START_PADDING_FRAMES = max(1, START_PADDING_MS // FRAME_MS)
MAX_UTTERANCE_FRAMES = int(MAX_UTTERANCE_SECONDS * 1000 / FRAME_MS)


# ------------------------
# TIMING METRICS
# ------------------------
class Metrics:

  def __init__(self):
    self.t0 = time.perf_counter()
    self.wall_start = datetime.now()
    self.events = {}
    self.inference_calls = 0
    self.inference_seconds = 0.0
    self.input_error = None
    self.input_samples = 0
    self.kept_samples = 0
    self.speech_frames = 0
    self.event_logger = diag.event
    self.capture_status_counts = {}
    self.queue_peak = 0
    self.last_health_at = 0

  def emit(self, event, **fields):
    if self.event_logger is not None:
      self.event_logger(event, **fields)

  def health_tick(self, force=False):
    now = time.perf_counter()
    depth = audio_q.qsize()
    self.queue_peak = max(self.queue_peak, depth)
    if force or now - self.last_health_at >= 1:
      self.last_health_at = now
      self.emit("capture_health", input_seconds=self.input_samples / TARGET_SR,
                queue_frames=depth, queue_peak_frames=self.queue_peak,
                speech_frames=self.speech_frames, capture_status_counts=dict(self.capture_status_counts))

  def mark(self, name):
    self.events[name] = time.perf_counter()

  def mark_once(self, name):
    if name not in self.events:
      self.mark(name)

  def has(self, name):
    return name in self.events

  def delta(self, start_name, end_name):
    return self.events[end_name] - self.events[start_name]

  def print_delta(self, label, start_name, end_name):
    if self.has(start_name) and self.has(end_name):
      print(f"{label}: {self.delta(start_name, end_name):.3f}s")
    else:
      missing = [name for name in (start_name, end_name) if not self.has(name)]
      print(f"{label}: n/a, missing {', '.join(missing)}")

  def report(self):
    print("\n=== TIMING METRICS ===")
    print(
      f"Start wall time: {self.wall_start.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}"
    )
    print(f"Inference calls: {self.inference_calls}")
    print(f"Total inference time: {self.inference_seconds:.3f}s")
    self.print_delta("Audio end to text ready", "input_last_frame", "text_ready")

    self.print_delta(
      "Time to load model",
      "model_load_start",
      "model_load_end",
    )

    self.print_delta(
      "Time to start listening",
      "program_start",
      "listening_start",
    )

    self.print_delta(
      "Time from start listening to first raw input frame",
      "listening_start",
      "input_first_frame",
    )

    self.print_delta(
      "Time from first raw input frame to first filtered output frame",
      "input_first_frame",
      "filtered_first_frame",
    )

    self.print_delta(
      "Input feed duration",
      "input_first_frame",
      "input_last_frame",
    )

    self.print_delta(
      "Time from first filtered output frame to last filtered output frame",
      "filtered_first_frame",
      "filtered_last_frame",
    )

    self.print_delta(
      "Time from first filtered output frame to first VAD-positive frame",
      "filtered_first_frame",
      "vad_first_speech_frame",
    )

    self.print_delta(
      "Time from utterance end to inference start",
      "utterance_end",
      "inference_start",
    )

    self.print_delta(
      "Time from inference start to inference end",
      "inference_start",
      "inference_end",
    )

    self.print_delta(
      "Audio end to typed text complete",
      "input_last_frame",
      "typing_end",
    )
    self.print_delta(
      "Audio end to start typing",
      "input_last_frame",
      "typing_start",
    )

    self.print_delta(
      "Filter end to typed text complete",
      "filtered_last_frame",
      "typing_end",
    )

    self.print_delta(
      "Utterance end to typed text complete",
      "utterance_end",
      "typing_end",
    )

    self.print_delta(
      "Inference end to typed text complete",
      "inference_end",
      "typing_end",
    )

    self.print_delta(
      "Time spent typing",
      "typing_start",
      "typing_end",
    )

    self.print_delta(
      "Total program time",
      "program_start",
      "program_done",
    )


# ------------------------
# AUDIO HELPERS
# ------------------------
def normalize(audio):
  if not len(audio):
    return np.array([], dtype=np.float32)
  return audio / (np.max(np.abs(audio)) + 1e-6)


def noise_gate(audio, threshold = 0.01):
  return np.where(np.abs(audio) < threshold, 0, audio)


def is_speech_frame_f32(frame_f32):
  rms = float(np.sqrt(np.mean(frame_f32 ** 2)))
  if rms < 1e-6:
    return False
  # RNNoise can leave quiet speech below WebRTC's useful input level.
  # Only amplify the detector's copy; retain the original audio for Whisper.
  gain = min(VAD_MAX_GAIN, max(1.0, VAD_TARGET_RMS / rms))
  frame_f32 = frame_f32 * gain
  frame_f32 = np.clip(frame_f32, -1.0, 1.0)
  pcm16 = (frame_f32 * 32767).astype(np.int16).tobytes()
  return vad.is_speech(pcm16, TARGET_SR)


def read_exact(pipe, nbytes):
  chunks = []
  remaining = nbytes

  while remaining > 0:
    chunk = pipe.read(remaining)

    if not chunk:
      return b"".join(chunks)

    chunks.append(chunk)
    remaining -= len(chunk)

  return b"".join(chunks)


# ------------------------
# FFMPEG FILTER PROCESS
# ------------------------
def start_ffmpeg_filter():
  # Raw PCM already specifies the format. Do not use nobuffer, which discards
  # samples consumed during probing at the start of a pipe.
  proc = (ffmpeg.input("pipe:0", format="f32le", ar=TARGET_SR, ac=1)
          .filter("highpass", f=80)
          .filter("lowpass", f=7600)
          .filter("arnndn", m=RNNOISE_MODEL, mix=0.8)
          .output("pipe:1", format="f32le", ar=TARGET_SR, ac=1, loglevel="error")
          .run_async(pipe_stdin=True, pipe_stdout=True, pipe_stderr=True,
                     overwrite_output=True))

  def drain_stderr():
    for line in proc.stderr:
      sys.stderr.write("[ffmpeg] " + line.decode(errors="replace"))

  threading.Thread(target=drain_stderr, daemon=True).start()
  return proc


# ------------------------
# MIC CAPTURE
# ------------------------
audio_q = queue.Queue()
metrics_global: Metrics


def mic_callback(indata, frames, time_info, status):
  """
    - takes in data from mic_stream
    - converts it to np.float32
    - adds it to audio_q
    """

  global metrics_global

  audio = indata.copy().reshape(-1).astype(np.float32)
  enqueue_mic_audio(audio, status, metrics_global, audio_q)


def enqueue_mic_audio(audio, status, metrics, target_queue):
  """Account for a captured frame and send it to the selected session."""
  if metrics is not None:
    if status:
      warning = str(status)
      counts = metrics.capture_status_counts
      counts[warning] = counts.get(warning, 0) + 1
    metrics.mark_once("input_first_frame")
    metrics.mark("input_last_frame")
    metrics.input_samples += len(audio)

  target_queue.put(audio)


def start_mic_stream(callback=None):
  import sounddevice as sd

  return sd.InputStream(
    device = SOUND_DEVICE_ID,
    samplerate = TARGET_SR,
    channels = CHANNELS,
    dtype = "float32",
    blocksize = FRAME_SAMPLES,
    callback = mic_callback if callback is None else callback,
  )


def play_file_into_audio_q(path, realtime=True, metrics=None, stop_event=None):
  started_at = time.perf_counter()
  samples_sent = 0
  proc = None
  errors = []
  try:
    proc = subprocess.Popen(
      ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostats",
       "-i", str(path), "-ac", "1", "-ar", str(TARGET_SR),
       "-f", "f32le", "pipe:1"],
      stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
    )
    def drain_errors():
      errors.append(proc.stderr.read().decode(errors="replace"))
    stderr_thread = threading.Thread(target=drain_errors, daemon=True)
    stderr_thread.start()
    print("[+] Playing test file into pipeline:", path)
    while stop_event is None or not stop_event.is_set():
      data = read_exact(proc.stdout, FRAME_BYTES_F32)
      if not data:
        break
      frame = np.frombuffer(data, dtype=np.float32).copy()
      samples_sent += len(frame)
      if metrics is not None:
        metrics.mark_once("input_first_frame")
        metrics.mark("input_last_frame")
        metrics.input_samples = samples_sent
      if len(frame) < FRAME_SAMPLES:
        frame = np.pad(frame, (0, FRAME_SAMPLES - len(frame)))
      audio_q.put(frame)
      if realtime:
        # Use a deadline so per-frame scheduling overhead does not accumulate.
        deadline = started_at + samples_sent / TARGET_SR
        delay = max(0, deadline - time.perf_counter())
        if stop_event is not None:
          stop_event.wait(delay)
        else:
          time.sleep(delay)
    stopped = stop_event is not None and stop_event.is_set()
    if stopped and proc.poll() is None:
      proc.terminate()
    proc.wait()
    stderr_thread.join()
    if proc.returncode and not stopped:
      raise RuntimeError("File decoding failed: " + "".join(errors).strip())
    print(f"[+] Test file finished: {samples_sent / TARGET_SR:.3f}s audio "
          f"in {time.perf_counter() - started_at:.3f}s")
  except Exception as exc:
    if metrics is not None:
      metrics.input_error = exc
      metrics.emit("file_input_failed", exc=exc, path=str(path))
    else:
      raise
  finally:
    if proc is not None:
      if proc.poll() is None:
        proc.terminate()
        proc.wait()
      proc.stdout.close()
      proc.stderr.close()
    audio_q.put(None)


def start_ffmpeg_writer(proc):
  """
    Writer thread takes in a process and writes to stdin of that processes
    IN THIS CASE:
      - we call it with start_ffmpeg_writer(ffmpeg_filter_proc)
      - it pulls data from audio_q.get() and writes them into the the ffmpeg_filter_proc
    """

  def writer():
    while True:
      audio = audio_q.get()

      if audio is None:
        try:
          proc.stdin.close()
        except Exception:
          pass
        break

      try:
        proc.stdin.write(audio.tobytes())
        # note... im not actually sure if we need to flush
        proc.stdin.flush()
      except BrokenPipeError:
        break

  thread = threading.Thread(target = writer, daemon = True)
  thread.start()
  return thread


# ------------------------
# UTTERANCE COLLECTION
# ------------------------
def read_next_frame(proc, metrics=None):
  if proc is None:
    frame = audio_q.get()
    if frame is None:
      return None
    frame = np.asarray(frame, dtype=np.float32)
  else:
    data = read_exact(proc.stdout, FRAME_BYTES_F32)
    if not data:
      return None
    frame = np.frombuffer(data, dtype=np.float32)
  if len(frame) < FRAME_SAMPLES:
    frame = np.pad(frame, (0, FRAME_SAMPLES - len(frame)))
  if metrics is not None:
    metrics.mark_once("filtered_first_frame")
    metrics.mark("filtered_last_frame")
  return frame


PROGRESSIVE_TEXT = True
PROGRESSIVE_CHUNK_MS = 7000
PROGRESSIVE_OVERLAP_MS = 2000
PROGRESSIVE_CHUNK_SAMPLES = TARGET_SR * PROGRESSIVE_CHUNK_MS // 1000
PROGRESSIVE_OVERLAP_SAMPLES = TARGET_SR * PROGRESSIVE_OVERLAP_MS // 1000


def clean_text(text):
  text = re.sub(r"\[(?:BLANK_AUDIO|NO_SPEECH|SILENCE)\]", "", text, flags=re.I)
  text = re.sub(r"\s+([.,!?;:])", r"\1", text)
  return " ".join(text.split())


def flush_progressive_chunks(state, full_audio, metrics=None):
  while len(full_audio) - state["processed_samples"] >= PROGRESSIVE_CHUNK_SAMPLES:
    end = state["processed_samples"] + PROGRESSIVE_CHUNK_SAMPLES
    start = max(0, state["processed_samples"] - PROGRESSIVE_OVERLAP_SAMPLES)
    segments = process(full_audio[start:end], metrics=metrics, return_segments=True)
    merge_timed_segments(state, segments, start / TARGET_SR)
    state["processed_samples"] = end


def finalize_progressive_chunks(state, full_audio, metrics=None):
  if len(full_audio) > state["processed_samples"]:
    start = max(0, state["processed_samples"] - PROGRESSIVE_OVERLAP_SAMPLES)
    segments = process(full_audio[start:], metrics=metrics, return_segments=True)
    merge_timed_segments(state, segments, start / TARGET_SR)
    state["processed_samples"] = len(full_audio)
  return state["text"]


def merge_timed_segments(state, segments, audio_start):
  """Replace overlapping words using audio position, with nearby word anchors.

  Whisper timestamps are centiseconds. Keep the earlier decode before the
  middle of the audio overlap and the later decode after it. A matching word
  near that boundary avoids duplication when word timestamps drift slightly.
  """
  incoming = []
  for segment in segments:
    text = clean_text(segment.text)
    if not text:
      continue
    if incoming and not re.search(r"\w", text):
      incoming[-1]["text"] = clean_text(incoming[-1]["text"] + " " + text)
      continue
    incoming.append({"text": text, "start": audio_start + segment.t0 / 100,
                     "end": audio_start + segment.t1 / 100})
  previous = state.get("words", [])
  if not previous:
    combined = incoming
  elif not incoming:
    combined = previous
  else:
    previous_end = state["processed_samples"] / TARGET_SR
    seam = (audio_start + previous_end) / 2
    key = lambda word: re.sub(r"[^\w]", "", word["text"].casefold())
    middle = lambda word: (word["start"] + word["end"]) / 2
    anchors = []
    for i, old in enumerate(previous):
      if old["end"] < audio_start or old["start"] > previous_end:
        continue
      for j, new in enumerate(incoming):
        if new["start"] > previous_end:
          break
        if key(old) and key(old) == key(new) and abs(middle(old) - middle(new)) < 0.75:
          # Short words need a second matching word to be a trustworthy anchor.
          pair = (i + 1 < len(previous) and j + 1 < len(incoming)
                  and key(previous[i + 1]) == key(incoming[j + 1]))
          if len(key(old)) >= 4 or pair:
            anchors.append((abs(middle(old) - seam), i, j))
    if anchors:
      _, i, j = min(anchors)
      combined = previous[:i] + incoming[j:]
    else:
      combined = ([w for w in previous if middle(w) <= seam]
                  + [w for w in incoming if middle(w) > seam])
  state["words"] = combined
  state["text"] = clean_text(" ".join(w["text"] for w in combined))


class SpeechCollector:
  """Retain speech with padding while dropping the middle of long pauses."""

  def __init__(self):
    self.frames = []
    self.pre_roll = deque(maxlen=START_PADDING_FRAMES)
    self.pending_silence = []
    self.speaking = False

  def push(self, frame, speech):
    frame = frame.copy()
    if speech:
      if not self.speaking:
        self.frames.extend(self.pre_roll)
        self.pre_roll.clear()
        self.speaking = True
      else:
        self.frames.extend(self.pending_silence)
      self.pending_silence.clear()
      self.frames.append(frame)
      return False
    if not self.speaking:
      self.pre_roll.append(frame)
      return False
    self.pending_silence.append(frame)
    if len(self.pending_silence) >= END_SILENCE_FRAMES:
      self.frames.extend(self.pending_silence[:START_PADDING_FRAMES])
      self.pre_roll.extend(self.pending_silence[-START_PADDING_FRAMES:])
      self.pending_silence.clear()
      self.speaking = False
      return True
    return False

  def finish(self):
    self.frames.extend(self.pending_silence[:START_PADDING_FRAMES])
    self.pending_silence.clear()
    return self.audio()

  def audio(self):
    if not self.frames:
      return np.array([], dtype=np.float32)
    return np.concatenate(self.frames).astype(np.float32)


class PauseDecoder:
  """Revise a whole speech span, then settle it at a pause.

  Recognition receives the original samples. VAD selects decoding boundaries;
  it never gates individual samples or joins words across removed pauses.
  Long uninterrupted speech falls back to bounded overlapping windows.
  """
  PAUSE_SAMPLES = TARGET_SR * 300 // 1000
  MIN_SAMPLES = TARGET_SR * 4
  MAX_SAMPLES = TARGET_SR * 28

  def __init__(self, pause_ms=300, on_stable_text=None):
    self.PAUSE_SAMPLES = TARGET_SR * pause_ms // 1000
    self.on_stable_text = on_stable_text
    # Live output needs short sentences to settle too; offline decoding keeps
    # the existing longer context floor.
    if on_stable_text is not None:
      self.MIN_SAMPLES = TARGET_SR
    self.frames = []
    self.samples = 0
    self.quiet_samples = 0
    self.has_speech = False
    self.finished = []
    self.preview = ""
    self.decoded_samples = 0
    self.fallback = None

  def audio(self):
    return np.concatenate(self.frames).astype(np.float32) if self.frames else np.array([], dtype=np.float32)

  def decode(self, metrics=None, reason="preview"):
    if self.decoded_samples == self.samples:
      return self.preview
    audio = self.audio()
    if metrics is not None:
      metrics.emit("decode_window", reason=reason, span=len(self.finished) + 1,
                   samples=self.samples, seconds=self.samples / TARGET_SR,
                   fallback=self.fallback is not None or self.samples >= self.MAX_SAMPLES)
    if self.fallback is not None or self.samples >= self.MAX_SAMPLES:
      if self.fallback is None:
        self.fallback = {"processed_samples": 0, "text": ""}
      self.preview = finalize_progressive_chunks(self.fallback, audio, metrics=metrics)
    else:
      self.preview = process(audio, metrics=metrics)
    self.decoded_samples = self.samples
    return self.preview

  def push(self, frame, speech, metrics=None):
    self.frames.append(frame.copy())
    self.samples += len(frame)
    self.has_speech |= speech
    self.quiet_samples = 0 if speech else self.quiet_samples + len(frame)
    ended = self.has_speech and self.quiet_samples >= self.PAUSE_SAMPLES and self.samples >= self.MIN_SAMPLES
    if ended:
      self.finished.append(self.decode(metrics, reason="pause"))
      # Only the latter half of the detected pause overlaps the next span.
      padding = self.audio()[-self.PAUSE_SAMPLES // 2:].copy()
      self.frames = [padding]
      self.samples = len(padding)
      self.quiet_samples = self.samples
      self.has_speech = False
      self.preview = ""
      self.decoded_samples = 0
      self.fallback = None
      if self.on_stable_text is not None:
        self.on_stable_text(self.text())
    elif self.has_speech and self.samples - self.decoded_samples >= PROGRESSIVE_CHUNK_SAMPLES:
      self.decode(metrics)
      print("[progressive, revisable]", self.text())
    return ended

  def text(self):
    return clean_text(" ".join(self.finished + [self.preview]))

  def finish(self, metrics=None):
    if self.has_speech:
      self.decode(metrics, reason="eof")
    return self.text()


def collect_context_utterance(proc, metrics=None, on_stable_text=None):
  decoder = PauseDecoder(PAUSE_ENDPOINT_MS, on_stable_text=on_stable_text)
  frames = []
  speech_seen = False
  quiet = 0
  while True:
    frame = read_next_frame(proc, metrics=metrics)
    if frame is None:
      break
    frames.append(frame)
    if metrics is not None:
      metrics.health_tick()
    speech = is_speech_frame_f32(frame)
    speech_seen |= speech
    quiet = 0 if speech else quiet + 1
    if speech and metrics is not None:
      metrics.mark_once("vad_first_speech_frame")
      metrics.speech_frames += 1
    if PROGRESSIVE_TEXT:
      decoder.push(frame, speech, metrics)
    if ((not WAIT_AUDIO_COMPLETE and speech_seen and quiet >= END_SILENCE_FRAMES)
        or (not USE_TEST_FILE and MAX_UTTERANCE_FRAMES > 0 and len(frames) >= MAX_UTTERANCE_FRAMES)):
      break
  audio = np.concatenate(frames).astype(np.float32) if speech_seen else np.array([], dtype=np.float32)
  if metrics is not None:
    metrics.mark("utterance_end")
    metrics.kept_samples = len(audio)
  return audio, decoder.finish(metrics) if PROGRESSIVE_TEXT else None


def collect_one_utterance(proc, metrics=None, on_stable_text=None):
  if ASR_PROFILE == "balanced":
    return collect_context_utterance(proc, metrics, on_stable_text=on_stable_text)

  print("[+] Reading file audio" if USE_TEST_FILE else "[+] Listening")
  collector = SpeechCollector()
  state = {"processed_samples": 0, "text": ""}
  while True:
    frame = read_next_frame(proc, metrics=metrics)
    if frame is None:
      break
    speech = is_speech_frame_f32(frame)
    if speech and metrics is not None:
      metrics.mark_once("vad_first_speech_frame")
      metrics.speech_frames += 1
    ended = collector.push(frame, speech)
    samples = len(collector.frames) * FRAME_SAMPLES
    if PROGRESSIVE_TEXT and samples - state["processed_samples"] >= PROGRESSIVE_CHUNK_SAMPLES:
      flush_progressive_chunks(state, collector.audio(), metrics=metrics)
      print("[progressive]", state["text"])
    if (ended and not WAIT_AUDIO_COMPLETE) or (not USE_TEST_FILE and samples >= MAX_UTTERANCE_FRAMES * FRAME_SAMPLES):
      break
  audio = collector.finish()
  if metrics is not None:
    metrics.mark("utterance_end")
    metrics.kept_samples = len(audio)
  text = None
  if PROGRESSIVE_TEXT:
    text = finalize_progressive_chunks(state, audio, metrics=metrics)
  return audio, text


# ------------------------
# PIPELINE
# ------------------------
def whisper_audio_context(sample_count, model_path):
  """FUTO ACFT's shortened encoder context, with the keyboard's short-clip floor."""
  if not Path(model_path).name.startswith("acft."):
    return 0
  return max(160, min(1500, math.ceil(sample_count / 320) + 32))


def process(audio, metrics = None, *, return_segments=False):
  if len(audio) == 0:
    return [] if return_segments else ""
  if ASR_PROFILE == "legacy":
    audio = noise_gate(normalize(audio))

  print(f"[+] Transcribing {len(audio) / TARGET_SR:.2f}s audio")

  if metrics is not None:
    metrics.mark("inference_start")
    metrics.inference_calls += 1
    metrics.emit("asr_started", call=metrics.inference_calls, audio_seconds=len(audio) / TARGET_SR,
                 timed_words=return_segments, audio_context=whisper_audio_context(len(audio), ACTIVE_MODEL_PATH),
                 peak=float(np.max(np.abs(audio))), rms=float(np.sqrt(np.mean(audio ** 2))),
                 clipped_samples=int(np.count_nonzero(np.abs(audio) >= .999)))

  started_at = time.perf_counter()
  try:
    segments = model.transcribe(audio, print_stats=True, token_timestamps=return_segments,
                                max_len=1 if return_segments else 0, split_on_word=True,
                                audio_ctx=whisper_audio_context(len(audio), ACTIVE_MODEL_PATH))
  except Exception as exc:
    if metrics is not None:
      metrics.emit("asr_failed", exc=exc, call=metrics.inference_calls,
                   duration_ms=(time.perf_counter() - started_at) * 1000)
    raise

  if metrics is not None:
    metrics.mark("inference_end")
    metrics.inference_seconds += time.perf_counter() - started_at

  text = clean_text(" ".join(segment.text for segment in segments))
  if metrics is not None:
    metrics.emit("asr_completed", call=metrics.inference_calls,
                 duration_ms=(time.perf_counter() - started_at) * 1000, **diag.text_fields(text))
  print(segments)
  print(text)

  return segments if return_segments else text




def run_timed(command, label, *, input_text=None):
  started_at = time.perf_counter()
  result = subprocess.run(command, check=True, input=input_text, text=True, capture_output=True)
  if result.stdout:
    print(result.stdout, end="")
  if result.stderr:
    print(result.stderr, end="", file=sys.stderr)
  elapsed_seconds = time.perf_counter() - started_at
  print(
    f"[timing] {label}: {elapsed_seconds:.6f}s ({elapsed_seconds * 1000:.2f} ms)"
  )
  return elapsed_seconds


def type_with_ydotool(text, metrics = None):
  if not text:
    print("[!] No text to type")
    return

  yd_start_delay = 50
  yd_type_delay = 1

  if metrics is not None:
    metrics.mark("typing_start")
    metrics.emit("typing_started", text_chars=len(text), backend="ydotool",
                 start_delay_ms=yd_start_delay, key_delay_ms=yd_type_delay)

  try:
    # The old --delay option is absent in ydotool 1.x. Keep the initial
    # focus-settling delay here; --key-delay and stdin work in both versions.
    time.sleep(yd_start_delay / 1000)
    run_timed(
      ["ydotool", "type", "--key-delay", str(yd_type_delay), "--file", "-"],
      "ydotool",
      input_text=text,
    )
  except Exception as exc:
    if metrics is not None:
      metrics.emit("typing_failed", exc=exc, returncode=getattr(exc, "returncode", None),
                   stderr=getattr(exc, "stderr", None), stdout=getattr(exc, "stdout", None))
    raise

  if metrics is not None:
    metrics.mark("typing_end")
    metrics.emit("typing_completed", duration_ms=metrics.delta("typing_start", "typing_end") * 1000,
                 text_chars=len(text))


# ------------------------
# MAIN
# ------------------------
def main(argv=None):
  global model, metrics_global, audio_q, vad, USE_TEST_FILE, TYPE_OUTPUT, USE_FILTER, PROGRESSIVE_TEXT, ACTIVE_MODEL_PATH, ASR_PROFILE, PAUSE_ENDPOINT_MS, SOUND_DEVICE_ID, WAIT_AUDIO_COMPLETE
  parser = argparse.ArgumentParser(description="CPU dictation pipeline; file input and stdout by default.")
  parser.add_argument("audio", nargs="?", default=TEST_AUDIO_FILE)
  parser.add_argument("--model", default=MODEL_PATH)
  parser.add_argument("--threads", type=int, default=4)
  parser.add_argument("--mode", choices=("progressive", "complete"), default="progressive")
  parser.add_argument("--offline", action="store_true", help="Feed the file without real-time pacing.")
  parser.add_argument("--profile", choices=("balanced", "legacy"), default="balanced")
  parser.add_argument("--pause-ms", type=int, default=300, help="Pause required to settle a speech span (default: 300 ms).")
  filters = parser.add_mutually_exclusive_group()
  filters.add_argument("--filter", dest="filter", action="store_true", help="Opt into RNNoise filtering.")
  filters.add_argument("--no-filter", dest="filter", action="store_false")
  parser.set_defaults(filter=None)
  parser.add_argument("--mic", action="store_true", help="Explicitly enable microphone capture instead of file input.")
  parser.add_argument("--type", action="store_true", help="Explicitly enable keyboard output.")
  parser.add_argument("--device", help="Sounddevice input ID or name; defaults to the system input.")
  parser.add_argument("--until-silence", action="store_true", help="End microphone capture after 700 ms of detected silence.")
  parser.add_argument("--json-out", type=Path)
  args = parser.parse_args(argv)
  if args.threads < 1:
    parser.error("--threads must be positive")
  if args.pause_ms < FRAME_MS:
    parser.error(f"--pause-ms must be at least {FRAME_MS}")
  SOUND_DEVICE_ID = int(args.device) if args.device and args.device.isdecimal() else args.device
  WAIT_AUDIO_COMPLETE = not args.until_silence
  ACTIVE_MODEL_PATH = args.model
  USE_TEST_FILE, TYPE_OUTPUT = not args.mic, args.type
  ASR_PROFILE = args.profile
  PAUSE_ENDPOINT_MS = args.pause_ms
  USE_FILTER = args.filter if args.filter is not None else args.profile == "legacy"
  PROGRESSIVE_TEXT = args.mode == "progressive"
  metrics = Metrics()
  metrics_global = metrics
  metrics.mark("program_start")
  audio_q = queue.Queue()
  vad = webrtcvad.Vad(VAD_MODE)
  stop_event = threading.Event()
  filter_process = writer_thread = feeder_thread = None
  text, error = "", None
  metrics.emit("pipeline_starting", model=args.model, profile=ASR_PROFILE,
               filtering=USE_FILTER, threads=args.threads, pause_ms=PAUSE_ENDPOINT_MS,
               microphone=args.mic, input_device=SOUND_DEVICE_ID,
               input_file=str(args.audio) if USE_TEST_FILE else None, keyboard_output=TYPE_OUTPUT)
  try:
    if USE_TEST_FILE and not Path(args.audio).is_file():
      raise FileNotFoundError(args.audio)
    metrics.mark("model_load_start")
    metrics.emit("model_loading", model=args.model, device="cpu", threads=args.threads)
    # The bundled Flash Attention path gives order-dependent transcripts when
    # ACFT audio_ctx shrinks between calls. Keep it disabled in the default path.
    model = Model(model=args.model, context_params={"use_gpu": False, "flash_attn": ASR_PROFILE == "legacy"},
                  no_context=True, split_on_word=True, n_threads=args.threads, language="en")
    metrics.mark("model_load_end")
    metrics.emit("model_loaded", load_ms=metrics.delta("model_load_start", "model_load_end")*1000)
    if USE_FILTER:
      filter_process = start_ffmpeg_filter()
      writer_thread = start_ffmpeg_writer(filter_process)
    metrics.mark("listening_start")
    if USE_TEST_FILE:
      metrics.emit("capture_started", source="file", realtime=not args.offline)
      feeder_thread = threading.Thread(
        target=play_file_into_audio_q, args=(args.audio,),
        kwargs={"realtime": not args.offline, "metrics": metrics, "stop_event": stop_event},
        daemon=True)
      feeder_thread.start()
      audio, text = collect_one_utterance(filter_process, metrics=metrics)
    else:
      with start_mic_stream():
        metrics.emit("capture_started", source="microphone", input_device=SOUND_DEVICE_ID)
        audio, text = collect_one_utterance(filter_process, metrics=metrics)
      metrics.emit("capture_closed", source="microphone")
    if metrics.input_error is not None:
      raise metrics.input_error
    if filter_process is not None and USE_TEST_FILE and WAIT_AUDIO_COMPLETE:
      filter_process.wait()
      if filter_process.returncode:
        raise RuntimeError(f"Audio preprocessing failed (exit {filter_process.returncode})")
    if text is None:
      text = process(audio, metrics=metrics)
    metrics.mark("text_ready")
    metrics.emit("text_ready", **diag.text_fields(text or ""), final_asr_delay_ms=(
      metrics.delta("input_last_frame", "text_ready")*1000 if metrics.has("input_last_frame") else None))
    print("\n=== TRANSCRIPTION ===")
    print(text if text else "[No speech detected]")
    if not (text or "").strip():
      user_feedback.no_text(metrics, desktop=not USE_TEST_FILE and TYPE_OUTPUT)
    elif TYPE_OUTPUT:
      type_with_ydotool(text, metrics=metrics)
  except KeyboardInterrupt:
    error = "Interrupted"
    metrics.emit("pipeline_interrupted")
  except Exception as exc:
    error = f"{type(exc).__name__}: {exc}"
    metrics.emit("pipeline_failed", exc=exc)
    print(error, file=sys.stderr)
    if not USE_TEST_FILE and TYPE_OUTPUT:
      user_feedback.notify("Dictation failed",
        "Dictation could not finish. Check your microphone and the troubleshooting report.", emit=metrics.emit)
  finally:
    stop_event.set()
    if filter_process is not None and filter_process.poll() is None:
      filter_process.terminate()
      filter_process.wait()
    audio_q.put(None)
    if feeder_thread is not None:
      feeder_thread.join(timeout=5)
    if writer_thread is not None:
      writer_thread.join(timeout=5)
    if filter_process is not None:
      for stream in (filter_process.stdin, filter_process.stdout):
        try:
          stream.close()
        except BrokenPipeError:
          pass
    metrics.mark("program_done")
    metrics.health_tick(force=True)
    metrics.emit("pipeline_finished", error=error, inference_calls=metrics.inference_calls,
                 inference_ms=metrics.inference_seconds*1000,
                 duration_ms=metrics.delta("program_start", "program_done")*1000)
    diag.flush()
    metrics.report()
    if args.json_out is not None:
      args.json_out.parent.mkdir(parents=True, exist_ok=True)
      args.json_out.write_text(json.dumps({
        "status": "failed" if error else "completed", "error": error,
        "text": text or "", "backend": "cpu", "mode": args.mode,
        "profile": ASR_PROFILE, "filtering": USE_FILTER,
        "pause_ms": PAUSE_ENDPOINT_MS,
        "flash_attn": ASR_PROFILE == "legacy",
        "model": args.model, "threads": args.threads,
        "audio": str(args.audio) if USE_TEST_FILE else None,
        "microphone": not USE_TEST_FILE, "typing": TYPE_OUTPUT,
        "input_seconds": metrics.input_samples / TARGET_SR,
        "retained_seconds": metrics.kept_samples / TARGET_SR,
        "speech_frames": metrics.speech_frames,
        "inference_calls": metrics.inference_calls,
        "inference_seconds": metrics.inference_seconds,
        "audio_end_to_text_seconds": (
          metrics.delta("input_last_frame", "text_ready")
          if metrics.has("input_last_frame") and metrics.has("text_ready") else None),
        "wall_seconds": metrics.events["program_done"] - metrics.t0,
      }, indent=2) + "\n")
  return 1 if error else 0


if __name__ == "__main__":
  diag.configure("pipeline")
  raise SystemExit(main())
