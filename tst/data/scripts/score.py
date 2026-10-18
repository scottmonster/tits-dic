#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import re
import string
import wave
from collections import defaultdict
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import contractions
import jiwer

PUNCT_TRANSLATOR = str.maketrans(
  "", "", string.punctuation)
SPECIAL_CHAR_TRANSLATIONS = str.maketrans(
  {
    "’": "'",
    "‘": "'",
    "“": '"',
    "”": '"',
    "-": " ",
    "–": " ",
    "—": " ",
  })
PUNCTUATION_SET = set(
  string.punctuation)
TRACKED_PUNCTUATION = {
  ".",
  "?",
  "!",
  ",",
  "'",
  "-",
}
PUNCTUATION_TRANSLATIONS = str.maketrans(
  {
    "’": "'",
    "‘": "'",
    "–": "-",
    "—": "-",
  })
ALIGNMENT_WORD_RE = re.compile(
  r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)*")
RUNTIME_COMPONENT_KEYS = (
  "mel",
  "sample",
  "encode",
  "decode",
  "batchd",
  "prompt",
)


@dataclass(frozen = True)
class WordToken:
  word: str
  trailing_punctuation: tuple[str, ...]


@dataclass(frozen = True)
class PunctuationScore:
  punctuation_error_rate: float
  punctuation_errors: int
  punctuation_substitutions: int
  punctuation_deletions: int
  punctuation_insertions: int


@dataclass
class ScoreResult:
  run_id: str
  backend: str
  input_file: str
  model: str
  model_family: str
  model_base_name: str
  model_size: str
  language_variant: str
  quantization: str
  is_quantized: bool
  variant: str | None
  audio_ctx_enabled: bool
  runtime_seconds: float | None
  audio_duration_seconds: float
  realtime_factor: float | None
  realtime_speed: float | None
  reference_file: str
  word_error_rate: float
  char_error_rate: float
  exact_match_raw: bool
  exact_match_normalized: bool
  punctuation_error_rate: float
  punctuation_errors: int
  punctuation_substitutions: int
  punctuation_deletions: int
  punctuation_insertions: int
  capitalization_accuracy: float
  reference_text_normalized: str
  hypothesis_text_normalized: str


def parse_args(
) -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description =
    "Score extracted ASR runs against reference transcripts."
  )
  parser.add_argument(
    "--input-jsonl",
    type = Path,
    default = Path(
      "data/extracted_data/benchmark_tests_extracted.jsonl"
    ),
    help =
    "Path to extracted benchmark JSONL data.",
  )
  parser.add_argument(
    "--reference-dir",
    type = Path,
    default = Path(
      "data/text"),
    help =
    "Directory containing reference transcript text files.",
  )
  parser.add_argument(
    "--audio-dir",
    type = Path,
    default = Path("data"),
    help =
    "Directory containing wav inputs used for scoring speed metrics.",
  )
  parser.add_argument(
    "--jsonl-out",
    type = Path,
    default = Path(
      "data/extracted_data/scored_runs.jsonl"
    ),
    help =
    "Canonical scored JSONL output.",
  )
  parser.add_argument(
    "--csv-out",
    type = Path,
    default = Path(
      "data/extracted_data/scored_runs.csv"
    ),
    help =
    "Flattened scored CSV output.",
  )
  parser.add_argument(
    "--summary-out",
    type = Path,
    default = Path(
      "data/extracted_data/scoring_summary.json"
    ),
    help =
    "Summary JSON output.",
  )
  parser.add_argument(
    "--dry-run",
    action = "store_true",
    help =
    "Score inputs and print summary without writing output files.",
  )
  return parser.parse_args()


def normalize_for_word_accuracy(
    text: str) -> str:
  normalized = text.translate(
    SPECIAL_CHAR_TRANSLATIONS)
  normalized = cast(
    str,
    contractions.fix(
      normalized))
  normalized = normalized.lower(
  )
  normalized = normalized.translate(
    PUNCT_TRANSLATOR)
  normalized = re.sub(
    r"\s+", " ",
    normalized).strip()
  return normalized


def normalize_raw_text(
    text: str) -> str:
  return re.sub(r"\s+", " ",
                text.strip())


def normalize_alignment_word(
    text: str) -> str:
  normalized = text.translate(
    PUNCTUATION_TRANSLATIONS)
  normalized = normalized.lower(
  )
  normalized = normalized.replace(
    "'", "")
  return normalized


