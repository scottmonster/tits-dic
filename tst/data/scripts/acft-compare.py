#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable


METRIC_FIELDS = [
	"runtime_seconds",
	"realtime_factor",
	"realtime_speed",
	"word_error_rate",
	"char_error_rate",
	"punctuation_error_rate",
	"capitalization_accuracy",
]

TIMING_FIELDS = [
	"mel",
	"sample",
	"encode",
	"decode",
	"batchd",
	"prompt",
]

SUMMARY_FIELDS = [
	"delta_runtime_seconds",
	"relative_runtime_change",
	"delta_word_error_rate",
	"delta_char_error_rate",
	"delta_punctuation_error_rate",
	"delta_capitalization_accuracy",
	"delta_encode_ms",
	"delta_batchd_ms",
	"delta_sample_ms",
	"delta_mel_ms",
	"delta_decode_ms",
	"delta_prompt_ms",
]

DURATION_FIELDS = [
	"delta_runtime_seconds",
	"relative_runtime_change",
	"delta_word_error_rate",
	"delta_char_error_rate",
	"delta_punctuation_error_rate",
]

COMPARISON_AUDIO_CTX = "audio_ctx_only"
COMPARISON_WITH_CTX = "acft_with_ctx_vs_standard"
COMPARISON_WITHOUT_CTX = "acft_without_ctx_vs_standard"

COMPARISON_OUTPUTS = {
	COMPARISON_AUDIO_CTX: "acft_compare_audio_ctx_pairs.jsonl",
	COMPARISON_WITH_CTX: "acft_compare_vs_standard_with_ctx.jsonl",
	COMPARISON_WITHOUT_CTX: "acft_compare_vs_standard_without_ctx.jsonl",
}

SUMMARY_OUTPUT = "acft_compare_summary.json"
DURATION_OUTPUT = "acft_compare_duration_analysis.json"
MANIFEST_OUTPUT = "acft_compare_manifest.json"
INSIGHTS_OUTPUT = "acft_compare_insights.json"

SHORT_CLIP_MAX_SECONDS = 10.0
LONG_CLIP_MIN_SECONDS = 20.0

SERIOUS_ISSUE_TYPES = {
	"no_scored_rows",
	"no_raw_rows",
	"missing_raw_run",
	"zero_matched_rows",
	"duplicate_conflicting_matches",
}


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Build ACFT matched-pair comparison artifacts from scored benchmark data."
	)
	parser.add_argument(
		"--scored-jsonl",
		type=Path,
		default=Path("data/extracted_data/scored_runs.jsonl"),
		help="Canonical scored run input JSONL.",
	)
	parser.add_argument(
		"--raw-jsonl",
		type=Path,
		default=Path("data/extracted_data/benchmark_tests_extracted.jsonl"),
		help="Raw extracted benchmark JSONL with timings_ms.",
	)
	parser.add_argument(
		"--output-dir",
		type=Path,
		default=Path("data/extracted_data/derived"),
		help="Directory for ACFT comparison artifacts.",
	)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Build all outputs in memory and print a concise summary without writing files.",
	)
	return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
	with path.open("r", encoding="utf-8") as handle:
		return [json.loads(line) for line in handle if line.strip()]


def write_json(path: Path, payload: Any) -> None:
	path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
	with path.open("w", encoding="utf-8") as handle:
		for row in rows:
			handle.write(json.dumps(row, sort_keys=True) + "\n")


def normalize_model_key(record: dict[str, Any]) -> str:
	model = str(record["model"])
	if record.get("model_family") == "acft" and model.startswith("acft."):
		return model.removeprefix("acft.")
	return model


def metric_stats(values: list[float]) -> dict[str, float] | None:
	if not values:
		return None
	ordered = sorted(values)
	return {
		"count": len(ordered),
		"mean": mean(ordered),
		"median": median(ordered),
		"min": ordered[0],
		"max": ordered[-1],
	}


