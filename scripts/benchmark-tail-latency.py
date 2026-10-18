#!/usr/bin/env python3
"""Measure final-frame-to-text delay in the actual file pipeline, CPU only.

Only the seven distinct personal recordings under tst/data and data/mine/short
are eligible. Models stay loaded between recordings. Each file is paced at
real time and passes through the actual FFmpeg, VAD, collector, ASR and merger.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def approved_recordings():
    # Pin the allowlist: adding another WAV to the repo must not expand tests.
    candidates = [ROOT / f"tst/data/{name}.wav" for name in ("short", "002SHORT", "0000WEIRD")]
    candidates += [ROOT / f"data/mine/short/{name}/orig.wav"
                   for name in ("000SHORT", "001SHORT", "003SHORT", "004SHORT")]
    seen, recordings = set(), []
    for path in candidates:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest not in seen:
            seen.add(digest)
            recordings.append((path, digest))
    if len(recordings) != 7:
        raise RuntimeError(f"Expected the seven approved recordings, found {len(recordings)}")
    return recordings


def summarize(directory):
    approved = {(str(p.relative_to(ROOT)), digest) for p, digest in approved_recordings()}
    result = {"metric": "Milliseconds from final raw audio frame arrival to complete transcript ready",
              "timing_scope": "Actual real-time file pipeline including configured preprocessing, EOF drain, final ASR and merging; no typing",
              "recordings_per_model": 7, "passes_per_recording": 1, "backends": []}
    for name in ("futo-base", "futo-tiny", "redux", "phonon"):
        path = directory / f"{name}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        measured = {(row["recording"], row["sha256"]) for row in data["rows"]}
        if measured != approved or len(data["rows"]) != 7:
            raise RuntimeError(f"{name}: incomplete or unapproved audio test set")
        if data["microphone"] or data["typing"] or data["device"] != "cpu":
            raise RuntimeError(f"{name}: unexpected benchmark configuration")
        result["backends"].append(data)
    lines = [result["metric"], result["timing_scope"],
             "CPU only, four threads, warm model; one real-time run per approved recording.",
             "Median/mean/range are across the seven different recordings, not repeated runs of one file.", "",
             f'{"Model":<14} {"Median ms":>11} {"Mean ms":>11} {"Best ms":>11} {"Worst ms":>11}']
    for data in sorted(result["backends"], key=lambda d: d["summary"]["median_ms"]):
        s = data["summary"]
        lines.append(f'{data["backend"]:<14} {s["median_ms"]:11.1f} {s["mean_ms"]:11.1f} {s["min_ms"]:11.1f} {s["max_ms"]:11.1f}')
    lines += ["", "Per-recording delays:"]
    for recording, _ in sorted(approved):
        delays = []
        for data in result["backends"]:
            row = next(r for r in data["rows"] if r["recording"] == recording)
            delays.append(f'{data["backend"]}={row["audio_end_to_text_seconds"]*1000:.1f} ms')
        lines.append(recording + ": " + "; ".join(delays))
    (directory / "comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    (directory / "comparison.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--backend", choices=("futo-base", "futo-tiny", "redux", "phonon"))
    action.add_argument("--summarize", action="store_true")
    parser.add_argument("--profile", choices=("balanced", "legacy"), default="balanced")
    parser.add_argument("--pause-ms", type=int, default=300)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--output", type=Path, default=ROOT / "build/asr-compare/tail-latency")
    args = parser.parse_args()
    if args.summarize:
        summarize(args.output)
        return
    os.environ.update(CUDA_VISIBLE_DEVICES="", FERMION_DEVICE="cpu", FERMION_CPU_THREADS=str(args.threads),
                      OMP_WAIT_POLICY="PASSIVE", OMP_NUM_THREADS=str(args.threads))
    spec = importlib.util.spec_from_file_location("asr_comparison", ROOT / "scripts/compare-asr.py")
    comparison = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(comparison)
    recordings = approved_recordings()
    helpers = comparison.pipeline_helpers()
    decode, description = comparison.load_backend(args.backend, args.threads, helpers, flash_attn=args.profile == "legacy")

    class ModelAdapter:
        def transcribe(self, audio, **kwargs):
            timed = kwargs.get("token_timestamps", False)
            text, segments = decode(audio, timed)
            return segments if timed else [SimpleNamespace(text=text, t0=0, t1=len(audio) / 160)]

    # npstream imports this constructor, but these environments need only their
    # own ASR engine. Replace the unused Whisper import with the adapter; all
    # capture, preprocessing, collection, timing and merge code executes normally.
    if not args.backend.startswith("futo-"):
        package = ModuleType("pywhispercpp")
        package.__path__ = []
        module = ModuleType("pywhispercpp.model")
        module.Model = ModelAdapter
        sys.modules.update({"pywhispercpp": package, "pywhispercpp.model": module})
    import npstream as pipeline
    adapter = ModelAdapter()
    pipeline.Model = lambda **kwargs: adapter

    def forbidden(*args, **kwargs):
        raise AssertionError("Microphone/keyboard use is forbidden in this benchmark")

    pipeline.start_mic_stream = forbidden
    pipeline.type_with_ydotool = forbidden
    pipeline.mic_callback = forbidden
    args.output.mkdir(parents=True, exist_ok=True)
    # Warm the actual decoder with audio from an approved recording.
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(recordings[0][0]), "-ar", "16000",
                          "-ac", "1", "-f", "f32le", "-"], capture_output=True, check=True).stdout
    decode(np.frombuffer(raw, dtype=np.float32).copy(), True)
    results = {"backend": args.backend, "description": description, "device": "cpu", "threads": args.threads,
               "metric": "text_ready - input_last_frame", "realtime_feed": True, "live_filtering": args.profile == "legacy", "profile": args.profile,
               "microphone": False, "typing": False, "pause_ms": args.pause_ms, "rows": []}
    model_name = description["model"]
    for index, (path, digest) in enumerate(recordings):
        output = args.output / f"{args.backend}-{index}.json"
        status = pipeline.main([str(path), "--model", model_name, "--threads", str(args.threads),
                                "--profile", args.profile, "--pause-ms", str(args.pause_ms), "--json-out", str(output)])
        row = json.loads(output.read_text())
        if status or row["audio_end_to_text_seconds"] is None:
            raise RuntimeError(f"Pipeline failed for {path}: {row}")
        row.update(sha256=digest, recording=str(path.relative_to(ROOT)))
        results["rows"].append(row)
        delays = [r["audio_end_to_text_seconds"] for r in results["rows"]]
        results["summary"] = {"recordings": len(delays), "mean_ms": statistics.mean(delays) * 1000,
                              "median_ms": statistics.median(delays) * 1000,
                              "min_ms": min(delays) * 1000, "max_ms": max(delays) * 1000}
        comparison.write_json(args.output / f"{args.backend}.json", results)
        print("TAIL_RESULT", args.backend, row["recording"], round(delays[-1] * 1000, 1), "ms", flush=True)
    print("TAIL_SUMMARY", args.backend, json.dumps(results["summary"]), flush=True)


if __name__ == "__main__":
    main()