def tokenize_words_with_punctuation(
    text: str) -> list[WordToken]:
  translated = text.translate(
    PUNCTUATION_TRANSLATIONS)
  matches = list(
    ALIGNMENT_WORD_RE.finditer(
      translated))
  tokens: list[WordToken] = []

  for index, match in enumerate(
      matches):
    raw_word = match.group(0)
    word = normalize_alignment_word(
      raw_word)
    if not word:
      continue

    segment_end = len(
      translated) if index + 1 >= len(
        matches) else matches[
          index + 1].start()
    trailing_segment = translated[
      match.end():segment_end]
    trailing_punctuation = tuple(
      char for char in trailing_segment
      if char in TRACKED_PUNCTUATION
    )
    tokens.append(
      WordToken(
        word = word,
        trailing_punctuation =
        trailing_punctuation,
      ))

  return tokens


@lru_cache(maxsize = None)
def align_word_indices(
  reference_words: tuple[str, ...],
  hypothesis_words: tuple[str, ...]
) -> tuple[tuple[int | None,
                 int | None], ...]:
  ref_count = len(
    reference_words)
  hyp_count = len(
    hypothesis_words)
  dp = [
    [0] * (hyp_count + 1)
    for _ in range(
      ref_count + 1)
  ]

  for ref_index in range(
      ref_count + 1):
    dp[ref_index][0] = ref_index
  for hyp_index in range(
      hyp_count + 1):
    dp[0][hyp_index] = hyp_index

  for ref_index in range(
      1, ref_count + 1):
    for hyp_index in range(
        1, hyp_count + 1):
      substitution_cost = 0 if reference_words[
        ref_index - 1] == hypothesis_words[
          hyp_index - 1] else 1
      dp[ref_index][hyp_index] = min(
        dp[ref_index - 1][
          hyp_index] + 1,
        dp[ref_index][
          hyp_index - 1] + 1,
        dp[ref_index - 1][
          hyp_index - 1] + substitution_cost,
      )

  alignments: list[tuple[int | None,
                         int | None]] = []
  ref_index = ref_count
  hyp_index = hyp_count

  while ref_index > 0 or hyp_index > 0:
    if ref_index > 0 and hyp_index > 0:
      substitution_cost = 0 if reference_words[
        ref_index - 1] == hypothesis_words[
          hyp_index - 1] else 1
      if dp[ref_index][hyp_index] == dp[
          ref_index - 1][hyp_index - 1] + substitution_cost:
        alignments.append(
          (ref_index - 1,
           hyp_index - 1))
        ref_index -= 1
        hyp_index -= 1
        continue
    if ref_index > 0 and dp[
        ref_index][hyp_index] == dp[
          ref_index - 1][hyp_index] + 1:
      alignments.append(
        (ref_index - 1,
         None))
      ref_index -= 1
      continue
    alignments.append(
      (None,
       hyp_index - 1))
    hyp_index -= 1

  alignments.reverse()
  return tuple(alignments)


def punctuation_edits(
  reference_punctuation: tuple[
    str, ...],
  hypothesis_punctuation: tuple[
    str, ...]
) -> tuple[int, int, int]:
  alignments = align_word_indices(
    reference_punctuation,
    hypothesis_punctuation)
  substitutions = 0
  deletions = 0
  insertions = 0

  for reference_index, hypothesis_index in alignments:
    if reference_index is None:
      insertions += 1
      continue
    if hypothesis_index is None:
      deletions += 1
      continue
    if reference_punctuation[
        reference_index] != hypothesis_punctuation[
          hypothesis_index]:
      substitutions += 1

  return substitutions, deletions, insertions


def punctuation_score(
  reference: str,
  hypothesis: str
) -> PunctuationScore:
  reference_tokens = tokenize_words_with_punctuation(
    reference)
  hypothesis_tokens = tokenize_words_with_punctuation(
    hypothesis)
  reference_words = tuple(
    token.word for token in
    reference_tokens)
  hypothesis_words = tuple(
    token.word for token in
    hypothesis_tokens)
  alignments = align_word_indices(
    reference_words,
    hypothesis_words)
  substitutions = 0
  deletions = 0
  insertions = 0

  for reference_index, hypothesis_index in alignments:
    if reference_index is None:
      hypothesis_token = hypothesis_tokens[
        cast(int,
             hypothesis_index)]
      insertions += len(
        hypothesis_token.
        trailing_punctuation)
      continue
    if hypothesis_index is None:
      reference_token = reference_tokens[
        cast(int,
             reference_index)]
      deletions += len(
        reference_token.
        trailing_punctuation)
      continue

    reference_token = reference_tokens[
      reference_index]
    hypothesis_token = hypothesis_tokens[
      hypothesis_index]
    token_substitutions, token_deletions, token_insertions = punctuation_edits(
      reference_token.
      trailing_punctuation,
      hypothesis_token.
      trailing_punctuation,
    )
    substitutions += token_substitutions
    deletions += token_deletions
    insertions += token_insertions

  error_count = substitutions + deletions + insertions
  reference_word_count = len(
    reference_tokens)
  error_rate = 0.0
  if reference_word_count:
    error_rate = error_count / reference_word_count

  return PunctuationScore(
    punctuation_error_rate =
    error_rate,
    punctuation_errors =
    error_count,
    punctuation_substitutions =
    substitutions,
    punctuation_deletions =
    deletions,
    punctuation_insertions =
    insertions,
  )


