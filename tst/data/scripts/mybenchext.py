#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

HEADER_RE = re.compile(
  r"^---- backend: (?P<backend>.+?) -- input: (?P<input_file>.+?) -- model: (?P<model>.+?) ----$"
)
TIMING_RE = re.compile(
  r"^whisper_print_timings:\s+"
  r"(?P<name>[a-z]+) time\s+=\s+"
  r"(?P<ms>[0-9.]+) ms"
  r"(?:\s+/\s+(?P<runs>[0-9]+) runs \(\s*(?P<ms_per_run>[0-9.]+) ms per run\))?$"
)
FALLBACKS_RE = re.compile(
  r"^whisper_print_timings:\s+fallbacks\s+=\s+(?P<p>[0-9]+) p /\s+(?P<h>[0-9]+) h$"
)
START_RE = re.compile(
  r"^start: (?P<value>.+)$")
END_RE = re.compile(
  r"^end: (?P<value>.+)$")
RUNTIME_RE = re.compile(
  r"^total runtime: (?P<value>[0-9.]+) seconds$"
)
VARIANT_RE = re.compile(
  r"^variant: (?P<value>.+)$")


@dataclass
class Context:
  backend: str
  input_file: str
  model: str


@dataclass
class ExecutionRecord:
  bench_name: str
  source_log: str
  run_id: str
  backend: str
  input_file: str
  model: str
  model_family: str
  variant: str | None
  audio_ctx_enabled: bool
  command: str
  start_time: str | None
  end_time: str | None
  runtime_seconds: float | None
  output_text: str
  timings_ms: dict[
    str,
    float | None] = field(
      default_factory = dict)
  fallbacks: dict[
    str, int | None] = field(
      default_factory = dict)
  timing_runs: dict[
    str, int | None] = field(
      default_factory = dict)
  timing_ms_per_run: dict[
    str,
    float | None] = field(
      default_factory = dict)


@dataclass
class ExecutionBuilder:
  context: Context
  command: str
  variant: str | None = None
  start_time: str | None = None
  end_time: str | None = None
  runtime_seconds: float | None = None
  output_lines: list[
    str] = field(
      default_factory = list)
  timings_ms: dict[
    str,
    float | None] = field(
      default_factory = dict)
  fallbacks: dict[
    str, int | None] = field(
      default_factory = lambda:
      {
        "p": None,
        "h": None
      })
  timing_runs: dict[
    str, int | None] = field(
      default_factory = dict)
  timing_ms_per_run: dict[
    str,
    float | None] = field(
      default_factory = dict)
  in_transcript: bool = False
  transcript_finished: bool = False

  def to_record(
    self, source_log: Path,
    bench_name: str
  ) -> ExecutionRecord:
    variant = self.variant
    audio_ctx_enabled = "--audio-ctx" in self.command
    if variant is None and self.context.model.startswith(
        "acft."):
      variant = "with-audio-ctx" if audio_ctx_enabled else "without-audio-ctx"
    run_variant = variant or "default"
    output_text = "\n".join(
      self.output_lines
    ).strip()
    return ExecutionRecord(
      bench_name = bench_name,
      source_log = source_log.
      name,
      run_id =
      f"{self.context.backend}|{self.context.input_file}|{self.context.model}|{run_variant}",
      backend = self.context.
      backend,
      input_file = self.
      context.input_file,
      model = self.context.
      model,
      model_family = "acft"
      if self.context.model.
      startswith("acft.") else
      "standard",
      variant = variant,
      audio_ctx_enabled =
      audio_ctx_enabled,
      command = self.command,
      start_time = self.
      start_time,
      end_time = self.
      end_time,
      runtime_seconds = self.
      runtime_seconds,
      output_text =
      output_text,
      timings_ms = self.
      timings_ms,
      fallbacks = self.
      fallbacks,
      timing_runs = self.
      timing_runs,
      timing_ms_per_run = self
      .timing_ms_per_run,
    )


def parse_args(
) -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description =
    "Extract structured benchmark records from my-bench.log without modifying the source log."
  )
  parser.add_argument(
    "--log-file",
    type = Path,
    default = Path(
      "my-bench.log"),
    help =
    "Path to the bench log to read.",
  )
  parser.add_argument(
    "--bench-name",
    default = "first-bench",
    help =
    "Bench name stored in extracted records.",
  )
  parser.add_argument(
    "--jsonl-out",
    type = Path,
    default = Path(
      "benchmark_tests_extracted.jsonl"
    ),
    help =
    "Output path for canonical JSONL data.",
  )
  parser.add_argument(
    "--csv-out",
    type = Path,
    default = Path(
      "benchmark_tests_extracted.csv"
    ),
    help =
    "Output path for flattened CSV data.",
  )
  parser.add_argument(
    "--summary-out",
    type = Path,
    default = Path(
      "benchmark_extraction_summary.json"
    ),
    help =
    "Output path for extraction summary JSON.",
  )
  parser.add_argument(
    "--dry-run",
    action = "store_true",
    help =
    "Parse and validate without writing output files.",
  )
  parser.add_argument(
    "--expected-records",
    type = int,
    default = 486,
    help =
    "Expected extracted execution count for validation.",
  )
  return parser.parse_args()


