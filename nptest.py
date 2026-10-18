#!/usr/bin/env python3

from datetime import datetime
import re
import shlex
import subprocess
import time

import numpy as np
import soundfile as sf
import librosa
import webrtcvad
from pywhispercpp.model import Model

# ------------------------
# CONFIG
# ------------------------
MODEL_PATH = "/home/scott/Documents/tits-dic/models/ggml/tiny.en.q8.bin"
TARGET_SR = 16000
VAD_MODE = 2  # 0-3 (3 = most aggressive)
FRAME_MS = 30

# ------------------------
# LOAD MODEL (once)
# ------------------------
model: Model


# ------------------------
# TIMING METRICS
# ------------------------
class Metrics:

  def __init__(self):
    self.t0 = time.perf_counter()
    self.wall_start = datetime.now()
    self.events = {}

  def mark(self, name):
    self.events[name] = time.perf_counter()

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

    self.print_delta(
      "Time to load model",
      "model_load_start",
      "model_load_end",
    )
    self.print_delta(
      "Time to start file load",
      "program_start",
      "file_load_start",
    )
    self.print_delta(
      "Time spent loading and preprocessing audio",
      "file_load_start",
      "file_load_end",
    )
    self.print_delta(
      "Time spent VAD filtering",
      "vad_start",
      "vad_end",
    )
    self.print_delta(
      "Time from audio ready to inference start",
      "audio_ready",
      "inference_start",
    )
    self.print_delta(
      "Time from inference start to inference end",
      "inference_start",
      "inference_end",
    )
    self.print_delta(
      "prog start to typed text complete",
      "program_start",
      "typing_end",
    )

    self.print_delta(
      "Time from inference end to typed text complete",
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
# AUDIO LOAD + FORMAT
# ------------------------
def load_audio(path):
  audio, sr = sf.read(path)

  # stereo → mono
  if audio.ndim > 1:
    audio = audio.mean(axis = 1)

  audio = audio.astype("float32")

  # resample
  if sr != TARGET_SR:
    audio = librosa.resample(audio, orig_sr = sr, target_sr = TARGET_SR)

  return audio


def load_audio_fast(path):
  cmd = [
    "ffmpeg", "-i",
    str(path), "-ac", "1", "-ar", "16000", "-f", "f32le", "-"
  ]

  out = subprocess.run(cmd, capture_output = True, check = True).stdout
  audio = np.frombuffer(out, dtype = np.float32)

  return audio


def load_audio_fast_process(path):
  cmd = [
    "ffmpeg",
    "-i",
    str(path),

    # audio filters
    "-af",
    "highpass=f=80,lowpass=f=8000,"
    "dynaudnorm,"
    "afftdn",

    # format
    "-ac",
    "1",
    "-ar",
    "16000",
    "-f",
    "f32le",
    "-"
  ]

  out = subprocess.run(cmd, capture_output = True, check = True).stdout
  return np.frombuffer(out, dtype = np.float32)


def load_audio_fast_process_full(path, metrics = None):
  cmd = [
    "ffmpeg", "-i",
    str(path), "-af", "highpass=f=80,lowpass=f=7600,"
    "arnndn=m=/home/scott/Documents/tits-dic/models/rnnoise/voice-recording.rnnn:mix=0.8,"
    "dynaudnorm=f=150:g=15", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"
  ]

  try:
    if metrics is not None:
      metrics.mark("file_load_start")

    out = subprocess.run(cmd, capture_output = True, check = True).stdout

    if metrics is not None:
      metrics.mark("file_load_end")

    return np.frombuffer(out, dtype = np.float32)

  except subprocess.CalledProcessError as e:
    if metrics is not None and not metrics.has("file_load_end"):
      metrics.mark("file_load_end")

    print("❌ FFmpeg error:")
    print(e.stderr.decode() if e.stderr else "No stderr")
    return None


# ------------------------
# PREPROCESS
# ------------------------
def normalize(audio):
  return audio / (np.max(np.abs(audio)) + 1e-6)


def noise_gate(audio, threshold = 0.01):
  return np.where(np.abs(audio) < threshold, 0, audio)


# ------------------------
# VAD (WebRTC)
# ------------------------
vad = webrtcvad.Vad(VAD_MODE)


def frame_generator(audio, sr = TARGET_SR, frame_ms = FRAME_MS):
  frame_len = int(sr * frame_ms / 1000)
  for i in range(0, len(audio), frame_len):
    yield audio[i:i + frame_len]


def vad_filter(audio):
  voiced = []

  for frame in frame_generator(audio):
    if len(frame) == 0:
      continue

    pcm = (frame * 32768).astype("int16").tobytes()

    if vad.is_speech(pcm, TARGET_SR):
      voiced.append(frame)

  return np.concatenate(voiced) if voiced else np.array([], dtype = np.float32)


# ------------------------
# PIPELINE
# ------------------------
def process(audio, metrics = None):
  audio = normalize(audio)
  audio = noise_gate(audio)

  if metrics is not None:
    metrics.mark("vad_start")

  audio = vad_filter(audio)

  if metrics is not None:
    metrics.mark("vad_end")
    metrics.mark("audio_ready")

  if len(audio) == 0:
    return ""

  if metrics is not None:
    metrics.mark("inference_start")

  segments = model.transcribe(audio,
                              print_stats = True,
                              extract_probability=True,
                              )

  if metrics is not None:
    metrics.mark("inference_end")

  width = 60
  text = " ".join([segment.text for segment in segments])
  print("\n=== SEGMENTS ===")
  print(segments)

    
  for segment in segments:
    t1_seconds = segment.t1 / 1000.0
    left = f"{t1_seconds:6.3f}s: {segment.text}"
    print(f"{left:<25} {segment.probability:.4f}")
  
  # truncate
  # for segment in segments:
  #   t1_seconds = segment.t1 / 1000.0
  #   left = f"{t1_seconds:6.3f}s: {segment.text}"
  #   print(f"{left[:60]:<60} {segment.probability:.4f}")
  
  print("\n=== TEXT ===")
  print(text)
  return text


def clean(text: str) -> str:
  # handle backspaces correctly
  result = []
  for c in text:
    if c == '\b':
      if result:
        result.pop()
    else:
      result.append(c)
  text = ''.join(result)

  # handle carriage returns (overwrite line)
  if '\r' in text:
    text = text.split('\r')[-1]

  return text


def run_timed(command, label):
  started_at = time.perf_counter()
  subprocess.run(command, check = True)
  elapsed_seconds = time.perf_counter() - started_at
  print(
    f"[timing] {label}: {elapsed_seconds:.6f}s ({elapsed_seconds * 1000:.2f} ms)"
  )
  return elapsed_seconds


# ------------------------
# MAIN
# ------------------------
if __name__ == "__main__":
  import sys

  metrics = Metrics()
  metrics.mark("program_start")

  if len(sys.argv) < 2:
    # print("Usage: python script.py <audio.wav>")
    # exit(1)
    path = "/home/scott/Documents/tits-dic/tst/data/short.wav"
  else:
    path = sys.argv[1]


  print("[+] Loading:", path)

  print("[+] Loading model:", MODEL_PATH)
  metrics.mark("model_load_start")
  # model = Model(
  #   model = MODEL_PATH,
  #       # 0 = greedy sampling, nonzero = beam search sampling
  #   params_sampling_strategy=1,      
  #   # greedy decoder settings; -1 leaves library defaults in place
  #   greedy={ "best_of": -1 },
  #   # beam-search decoder settings; -1 leaves library defaults in place},         
  #   beam_search={
  #       "beam_size": 5,
  #       "patience": 1.0,
  #   },
  
  #   # max characters per segment; 0 means unlimited
  #   max_len=1,                           
  #   # split long segments on word boundaries instead of token boundaries
  #   split_on_word=True, 

  #   )
  
  model = Model(
    model=MODEL_PATH,
    params_sampling_strategy=1,
    # greedy={"best_of": 1},
    beam_search={
      "beam_size": 3,
      "patience": 5,
    },
    token_timestamps=True,
    max_len=1,
    # split_on_word=True, 
    # split_on_token=True, 
  )
  metrics.mark("model_load_end")

  audio = load_audio_fast_process_full(path, metrics = metrics)

  print("[+] Processing...")
  text = process(audio, metrics = metrics)

  print("\n=== TRANSCRIPTION ===")
  print(text)

  yd_start_delay = 40
  yd_type_delay = 0
  metrics.mark("typing_start")
  # run_timed([
  #   "ydotool",
  #   "type",
  #   "--delay",
  #   str(yd_start_delay),
  #   "--key-delay",
  #   str(yd_type_delay),
  #   text,
  # ], "ydotool")
  metrics.mark("typing_end")
  metrics.mark("program_done")
  # metrics.report()