def derive_runtime_seconds(
    entry: dict[str, Any]
) -> float | None:
  timings = entry.get(
    "timings_ms")
  if not isinstance(
      timings, dict):
    return None

  runtime_ms = 0.0
  found_component = False
  for key in RUNTIME_COMPONENT_KEYS:
    value = timings.get(key)
    if value is None:
      continue
    runtime_ms += float(
      value)
    found_component = True

  if not found_component:
    return None
  return runtime_ms / 1000.0


def capitalization_accuracy(
    reference: str,
    hypothesis: str) -> float:
  reference_letters = [
    char for char in reference
    if char.isalpha()
  ]
  hypothesis_letters = [
    char
    for char in hypothesis
    if char.isalpha()
  ]
  comparable = 0
  matches = 0

  for reference_char, hypothesis_char in zip(
      reference_letters,
      hypothesis_letters):
    if reference_char.lower(
    ) != hypothesis_char.lower(
    ):
      continue
    comparable += 1
    if reference_char.isupper(
    ) == hypothesis_char.isupper(
    ):
      matches += 1

  if comparable == 0:
    return 1.0
  return matches / comparable


def parse_model_metadata(
  model: str
) -> dict[str, Any]:
  model_name = model.removeprefix(
    "acft.")
  parts = model_name.split(
    ".")
  model_size = parts[0]
  language_variant = "en" if "en" in parts[
    1:] else "multilingual"
  quantization = next(
    (part
     for part in parts[1:]
     if part.startswith("q")),
    "none")
  model_base_name = model_name
  if quantization != "none":
    model_base_name = model_base_name.rsplit(
      f".{quantization}",
      1)[0]
  return {
    "model_base_name":
    model_base_name,
    "model_size":
    model_size,
    "language_variant":
    language_variant,
    "quantization":
    quantization,
    "is_quantized":
    quantization != "none",
  }


def audio_duration_seconds(
    audio_path: Path
) -> float:
  with wave.open(
      str(audio_path),
      "rb") as wav_file:
    frames = wav_file.getnframes(
    )
    frame_rate = wav_file.getframerate(
    )
  return frames / frame_rate


def load_jsonl(
  path: Path
) -> list[dict[str, Any]]:
  with path.open(
      "r", encoding = "utf-8"
  ) as handle:
    return [
      json.loads(line)
      for line in handle
      if line.strip()
    ]


def score_entry(
  entry: dict[str, Any],
  reference_dir: Path,
  audio_dir: Path
) -> ScoreResult:
  input_file = entry[
    "input_file"]
  reference_path = reference_dir / Path(
    input_file).with_suffix(
      ".txt")
  if not reference_path.exists(
  ):
    raise FileNotFoundError(
      f"missing reference transcript for {input_file}: {reference_path}"
    )

  audio_path = audio_dir / input_file
  if not audio_path.exists():
    raise FileNotFoundError(
      f"missing audio file for {input_file}: {audio_path}"
    )

  reference_text = reference_path.read_text(
    encoding = "utf-8").strip(
    )
  hypothesis_text = (
    entry.get("output_text")
    or "").strip()
  reference_normalized = normalize_for_word_accuracy(
    reference_text)
  hypothesis_normalized = normalize_for_word_accuracy(
    hypothesis_text)

  duration_seconds = audio_duration_seconds(
    audio_path)
  runtime_seconds = derive_runtime_seconds(
    entry)
  realtime_factor = None
  audio_speed = None
  if runtime_seconds is not None and duration_seconds:
    realtime_factor = runtime_seconds / duration_seconds
    if runtime_seconds != 0:
      audio_speed = duration_seconds / runtime_seconds

  punctuation = punctuation_score(
    reference_text,
    hypothesis_text)
  metadata = parse_model_metadata(
    entry["model"])

  return ScoreResult(
    run_id = entry["run_id"],
    backend = entry[
      "backend"],
    input_file = input_file,
    model = entry["model"],
    model_family = entry[
      "model_family"],
    model_base_name = metadata[
      "model_base_name"],
    model_size = metadata[
      "model_size"],
    language_variant =
    metadata[
      "language_variant"],
    quantization = metadata[
      "quantization"],
    is_quantized = metadata[
      "is_quantized"],
    variant = entry.get(
      "variant"),
    audio_ctx_enabled = entry[
      "audio_ctx_enabled"],
    runtime_seconds =
    runtime_seconds,
    audio_duration_seconds =
    duration_seconds,
    realtime_factor =
    realtime_factor,
    realtime_speed
    = audio_speed,
    reference_file = str(
      reference_path),
    word_error_rate = jiwer.
    wer(
      reference_normalized,
      hypothesis_normalized),
    char_error_rate = jiwer.
    cer(
      reference_normalized,
      hypothesis_normalized),
    exact_match_raw =
    normalize_raw_text(
      reference_text) ==
    normalize_raw_text(
      hypothesis_text),
    exact_match_normalized =
    reference_normalized ==
    hypothesis_normalized,
    punctuation_error_rate =
    punctuation.
    punctuation_error_rate,
    punctuation_errors =
    punctuation.
    punctuation_errors,
    punctuation_substitutions =
    punctuation.
    punctuation_substitutions,
    punctuation_deletions =
    punctuation.
    punctuation_deletions,
    punctuation_insertions =
    punctuation.
    punctuation_insertions,
    capitalization_accuracy =
    capitalization_accuracy(
      reference_text,
      hypothesis_text),
    reference_text_normalized
    = reference_normalized,
    hypothesis_text_normalized
    = hypothesis_normalized,
  )