def ensure_readable_log(
    path: Path) -> None:
  if not path.exists():
    raise FileNotFoundError(
      f"missing log file: {path}"
    )
  if not path.is_file():
    raise FileNotFoundError(
      f"log path is not a file: {path}"
    )


def parse_log(
  log_path: Path,
  bench_name: str
) -> tuple[
    list[ExecutionRecord],
    dict[str, Any]]:
  records: list[
    ExecutionRecord] = []
  warnings: list[str] = []
  current_context: Context | None = None
  current_execution: ExecutionBuilder | None = None
  header_count = 0

  with log_path.open(
      "r", encoding = "utf-8"
  ) as handle:
    for line_number, raw_line in enumerate(
        handle, start = 1):
      line = raw_line.rstrip(
        "\n")

      header_match = HEADER_RE.match(
        line)
      if header_match:
        if current_execution is not None:
          warnings.append(
            f"line {line_number}: new header before previous execution completed"
          )
          records.append(
            current_execution.
            to_record(
              log_path,
              bench_name))
          current_execution = None

        current_context = Context(
          backend = header_match
          .group("backend"),
          input_file =
          header_match.group(
            "input_file"),
          model = header_match
          .group("model"),
        )
        header_count += 1
        continue

      if current_execution is None and line.startswith(
          "calling: "):
        if current_context is None:
          warnings.append(
            f"line {line_number}: calling line without active header"
          )
          continue
        current_execution = ExecutionBuilder(
          context =
          current_context,
          command = line.
          removeprefix(
            "calling: "),
        )
        continue

      if current_execution is None:
        continue

      if line.startswith(
          "main: processing "
      ):
        current_execution.in_transcript = True
        current_execution.transcript_finished = False
        continue

      timing_match = TIMING_RE.match(
        line)
      if timing_match:
        current_execution.in_transcript = False
        current_execution.transcript_finished = True
        name = timing_match.group(
          "name")
        current_execution.timings_ms[
          name] = float(
            timing_match.
            group("ms"))
        runs = timing_match.group(
          "runs")
        ms_per_run = timing_match.group(
          "ms_per_run")
        current_execution.timing_runs[
          name] = int(
            runs
          ) if runs is not None else None
        current_execution.timing_ms_per_run[
          name] = (
            float(ms_per_run)
            if ms_per_run
            is not None else
            None)
        continue

      fallbacks_match = FALLBACKS_RE.match(
        line)
      if fallbacks_match:
        current_execution.in_transcript = False
        current_execution.transcript_finished = True
        current_execution.fallbacks[
          "p"] = int(
            fallbacks_match.
            group("p"))
        current_execution.fallbacks[
          "h"] = int(
            fallbacks_match.
            group("h"))
        continue

      variant_match = VARIANT_RE.match(
        line)
      if variant_match:
        current_execution.variant = variant_match.group(
          "value")
        continue

      start_match = START_RE.match(
        line)
      if start_match:
        current_execution.start_time = start_match.group(
          "value")
        continue

      end_match = END_RE.match(
        line)
      if end_match:
        current_execution.end_time = end_match.group(
          "value")
        continue

      runtime_match = RUNTIME_RE.match(
        line)
      if runtime_match:
        current_execution.runtime_seconds = float(
          runtime_match.group(
            "value"))
        continue

      if line.startswith(
          "complete: "):
        records.append(
          current_execution.
          to_record(
            log_path,
            bench_name))
        current_execution = None
        continue

      if current_execution.in_transcript and not current_execution.transcript_finished:
        current_execution.output_lines.append(
          line)

  if current_execution is not None:
    warnings.append(
      "end of file reached before current execution completed"
    )
    records.append(
      current_execution.
      to_record(log_path,
                bench_name))

  summary = build_summary(
    records, header_count,
    warnings)
  return records, summary


def build_summary(
  records: list[
    ExecutionRecord],
  header_count: int,
  warnings: list[str]
) -> dict[str, Any]:
  backend_counts: dict[
    str, int] = {}
  input_counts: dict[
    str, int] = {}
  variant_counts: dict[
    str, int] = {}
  missing_fields: list[
    str] = []
  duplicate_run_ids: list[
    str] = []
  seen_run_ids: set[
    str] = set()

  for record in records:
    backend_counts[
      record.
      backend] = backend_counts.get(
        record.backend, 0) + 1
    input_counts[
      record.
      input_file] = input_counts.get(
        record.input_file,
        0) + 1
    variant_key = record.variant or "default"
    variant_counts[
      variant_key] = variant_counts.get(
        variant_key, 0) + 1

    if record.run_id in seen_run_ids:
      duplicate_run_ids.append(
        record.run_id)
    seen_run_ids.add(
      record.run_id)

    required = {
      "backend":
      record.backend,
      "input_file":
      record.input_file,
      "model":
      record.model,
      "command":
      record.command,
      "start_time":
      record.start_time,
      "end_time":
      record.end_time,
      "runtime_seconds":
      record.runtime_seconds,
    }
    for field_name, value in required.items(
    ):
      if value in (None, ""):
        missing_fields.append(
          f"{record.run_id}:{field_name}"
        )

    if record.model.startswith(
        "acft."
    ) and record.variant is None:
      missing_fields.append(
        f"{record.run_id}:variant"
      )

    if not record.model.startswith(
        "acft."
    ) and record.variant is not None:
      warnings.append(
        f"unexpected variant on non-acft model: {record.run_id}"
      )

  return {
    "record_count":
    len(records),
    "outer_header_count":
    header_count,
    "backend_counts":
    backend_counts,
    "input_counts":
    input_counts,
    "variant_counts":
    variant_counts,
    "duplicate_run_ids":
    duplicate_run_ids,
    "missing_fields":
    missing_fields,
    "warnings": warnings,
  }


