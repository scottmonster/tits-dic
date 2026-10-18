#!/usr/bin/env python3
"""CPU-only, file-only comparison of FUTO ACFT, Redux/Photon, and Phonon-2.

Run prepare/summarize in the existing project environment. Run each backend
in its own environment; no microphone, keyboard, cloud ASR, or GPU is used.
"""
import argparse
import ast
import importlib.metadata
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import resource
from statistics import median
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SR = 16000
CLIPS = ("short", "002SHORT", "0000WEIRD")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")


def pipeline_helpers():
    # Use the application's actual merger without importing its Whisper or VAD
    # dependencies into the two isolated environments.
    source = ast.parse((ROOT / "npstream.py").read_text())
    names = {"clean_text", "merge_timed_segments", "whisper_audio_context"}
    definitions = [n for n in source.body if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace = {"re": re, "math": math, "Path": Path, "TARGET_SR": SR}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), "npstream.py", "exec"), namespace)
    return namespace


def prepare(directory):
    import numpy as np
    import npstream as p
    manifest = {"sample_rate": SR, "chunk_seconds": 7, "overlap_seconds": 2,
                "microphone": False, "typing": False, "clips": []}
    for name in CLIPS:
        raw = np.frombuffer(subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(ROOT / f"tst/data/{name}.wav"),
             "-ar", str(SR), "-ac", "1", "-f", "f32le", "-"],
            capture_output=True, check=True).stdout, dtype=np.float32).copy()
        started = time.perf_counter()
        filtered = np.frombuffer(subprocess.run(
            ["ffmpeg", "-v", "error", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-",
             "-af", f"highpass=f=80,lowpass=f=7600,arnndn=m={p.RNNOISE_MODEL}:mix=0.8",
             "-ar", str(SR), "-ac", "1", "-f", "f32le", "-"],
            input=raw.tobytes(), capture_output=True, check=True).stdout, dtype=np.float32)
        p.vad = p.webrtcvad.Vad(p.VAD_MODE)
        collector = p.SpeechCollector()
        chunks = []
        processed = 0
        for offset in range(0, len(filtered), p.FRAME_SAMPLES):
            frame = filtered[offset:offset + p.FRAME_SAMPLES]
            if len(frame) < p.FRAME_SAMPLES:
                frame = np.pad(frame, (0, p.FRAME_SAMPLES - len(frame)))
            collector.push(frame, p.is_speech_frame_f32(frame))
            count = len(collector.frames) * p.FRAME_SAMPLES
            while count - processed >= p.PROGRESSIVE_CHUNK_SAMPLES:
                end = processed + p.PROGRESSIVE_CHUNK_SAMPLES
                chunks.append({"start": max(0, processed - p.PROGRESSIVE_OVERLAP_SAMPLES), "end": end,
                               "available_at": min((offset + p.FRAME_SAMPLES) / SR, len(raw) / SR)})
                processed = end
        audio = collector.finish()
        if len(audio) > processed:
            chunks.append({"start": max(0, processed - p.PROGRESSIVE_OVERLAP_SAMPLES),
                           "end": len(audio), "available_at": len(raw) / SR})
        directory.mkdir(parents=True, exist_ok=True)
        np.save(directory / f"{name}-raw.npy", raw)
        np.save(directory / f"{name}-pipeline.npy", audio)
        manifest["clips"].append({"name": name, "audio_seconds": len(raw) / SR,
                                  "retained_seconds": len(audio) / SR, "chunks": chunks,
                                  "preprocessing_seconds": time.perf_counter() - started,
                                  "reference": (ROOT / f"tst/data/text/{name}.txt").read_text().strip()})
    write_json(directory / "inputs.json", manifest)
    print(json.dumps(manifest, indent=2))