def average(
  values: list[float]
) -> float | None:
  if not values:
    return None
  return sum(values) / len(
    values)


def summarize(
  results: list[ScoreResult]
) -> dict[str, Any]:
  by_backend: dict[str, list[
    ScoreResult]] = defaultdict(
      list)
  by_model: dict[str, list[
    ScoreResult]] = defaultdict(
      list)
  by_variant: dict[str, list[
    ScoreResult]] = defaultdict(
      list)

  for result in results:
    by_backend[
      result.backend].append(
        result)
    by_model[
      result.model].append(
        result)
    by_variant[
      result.variant
      or "default"].append(
        result)

  def summarize_group(
    group: dict[
      str, list[ScoreResult]]
  ) -> dict[str, Any]:
    summary: dict[str,
                  Any] = {}
    for key, records in group.items(
    ):
      summary[key] = {
        "count":
        len(records),
        "avg_word_error_rate":
        average([
          record.
          word_error_rate for
          record in records
        ]),
        "avg_char_error_rate":
        average([
          record.
          char_error_rate for
          record in records
        ]),
        "avg_realtime_factor":
        average([
          record.
          realtime_factor for
          record in records
          if record.
          realtime_factor
          is not None
        ]),
        "avg_punctuation_error_rate":
        average([
          record.
          punctuation_error_rate
          for
          record in records
        ]),
        "avg_capitalization_accuracy":
        average([
          record.
          capitalization_accuracy
          for record in
          records
        ]),
      }
    return summary

  return {
    "scored_run_count":
    len(results),
    "backend_summary":
    summarize_group(
      by_backend),
    "model_summary":
    summarize_group(by_model),
    "variant_summary":
    summarize_group(
      by_variant),
  }


def write_jsonl(
  path: Path,
  rows: list[ScoreResult]
) -> None:
  path.parent.mkdir(
    parents = True,
    exist_ok = True)
  with path.open(
      "w", encoding = "utf-8"
  ) as handle:
    for row in rows:
      handle.write(
        json.dumps(
          asdict(row),
          ensure_ascii = False
        ) + "\n")


def write_csv(
  path: Path,
  rows: list[ScoreResult]
) -> None:
  path.parent.mkdir(
    parents = True,
    exist_ok = True)
  flattened_rows = [
    asdict(row)
    for row in rows
  ]
  fieldnames = list(
    flattened_rows[0].keys()
  ) if flattened_rows else []
  with path.open(
      "w",
      encoding = "utf-8",
      newline = "") as handle:
    writer = csv.DictWriter(
      handle,
      fieldnames = fieldnames)
    writer.writeheader()
    writer.writerows(
      flattened_rows)


def write_summary(
  path: Path,
  summary: dict[str, Any]
) -> None:
  path.parent.mkdir(
    parents = True,
    exist_ok = True)
  with path.open(
      "w", encoding = "utf-8"
  ) as handle:
    json.dump(
      summary,
      handle,
      indent = 2,
      ensure_ascii = False)
    handle.write("\n")


def main() -> int:
  args = parse_args()
  entries = load_jsonl(
    args.input_jsonl)
  results = [
    score_entry(
      entry,
      args.reference_dir,
      args.audio_dir)
    for entry in entries
  ]
  summary = summarize(results)

  print(
    f"scored_run_count={summary['scored_run_count']}"
  )
  print(
    f"backend_summary={json.dumps(summary['backend_summary'], sort_keys=True)}"
  )
  print(
    f"variant_summary={json.dumps(summary['variant_summary'], sort_keys=True)}"
  )

  if args.dry_run:
    return 0

  write_jsonl(args.jsonl_out,
              results)
  write_csv(args.csv_out,
            results)
  write_summary(
    args.summary_out, summary)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