def add_issue(issues: list[dict[str, Any]], issue_type: str, message: str, **details: Any) -> None:
	issue = {
		"issue_type": issue_type,
		"severity": "serious" if issue_type in SERIOUS_ISSUE_TYPES else "warning",
		"message": message,
	}
	issue.update(details)
	issues.append(issue)


def build_index(
	records: list[dict[str, Any]],
	key_fields: list[str],
	issues: list[dict[str, Any]],
	index_name: str,
) -> dict[tuple[Any, ...], dict[str, Any]]:
	grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
	for record in records:
		key = tuple(record.get(field) for field in key_fields)
		grouped[key].append(record)

	indexed: dict[tuple[Any, ...], dict[str, Any]] = {}
	for key, grouped_records in grouped.items():
		if len(grouped_records) > 1:
			add_issue(
				issues,
				"duplicate_conflicting_matches",
				f"Found duplicate rows for {index_name} logical key.",
				index_name=index_name,
				key=list(key),
				run_ids=[record.get("run_id") for record in grouped_records],
			)
			continue
		indexed[key] = grouped_records[0]
	return indexed


def safe_delta(left: Any, right: Any) -> float | None:
	if left is None or right is None:
		return None
	return float(left) - float(right)


def relative_runtime_change(left: dict[str, Any], right: dict[str, Any]) -> float | None:
	right_runtime = right.get("runtime_seconds")
	left_runtime = left.get("runtime_seconds")
	if left_runtime is None or right_runtime in (None, 0):
		return None
	return (float(left_runtime) - float(right_runtime)) / float(right_runtime)


def build_pair_row(
	comparison_type: str,
	model_key: str,
	left: dict[str, Any],
	right: dict[str, Any],
	raw_by_run_id: dict[str, dict[str, Any]],
	issues: list[dict[str, Any]],
) -> dict[str, Any]:
	row: dict[str, Any] = {
		"comparison_type": comparison_type,
		"backend": left.get("backend"),
		"input_file": left.get("input_file"),
		"audio_duration_seconds": left.get("audio_duration_seconds"),
		"model_key": model_key,
		"left_run_id": left.get("run_id"),
		"right_run_id": right.get("run_id"),
		"left_model": left.get("model"),
		"right_model": right.get("model"),
		"left_variant": left.get("variant"),
		"right_variant": right.get("variant"),
		"left_model_family": left.get("model_family"),
		"right_model_family": right.get("model_family"),
	}

	for metric in METRIC_FIELDS:
		row[f"left_{metric}"] = left.get(metric)
		row[f"right_{metric}"] = right.get(metric)
		row[f"delta_{metric}"] = safe_delta(left.get(metric), right.get(metric))

	row["relative_runtime_change"] = relative_runtime_change(left, right)

	left_raw = raw_by_run_id.get(str(left.get("run_id")))
	right_raw = raw_by_run_id.get(str(right.get("run_id")))
	if left_raw is None:
		add_issue(
			issues,
			"missing_raw_run",
			"Missing raw timing row for left scored run used in a comparison.",
			comparison_type=comparison_type,
			run_id=left.get("run_id"),
		)
	if right_raw is None:
		add_issue(
			issues,
			"missing_raw_run",
			"Missing raw timing row for right scored run used in a comparison.",
			comparison_type=comparison_type,
			run_id=right.get("run_id"),
		)

	left_timings = (left_raw or {}).get("timings_ms") if left_raw else None
	right_timings = (right_raw or {}).get("timings_ms") if right_raw else None
	missing_timing_fields: list[str] = []
	for field in TIMING_FIELDS:
		left_value = left_timings.get(field) if isinstance(left_timings, dict) else None
		right_value = right_timings.get(field) if isinstance(right_timings, dict) else None
		row[f"left_{field}_ms"] = left_value
		row[f"right_{field}_ms"] = right_value
		row[f"delta_{field}_ms"] = safe_delta(left_value, right_value)
		if left_value is None or right_value is None:
			missing_timing_fields.append(field)

	if missing_timing_fields:
		add_issue(
			issues,
			"missing_timing_data",
			"Missing timing component data for a matched pair.",
			comparison_type=comparison_type,
			left_run_id=left.get("run_id"),
			right_run_id=right.get("run_id"),
			missing_fields=missing_timing_fields,
		)

	return row