def load_backend(name, threads, helpers, *, flash_attn=False):
    import numpy as np
    clean = helpers["clean_text"]
    if name.startswith("futo-"):
        from pywhispercpp.model import Model
        filename = "acft.base.q8.bin" if name == "futo-base" else "acft.tiny.en.q8.bin"
        path = ROOT / "models/ggml" / filename
        started = time.perf_counter()
        model = Model(str(path), context_params={"use_gpu": False, "flash_attn": flash_attn}, n_threads=threads,
                      no_context=True, language="en", split_on_word=True)
        load_seconds = time.perf_counter() - started
        def decode(audio, timed):
            segments = model.transcribe(audio, audio_ctx=helpers["whisper_audio_context"](len(audio), path),
                                        token_timestamps=timed, max_len=1 if timed else 0,
                                        split_on_word=True)
            return clean(" ".join(s.text for s in segments)), segments
        return decode, {"model": filename, "load_seconds": load_seconds,
                        "audio_ctx_policy": "clamp(ceil(samples/320)+32,160,1500)",
                        "sampling": "greedy", "flash_attn": flash_attn, "weights_bytes": path.stat().st_size}
    import torch
    torch.set_num_threads(threads)
    torch.set_num_interop_threads(1)
    if name == "redux":
        import moondream as md
        started = time.perf_counter()
        model = md.photon("moondream/parakeet-redux", device="cpu", cpu_threads=threads)
        load_seconds = time.perf_counter() - started
        def decode(audio, timed):
            result = model.transcribe(audio=np.asarray(audio, dtype=np.float32), sample_rate=SR,
                                      timestamps="word" if timed else "none")
            words = [SimpleNamespace(text=w["word"], t0=w["start"] * 100, t1=w["end"] * 100)
                     for s in result.get("segments", []) for w in s.get("words", [])]
            return clean(result["text"]), words
        return decode, {"model": "moondream/parakeet-redux", "load_seconds": load_seconds,
                        "runtime": "Photon / Kestrel, CPU", "torch": torch.__version__}
    import fermion
    started = time.perf_counter()
    model = fermion.load_speech("phonon-2")
    load_seconds = time.perf_counter() - started
    def decode(audio, timed):
        result = model.transcribe_array_detailed(audio)
        words = [SimpleNamespace(text=w["text"], t0=w["start"] * 100, t1=w["end"] * 100)
                 for w in result.words]
        return clean(result.text), words
    return decode, {"model": "FermionResearch/Phonon-2", "load_seconds": load_seconds,
                    "description": model.describe(), "torch": torch.__version__}


