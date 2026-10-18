#!/usr/bin/env python3
"""Compare saved real-time runs against the seven approved repo references."""
import argparse
import importlib.util
import json
from pathlib import Path
import statistics
import sys

import jiwer

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, default=ROOT / "build/asr-compare/tail-latency")
    parser.add_argument("--after", type=Path, default=ROOT / "build/accuracy-latency/real-time-300")
    parser.add_argument("--out", type=Path, default=ROOT / "build/accuracy-latency/results-300.json")
    args = parser.parse_args()
    bench = load("tail_bench", ROOT / "scripts/benchmark-tail-latency.py")
    scorer = load("repo_scorer", ROOT / "tst/data/scripts/score.py")
    recordings = bench.approved_recordings()
    approved = {str(path.relative_to(ROOT)): digest for path, digest in recordings}
    references = {}
    for path, _ in recordings:
        ref = (ROOT / f"tst/data/text/{path.stem}.txt" if path.parent == ROOT / "tst/data"
               else ROOT / f"data/mine/text/short/{path.parent.name}.txt")
        references[str(path.relative_to(ROOT))] = scorer.normalize_for_word_accuracy(ref.read_text())
    report = {"metric": "text_ready - input_last_frame", "device": "cpu", "threads": 4,
              "recordings": list(approved), "references": "Existing repo transcripts; no manual correction",
              "protocol": "Warm model; one real-time pass per file and backend. WER pools all reference words. No mic or typing.",
              "rows": []}
    for backend in ("futo-base", "futo-tiny", "redux", "phonon"):
        result = {"backend": backend}
        for label, directory in (("before", args.before), ("after", args.after)):
            file = directory / f"{backend}.json"
            if not file.exists():
                break
            data = json.loads(file.read_text())
            if {r["recording"]: r["sha256"] for r in data["rows"]} != approved or len(data["rows"]) != 7:
                break  # A benchmark in progress is not a complete result.
            if data["device"] != "cpu" or data["threads"] != 4 or data["microphone"] or data["typing"]:
                raise ValueError(f"Unexpected benchmark configuration: {file}")
            if not data["realtime_feed"] or any(r["mode"] != "progressive" or r["status"] != "completed"
                                                for r in data["rows"]):
                raise ValueError(f"Expected completed real-time progressive runs: {file}")
            refs = [references[r["recording"]] for r in data["rows"]]
            hyps = [scorer.normalize_for_word_accuracy(r["text"]) for r in data["rows"]]
            delays = [r["audio_end_to_text_seconds"] * 1000 for r in data["rows"]]
            alignment = jiwer.process_words(refs, hyps)
            result[label] = {"wer": alignment.wer,
                             "reference_words": alignment.hits + alignment.substitutions + alignment.deletions,
                             "substitutions": alignment.substitutions, "deletions": alignment.deletions,
                             "insertions": alignment.insertions, "median_ms": statistics.median(delays),
                             "mean_ms": statistics.mean(delays), "worst_ms": max(delays),
                             "inference_seconds": sum(r["inference_seconds"] for r in data["rows"]),
                             "per_file": [{"recording": r["recording"], "wer": jiwer.wer(a, b), "delay_ms": d,
                                           "text": r["text"]} for r, a, b, d in zip(data["rows"], refs, hyps, delays)]}
        if "after" in result:
            report["rows"].append(result)
            a, b = result["before"], result["after"]
            print(f"{backend:<12} WER {a['wer']*100:5.2f}% -> {b['wer']*100:5.2f}% | median {a['median_ms']:6.1f} -> {b['median_ms']:6.1f} ms | worst {a['worst_ms']:6.1f} -> {b['worst_ms']:6.1f} ms")
    report["complete"] = len(report["rows"]) == 4
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