def build_audio_ctx_pairs(
	scored_rows: list[dict[str, Any]],
	raw_by_run_id: dict[str, dict[str, Any]],
	issues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
	acft_rows = [row for row in scored_rows if row.get("model_family") == "acft"]
	with_index = build_index(
		[row for row in acft_rows if row.get("variant") == "with-audio-ctx"],
		["backend", "input_file", "model"],
		issues,
		"audio_ctx_with",
	)
	without_index = build_index(
		[row for row in acft_rows if row.get("variant") == "without-audio-ctx"],
		["backend", "input_file", "model"],
		issues,
		"audio_ctx_without",
	)

	results: list[dict[str, Any]] = []
	for key in sorted(set(with_index) & set(without_index)):
		left = with_index[key]
		right = without_index[key]
		results.append(
			build_pair_row(
				COMPARISON_AUDIO_CTX,
				str(left.get("model")),
				left,
				right,
				raw_by_run_id,
				issues,
			)
		)
	return results


def build_acft_vs_standard_pairs(
	scored_rows: list[dict[str, Any]],
	raw_by_run_id: dict[str, dict[str, Any]],
	issues: list[dict[str, Any]],
	acft_variant: str,
	comparison_type: str,
) -> list[dict[str, Any]]:
	acft_rows = [
		row
		for row in scored_rows
		if row.get("model_family") == "acft" and row.get("variant") == acft_variant
	]
	standard_rows = [row for row in scored_rows if row.get("model_family") == "standard"]

	acft_index = build_index(
		acft_rows,
		["backend", "input_file", "model"],
		issues,
		f"{comparison_type}_acft",
	)
	standard_index = build_index(
		standard_rows,
		["backend", "input_file", "model"],
		issues,
		f"{comparison_type}_standard",
	)

	results: list[dict[str, Any]] = []
	for left in sorted(acft_index.values(), key=lambda row: str(row.get("run_id"))):
		model_key = normalize_model_key(left)
		matching_key = (left.get("backend"), left.get("input_file"), model_key)
		right = standard_index.get(matching_key)
		if right is None:
			continue
		results.append(build_pair_row(comparison_type, model_key, left, right, raw_by_run_id, issues))
	return results


def summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
	summary: dict[str, Any] = {
		"count": len(rows),
	}
	for field in SUMMARY_FIELDS:
		values = [float(row[field]) for row in rows if row.get(field) is not None]
		summary[field] = metric_stats(values)
	return summary


def summarize_grouped(rows: list[dict[str, Any]], field: str) -> dict[str, Any]:
	grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
	for row in rows:
		grouped[str(row.get(field))].append(row)
	return {key: summarize_rows(group_rows) for key, group_rows in sorted(grouped.items())}


def build_summary(all_rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
	flat_rows = [row for rows in all_rows.values() for row in rows]
	return {
		"row_counts": {comparison_type: len(rows) for comparison_type, rows in all_rows.items()},
		"available_inputs": sorted({str(row["input_file"]) for row in flat_rows}),
		"available_backends": sorted({str(row["backend"]) for row in flat_rows}),
		"available_comparable_model_keys": sorted({str(row["model_key"]) for row in flat_rows}),
		"comparison_summaries": {
			comparison_type: summarize_rows(rows) for comparison_type, rows in all_rows.items()
		},
		"grouped_by_input_file": {
			comparison_type: summarize_grouped(rows, "input_file")
			for comparison_type, rows in all_rows.items()
		},
		"grouped_by_backend": {
			comparison_type: summarize_grouped(rows, "backend")
			for comparison_type, rows in all_rows.items()
		},
		"grouped_by_model_key": {
			comparison_type: summarize_grouped(rows, "model_key")
			for comparison_type, rows in all_rows.items()
		},
	}


def classify_duration_trend(duration_rows: list[dict[str, Any]]) -> dict[str, Any]:
	unique_count = len(duration_rows)
	if unique_count < 2:
		return {
			"trend_classification": "insufficient-data",
			"sufficient_for_curve_fit": False,
			"notes": "Fewer than two unique duration values were available.",
		}

	runtime_means = [
		float(row["delta_runtime_seconds"]["mean"])
		for row in duration_rows
		if row.get("delta_runtime_seconds")
	]
	if len(runtime_means) < 2:
		return {
			"trend_classification": "insufficient-data",
			"sufficient_for_curve_fit": False,
			"notes": "Per-duration runtime summaries were incomplete.",
		}

	span = max(runtime_means) - min(runtime_means)
	abs_max = max(abs(value) for value in runtime_means) if runtime_means else 0.0
	if unique_count < 4:
		if abs_max > 0 and abs(runtime_means[0]) > abs(runtime_means[-1]) and span > 0:
			return {
				"trend_classification": "fixed-overhead-like",
				"sufficient_for_curve_fit": False,
				"notes": "The runtime delta magnitude shrinks at longer durations, consistent with a fixed-overhead effect, but there are too few unique durations for a fitted curve.",
			}
		if span <= 0.05:
			return {
				"trend_classification": "weak-duration-dependence",
				"sufficient_for_curve_fit": False,
				"notes": "Runtime deltas stay in a narrow band across the available durations.",
			}
		return {
			"trend_classification": "unclear",
			"sufficient_for_curve_fit": False,
			"notes": "There are too few unique durations to support a stronger claim.",
		}

	return {
		"trend_classification": "unclear",
		"sufficient_for_curve_fit": False,
		"notes": "More duration points are needed before attempting a curve fit.",
	}


def build_duration_analysis(all_rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
	analysis: dict[str, Any] = {}
	for comparison_type, rows in all_rows.items():
		grouped: dict[float, list[dict[str, Any]]] = defaultdict(list)
		for row in rows:
			duration = row.get("audio_duration_seconds")
			if duration is None:
				continue
			grouped[float(duration)].append(row)

		per_duration: list[dict[str, Any]] = []
		for duration, duration_rows in sorted(grouped.items()):
			entry: dict[str, Any] = {
				"audio_duration_seconds": duration,
				"count": len(duration_rows),
			}
			for field in DURATION_FIELDS:
				values = [float(row[field]) for row in duration_rows if row.get(field) is not None]
				entry[field] = metric_stats(values)
			per_duration.append(entry)

		analysis[comparison_type] = {
			"unique_duration_values": sorted(grouped),
			"per_duration": per_duration,
			"interpretation": classify_duration_trend(per_duration),
		}
	return analysis


def model_family_from_model_key(model_key: str) -> str:
	return model_key.split(".", 1)[0]


def summarize_focus_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
	def maybe_mean(field: str) -> float | None:
		values = [float(row[field]) for row in rows if row.get(field) is not None]
		return mean(values) if values else None

	def maybe_median(field: str) -> float | None:
		values = [float(row[field]) for row in rows if row.get(field) is not None]
		return median(values) if values else None

	return {
		"count": len(rows),
		"durations": sorted({float(row["audio_duration_seconds"]) for row in rows}),
		"mean_left_runtime_seconds": maybe_mean("left_runtime_seconds"),
		"mean_right_runtime_seconds": maybe_mean("right_runtime_seconds"),
		"mean_left_word_error_rate": maybe_mean("left_word_error_rate"),
		"mean_right_word_error_rate": maybe_mean("right_word_error_rate"),
		"mean_left_char_error_rate": maybe_mean("left_char_error_rate"),
		"mean_right_char_error_rate": maybe_mean("right_char_error_rate"),
		"mean_delta_runtime_seconds": maybe_mean("delta_runtime_seconds"),
		"median_delta_runtime_seconds": maybe_median("delta_runtime_seconds"),
		"mean_relative_runtime_change": maybe_mean("relative_runtime_change"),
		"mean_delta_word_error_rate": maybe_mean("delta_word_error_rate"),
		"mean_delta_char_error_rate": maybe_mean("delta_char_error_rate"),
		"mean_delta_punctuation_error_rate": maybe_mean("delta_punctuation_error_rate"),
		"mean_delta_capitalization_accuracy": maybe_mean("delta_capitalization_accuracy"),
		"mean_delta_encode_ms": maybe_mean("delta_encode_ms"),
		"mean_delta_batchd_ms": maybe_mean("delta_batchd_ms"),
	}


def top_short_clip_speed_wins(rows: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
	short_rows = [row for row in rows if float(row["audio_duration_seconds"]) <= SHORT_CLIP_MAX_SECONDS]
	grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
	for row in short_rows:
		grouped[str(row["model_key"])] .append(row)

	results: list[dict[str, Any]] = []
	for model_key, group_rows in grouped.items():
		summary = summarize_focus_rows(group_rows)
		results.append(
			{
				"model_key": model_key,
				"model_family": model_family_from_model_key(model_key),
				"left_models": sorted({str(row["left_model"]) for row in group_rows}),
				"backends": sorted({str(row["backend"]) for row in group_rows}),
				"inputs": sorted({str(row["input_file"]) for row in group_rows}),
				**summary,
			}
		)

	results.sort(
		key=lambda item: (
			item["mean_delta_runtime_seconds"] if item["mean_delta_runtime_seconds"] is not None else math.inf,
			item["mean_relative_runtime_change"] if item["mean_relative_runtime_change"] is not None else math.inf,
		)
	)
	return results[:limit]


def top_long_clip_quality_regressions(rows: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
	long_rows = [row for row in rows if float(row["audio_duration_seconds"]) >= LONG_CLIP_MIN_SECONDS]
	grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
	for row in long_rows:
		grouped[str(row["model_key"])] .append(row)

	results: list[dict[str, Any]] = []
	for model_key, group_rows in grouped.items():
		summary = summarize_focus_rows(group_rows)
		results.append(
			{
				"model_key": model_key,
				"model_family": model_family_from_model_key(model_key),
				"left_models": sorted({str(row["left_model"]) for row in group_rows}),
				"backends": sorted({str(row["backend"]) for row in group_rows}),
				"inputs": sorted({str(row["input_file"]) for row in group_rows}),
				**summary,
			}
		)

	results.sort(
		key=lambda item: (
			-(item["mean_delta_word_error_rate"] if item["mean_delta_word_error_rate"] is not None else -math.inf),
			-(item["mean_delta_char_error_rate"] if item["mean_delta_char_error_rate"] is not None else -math.inf),
			-(item["mean_delta_punctuation_error_rate"] if item["mean_delta_punctuation_error_rate"] is not None else -math.inf),
		)
	)
	return results[:limit]


def backend_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
	results: list[dict[str, Any]] = []
	for backend, backend_rows in sorted(summarize_group(rows, "backend").items()):
		short_rows = [row for row in backend_rows if float(row["audio_duration_seconds"]) <= SHORT_CLIP_MAX_SECONDS]
		long_rows = [row for row in backend_rows if float(row["audio_duration_seconds"]) >= LONG_CLIP_MIN_SECONDS]
		short_summary = summarize_focus_rows(short_rows) if short_rows else {"count": 0}
		long_summary = summarize_focus_rows(long_rows) if long_rows else {"count": 0}
		short_runtime = short_summary.get("mean_delta_runtime_seconds")
		long_runtime = long_summary.get("mean_delta_runtime_seconds")
		results.append(
			{
				"backend": backend,
				"all_rows": len(backend_rows),
				"short_clip": {
					**short_summary,
					"helps_left_side": short_runtime is not None and short_runtime < 0,
				},
				"long_clip": {
					**long_summary,
					"helps_left_side": long_runtime is not None and long_runtime < 0,
				},
				"stops_helping_on_long_clips": (
					short_runtime is not None
					and short_runtime < 0
					and long_runtime is not None
					and long_runtime >= 0
				),
			}
		)
	return results


def backend_input_breakdown(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
	results: list[dict[str, Any]] = []
	grouped = summarize_group_by_fields(rows, ["backend", "input_file"])
	for (backend, input_file), slice_rows in sorted(grouped.items()):
		short_rows = [row for row in slice_rows if float(row["audio_duration_seconds"]) <= SHORT_CLIP_MAX_SECONDS]
		long_rows = [row for row in slice_rows if float(row["audio_duration_seconds"]) >= LONG_CLIP_MIN_SECONDS]
		short_summary = summarize_focus_rows(short_rows) if short_rows else {"count": 0}
		long_summary = summarize_focus_rows(long_rows) if long_rows else {"count": 0}
		short_runtime = short_summary.get("mean_delta_runtime_seconds")
		long_runtime = long_summary.get("mean_delta_runtime_seconds")
		results.append(
			{
				"backend": backend,
				"input_file": input_file,
				"all_rows": len(slice_rows),
				"short_clip": {
					**short_summary,
					"helps_left_side": short_runtime is not None and short_runtime < 0,
				},
				"long_clip": {
					**long_summary,
					"helps_left_side": long_runtime is not None and long_runtime < 0,
				},
				"stops_helping_on_long_clips": (
					short_runtime is not None
					and short_runtime < 0
					and long_runtime is not None
					and long_runtime >= 0
				),
			}
		)
	return results


def summarize_group(rows: list[dict[str, Any]], field: str) -> dict[str, list[dict[str, Any]]]:
	grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
	for row in rows:
		grouped[str(row[field])].append(row)
	return grouped


def summarize_group_by_fields(
	rows: list[dict[str, Any]], fields: list[str]
) -> dict[tuple[str, ...], list[dict[str, Any]]]:
	grouped: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
	for row in rows:
		key = tuple(str(row[field]) for field in fields)
		grouped[key].append(row)
	return grouped


def build_insights(all_rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
	return {
		"duration_buckets": {
			"short_clip_max_seconds": SHORT_CLIP_MAX_SECONDS,
			"long_clip_min_seconds": LONG_CLIP_MIN_SECONDS,
		},
		"comparisons": {
			comparison_type: {
				"short_clip_speed_wins": top_short_clip_speed_wins(rows),
				"long_clip_quality_regressions": top_long_clip_quality_regressions(rows),
				"backend_breakdown": backend_breakdown(rows),
				"backend_input_breakdown": backend_input_breakdown(rows),
			}
			for comparison_type, rows in all_rows.items()
		},
	}


def build_manifest(
	args: argparse.Namespace,
	all_rows: dict[str, list[dict[str, Any]]],
	issues: list[dict[str, Any]],
) -> dict[str, Any]:
	flat_rows = [row for rows in all_rows.values() for row in rows]
	return {
		"input_files_used": {
			"scored_jsonl": str(args.scored_jsonl),
			"raw_jsonl": str(args.raw_jsonl),
		},
		"output_files_produced": sorted(list(COMPARISON_OUTPUTS.values()) + [SUMMARY_OUTPUT, DURATION_OUTPUT, INSIGHTS_OUTPUT, MANIFEST_OUTPUT]),
		"counts_by_comparison_type": {comparison_type: len(rows) for comparison_type, rows in all_rows.items()},
		"available_backends": sorted({str(row["backend"]) for row in flat_rows}),
		"available_inputs": sorted({str(row["input_file"]) for row in flat_rows}),
		"available_comparable_model_keys": sorted({str(row["model_key"]) for row in flat_rows}),
		"issue_count": len(issues),
	}


def print_summary(
	scored_rows: list[dict[str, Any]],
	raw_rows: list[dict[str, Any]],
	all_rows: dict[str, list[dict[str, Any]]],
	issues: list[dict[str, Any]],
) -> None:
	print(f"scored_row_count={len(scored_rows)}")
	print(f"raw_row_count={len(raw_rows)}")
	print(f"audio_ctx_pair_count={len(all_rows[COMPARISON_AUDIO_CTX])}")
	print(f"acft_with_vs_standard_count={len(all_rows[COMPARISON_WITH_CTX])}")
	print(f"acft_without_vs_standard_count={len(all_rows[COMPARISON_WITHOUT_CTX])}")
	print(f"issue_count={len(issues)}")


def validate_nonempty_inputs(
	scored_rows: list[dict[str, Any]], raw_rows: list[dict[str, Any]], issues: list[dict[str, Any]]
) -> None:
	if not scored_rows:
		add_issue(issues, "no_scored_rows", "No scored rows were loaded.")
	if not raw_rows:
		add_issue(issues, "no_raw_rows", "No raw rows were loaded.")


def validate_pair_counts(all_rows: dict[str, list[dict[str, Any]]], issues: list[dict[str, Any]]) -> None:
	for comparison_type, rows in all_rows.items():
		if not rows:
			add_issue(
				issues,
				"zero_matched_rows",
				"No matched rows were produced for an expected comparison type.",
				comparison_type=comparison_type,
			)


def dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
	seen: set[str] = set()
	result: list[dict[str, Any]] = []
	for issue in issues:
		key = json.dumps(issue, sort_keys=True)
		if key in seen:
			continue
		seen.add(key)
		result.append(issue)
	return result


def serious_issue_count(issues: list[dict[str, Any]]) -> int:
	return sum(1 for issue in issues if issue["severity"] == "serious")


def main() -> int:
	args = parse_args()
	scored_rows = load_jsonl(args.scored_jsonl)
	raw_rows = load_jsonl(args.raw_jsonl)
	issues: list[dict[str, Any]] = []

	validate_nonempty_inputs(scored_rows, raw_rows, issues)
	raw_by_run_id = {str(row.get("run_id")): row for row in raw_rows}

	audio_ctx_pairs = build_audio_ctx_pairs(scored_rows, raw_by_run_id, issues)
	acft_with_pairs = build_acft_vs_standard_pairs(
		scored_rows,
		raw_by_run_id,
		issues,
		acft_variant="with-audio-ctx",
		comparison_type=COMPARISON_WITH_CTX,
	)
	acft_without_pairs = build_acft_vs_standard_pairs(
		scored_rows,
		raw_by_run_id,
		issues,
		acft_variant="without-audio-ctx",
		comparison_type=COMPARISON_WITHOUT_CTX,
	)

	all_rows = {
		COMPARISON_AUDIO_CTX: audio_ctx_pairs,
		COMPARISON_WITH_CTX: acft_with_pairs,
		COMPARISON_WITHOUT_CTX: acft_without_pairs,
	}
	validate_pair_counts(all_rows, issues)
	issues = dedupe_issues(issues)

	summary = build_summary(all_rows)
	duration_analysis = build_duration_analysis(all_rows)
	insights = build_insights(all_rows)
	manifest = build_manifest(args, all_rows, issues)

	print_summary(scored_rows, raw_rows, all_rows, issues)

	if args.dry_run:
		return 1 if serious_issue_count(issues) else 0

	args.output_dir.mkdir(parents=True, exist_ok=True)
	for comparison_type, rows in all_rows.items():
		write_jsonl(args.output_dir / COMPARISON_OUTPUTS[comparison_type], rows)
	write_json(args.output_dir / SUMMARY_OUTPUT, summary)
	write_json(args.output_dir / DURATION_OUTPUT, duration_analysis)
	write_json(args.output_dir / INSIGHTS_OUTPUT, insights)
	write_json(args.output_dir / MANIFEST_OUTPUT, manifest)
	return 1 if serious_issue_count(issues) else 0


if __name__ == "__main__":
	sys.exit(main())