def run(args):
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["FERMION_DEVICE"] = "cpu"
    os.environ["FERMION_CPU_THREADS"] = str(args.threads)
    os.environ["OMP_WAIT_POLICY"] = "PASSIVE"
    os.environ["OMP_NUM_THREADS"] = str(args.threads)
    import numpy as np
    helpers = pipeline_helpers()
    decode, description = load_backend(args.backend, args.threads, helpers)
    manifest = json.loads((args.directory / "inputs.json").read_text())
    result = {"backend": args.backend, "device": "cpu", "threads": args.threads,
              "microphone": False, "typing": False, "description": description,
              "timing_scope": "Warm inference on shared prepared arrays; real-time chunk replay excludes live filter execution and keyboard output.",
              "python": sys.version, "executable": sys.executable, "rows": []}
    result["packages"] = {p: importlib.metadata.version(p) for p in
                          ("moondream", "kestrel", "kestrel-kernels", "torch", "fermion-research", "pywhispercpp")
                          if importlib.util.find_spec(p.replace("-", "_")) is not None
                          or p in {d.metadata["Name"] for d in importlib.metadata.distributions()}}
    output = args.directory / f"{args.backend}.json"

    def normalized(audio):
        if len(audio) == 0:
            return audio
        audio = audio / (np.max(np.abs(audio)) + 1e-6)
        return np.where(np.abs(audio) < 0.01, 0, audio).astype(np.float32)

    def progressive(audio, clip, realtime=False):
        state = {"processed_samples": 0, "text": ""}
        timings = []
        started = time.perf_counter()
        for chunk in clip["chunks"]:
            if realtime:
                time.sleep(max(0, started + chunk["available_at"] - time.perf_counter()))
            tick = time.perf_counter()
            text, words = decode(normalized(audio[chunk["start"]:chunk["end"]]), True)
            if text and not words:
                raise RuntimeError("Backend returned text without word timestamps; cannot compare merging")
            helpers["merge_timed_segments"](state, words, chunk["start"] / SR)
            state["processed_samples"] = chunk["end"]
            timings.append(time.perf_counter() - tick)
        elapsed = time.perf_counter() - started
        return {"text": state["text"], "seconds": elapsed, "chunk_seconds": timings,
                "last_chunk_seconds": timings[-1] if timings else 0,
                "inference_calls": len(timings),
                "audio_end_to_text_seconds": max(0, elapsed - clip["audio_seconds"]) if realtime else None}

    for clip in manifest["clips"]:
        raw = np.load(args.directory / f"{clip['name']}-raw.npy")
        pipeline = np.load(args.directory / f"{clip['name']}-pipeline.npy")
        for case in ("raw_full", "pipeline_full", "pipeline_progressive"):
            def once():
                if case == "pipeline_progressive":
                    return progressive(pipeline, clip)
                audio = raw if case == "raw_full" else normalized(pipeline)
                tick = time.perf_counter()
                text, _ = decode(audio, False)
                return {"text": text, "seconds": time.perf_counter() - tick}
            once()  # Warm each input and mode before timing.
            trials = [once() for _ in range(args.runs)]
            row = {"clip": clip["name"], "case": case, "audio_seconds": clip["audio_seconds"],
                   "median_seconds": median(t["seconds"] for t in trials), "trials": trials,
                   "text": trials[-1]["text"]}
            result["rows"].append(row)
            write_json(output, result)
            print(args.backend, clip["name"], case, round(row["median_seconds"], 4), flush=True)
        if args.realtime and clip["name"] == "0000WEIRD":
            row = progressive(pipeline, clip, realtime=True)
            row.update(clip=clip["name"], case="pipeline_realtime", audio_seconds=clip["audio_seconds"])
            result["rows"].append(row)
            write_json(output, result)
            print(args.backend, "realtime audio-end latency", row["audio_end_to_text_seconds"], flush=True)
    result["peak_rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    write_json(output, result)


def summarize(directory):
    spec = importlib.util.spec_from_file_location("comparison_score", ROOT / "tst/data/scripts/score.py")
    scorer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = scorer
    spec.loader.exec_module(scorer)
    inputs = json.loads((directory / "inputs.json").read_text())
    references = {c["name"]: scorer.normalize_for_word_accuracy(c["reference"]) for c in inputs["clips"]}
    summary = {"inputs": inputs, "backends": [], "aggregate_rows": [],
               "method": "CPU, four threads, one warmup plus three measured passes per input/mode; raw_full uses resampled mono input; pipeline modes share filtering, VAD, normalization, chunk boundaries and merger. Real-time replay excludes live filter execution and typing."}
    for name in ("futo-base", "futo-tiny", "redux", "phonon"):
        path = directory / f"{name}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text())
        for row in data["rows"]:
            ref = references[row["clip"]]
            hyp = scorer.normalize_for_word_accuracy(row["text"])
            row["word_error_rate"] = scorer.jiwer.wer(ref, hyp)
            row["char_error_rate"] = scorer.jiwer.cer(ref, hyp)
            for trial in row.get("trials", []):
                trial_hyp = scorer.normalize_for_word_accuracy(trial["text"])
                trial["word_error_rate"] = scorer.jiwer.wer(ref, trial_hyp)
                trial["char_error_rate"] = scorer.jiwer.cer(ref, trial_hyp)
            if "trials" in row:
                row["word_error_rate"] = sum(t["word_error_rate"] for t in row["trials"]) / len(row["trials"])
                row["char_error_rate"] = sum(t["char_error_rate"] for t in row["trials"]) / len(row["trials"])
                row["transcripts_identical"] = len({t["text"] for t in row["trials"]}) == 1
        summary["backends"].append(data)
        for case in ("raw_full", "pipeline_full", "pipeline_progressive"):
            rows = [r for r in data["rows"] if r["case"] == case]
            if len(rows) != len(inputs["clips"]):
                continue
            ref = [references[r["clip"]] for r in rows for t in r["trials"]]
            hyp = [scorer.normalize_for_word_accuracy(t["text"]) for r in rows for t in r["trials"]]
            aggregate = {"backend": name, "case": case,
                         "corpus_word_error_rate": scorer.jiwer.wer(ref, hyp),
                         "accuracy_scope": "Pooled across all three measured repetitions of the same three recordings",
                         "mean_clip_word_error_rate": sum(r["word_error_rate"] for r in rows) / len(rows),
                         "total_median_seconds": sum(r["median_seconds"] for r in rows),
                         "total_audio_seconds": sum(r["audio_seconds"] for r in rows)}
            aggregate["times_realtime"] = aggregate["total_audio_seconds"] / aggregate["total_median_seconds"]
            summary["aggregate_rows"].append(aggregate)
            print(name, case, "corpus WER", round(aggregate["corpus_word_error_rate"] * 100, 2),
                  "total seconds", round(aggregate["total_median_seconds"], 3))
    write_json(directory / "comparison.json", summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "summarize"))
    parser.add_argument("--directory", type=Path, default=ROOT / "build/asr-compare")
    parser.add_argument("--backend", choices=("futo-base", "futo-tiny", "redux", "phonon"))
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--realtime", action="store_true")
    args = parser.parse_args()
    if args.threads < 1 or args.runs < 1:
        parser.error("threads and runs must be positive")
    if args.action == "prepare":
        prepare(args.directory)
    elif args.action == "summarize":
        summarize(args.directory)
    elif not args.backend:
        parser.error("run requires --backend")
    else:
        run(args)


if __name__ == "__main__":
    main()