def write_jsonl(
  path: Path, records: list[
    ExecutionRecord]
) -> None:
  with path.open(
      "w", encoding = "utf-8"
  ) as handle:
    for record in records:
      handle.write(
        json.dumps(
          asdict(record),
          ensure_ascii = False
        ) + "\n")


def flatten_record(
  record: ExecutionRecord
) -> dict[str, Any]:
  flat: dict[str, Any] = {
    "bench_name":
    record.bench_name,
    "source_log":
    record.source_log,
    "run_id":
    record.run_id,
    "backend":
    record.backend,
    "input_file":
    record.input_file,
    "model":
    record.model,
    "model_family":
    record.model_family,
    "variant":
    record.variant,
    "audio_ctx_enabled":
    record.audio_ctx_enabled,
    "command":
    record.command,
    "start_time":
    record.start_time,
    "end_time":
    record.end_time,
    "runtime_seconds":
    record.runtime_seconds,
    "output_text":
    record.output_text,
    "fallbacks_p":
    record.fallbacks.get("p"),
    "fallbacks_h":
    record.fallbacks.get("h"),
  }
  for key, value in record.timings_ms.items(
  ):
    flat[f"{key}_ms"] = value
  for key, value in record.timing_runs.items(
  ):
    flat[
      f"{key}_runs"] = value
  for key, value in record.timing_ms_per_run.items(
  ):
    flat[
      f"{key}_ms_per_run"] = value
  return flat


def write_csv(
  path: Path, records: list[
    ExecutionRecord]
) -> None:
  rows = [
    flatten_record(record)
    for record in records
  ]
  fieldnames: list[str] = []
  for row in rows:
    for key in row:
      if key not in fieldnames:
        fieldnames.append(key)
  with path.open(
      "w",
      encoding = "utf-8",
      newline = "") as handle:
    writer = csv.DictWriter(
      handle,
      fieldnames = fieldnames)
    writer.writeheader()
    writer.writerows(rows)


def write_summary(
  path: Path,
  summary: dict[str, Any]
) -> None:
  with path.open(
      "w", encoding = "utf-8"
  ) as handle:
    json.dump(
      summary,
      handle,
      indent = 2,
      ensure_ascii = False)
    handle.write("\n")


def validate_summary(
  summary: dict[str, Any],
  expected_records: int
) -> list[str]:
  errors: list[str] = []
  if summary[
      "record_count"] != expected_records:
    errors.append(
      f"expected {expected_records} records but parsed {summary['record_count']}"
    )
  if summary[
      "duplicate_run_ids"]:
    errors.append(
      f"found duplicate run ids: {len(summary['duplicate_run_ids'])}"
    )
  if summary[
      "missing_fields"]:
    errors.append(
      f"found missing required fields: {len(summary['missing_fields'])}"
    )
  return errors


def print_summary(
  summary: dict[str, Any]
) -> None:
  print(
    f"record_count={summary['record_count']}"
  )
  print(
    f"outer_header_count={summary['outer_header_count']}"
  )
  print(
    f"backend_counts={json.dumps(summary['backend_counts'], sort_keys=True)}"
  )
  print(
    f"input_counts={json.dumps(summary['input_counts'], sort_keys=True)}"
  )
  print(
    f"variant_counts={json.dumps(summary['variant_counts'], sort_keys=True)}"
  )
  print(
    f"duplicate_run_ids={len(summary['duplicate_run_ids'])}"
  )
  print(
    f"missing_fields={len(summary['missing_fields'])}"
  )
  print(
    f"warnings={len(summary['warnings'])}"
  )


def main() -> int:
  args = parse_args()
  ensure_readable_log(
    args.log_file)
  records, summary = parse_log(
    args.log_file,
    args.bench_name)
  errors = validate_summary(
    summary,
    args.expected_records)
  print_summary(summary)

  if args.dry_run:
    for error in errors:
      print(error,
            file = sys.stderr)
    return 1 if errors else 0

  write_jsonl(args.jsonl_out,
              records)
  write_csv(args.csv_out,
            records)
  write_summary(
    args.summary_out, summary)

  for error in errors:
    print(error,
          file = sys.stderr)
  return 1 if errors else 0


if __name__ == "__main__":
  raise SystemExit(main())
