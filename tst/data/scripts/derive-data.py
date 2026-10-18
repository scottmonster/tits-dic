#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable


METRIC_FIELDS = [
	"word_error_rate",
	"char_error_rate",
	"punctuation_error_rate",
	"capitalization_accuracy",
	"runtime_seconds",
	"realtime_factor",
	"realtime_speed",
]

LOWER_IS_BETTER = {
	"word_error_rate": True,
	"char_error_rate": True,
	"runtime_seconds": True,
	"realtime_factor": True,
	"punctuation_error_rate": True,
	"capitalization_accuracy": False,
	"realtime_speed": False,
}

GROUPINGS: dict[str, list[str]] = {
	"backend": ["backend"],
	"model": ["model"],
	"variant": ["variant"],
	"quantization": ["quantization"],
	"is_quantized": ["is_quantized"],
	"model_family": ["model_family"],
	"model_size": ["model_size"],
	"language_variant": ["language_variant"],
	"audio_ctx_enabled": ["audio_ctx_enabled"],
	"backend__model": ["backend", "model"],
	"backend__quantization": ["backend", "quantization"],
	"model__variant": ["model", "variant"],
	"model_family__quantization": ["model_family", "quantization"],
	"model_size__language_variant": ["model_size", "language_variant"],
}

RANKING_METRICS = [
	"word_error_rate",
	"char_error_rate",
	"punctuation_error_rate",
	"capitalization_accuracy",
	"runtime_seconds",
	"realtime_factor",
	"realtime_speed",
]


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="Derive frontend-ready comparison artifacts from scored ASR runs."
	)
	parser.add_argument(
		"--input-jsonl",
		type=Path,
		default=Path("data/extracted_data/scored_runs.jsonl"),
		help="Canonical scored run input JSONL.",
	)
	parser.add_argument(
		"--output-dir",
		type=Path,
		default=Path("data/extracted_data/derived"),
		help="Directory for derived frontend artifacts.",
	)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Build all derivations in memory and print validation summary without writing files.",
	)
	return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
	with path.open("r", encoding="utf-8") as handle:
		return [json.loads(line) for line in handle if line.strip()]


def make_group_key(record: dict[str, Any], fields: list[str]) -> str:
	values = []
	for field in fields:
		value = record.get(field)
		if value is None:
			value = "default" if field == "variant" else "null"
		values.append(str(value))
	return "|".join(values)


def percentile(sorted_values: list[float], value: float) -> float:
	if not sorted_values:
		return math.nan
	if len(sorted_values) == 1:
		return sorted_values[0]
	position = (len(sorted_values) - 1) * value
	lower_index = math.floor(position)
	upper_index = math.ceil(position)
	if lower_index == upper_index:
		return sorted_values[lower_index]
	lower_value = sorted_values[lower_index]
	upper_value = sorted_values[upper_index]
	fraction = position - lower_index
	return lower_value + (upper_value - lower_value) * fraction


def stddev(values: list[float]) -> float:
	if len(values) < 2:
		return 0.0
	avg = mean(values)
	variance = sum((value - avg) ** 2 for value in values) / len(values)
	return math.sqrt(variance)


def summarize_metric(values: list[float]) -> dict[str, float] | None:
	if not values:
		return None
	ordered = sorted(values)
	return {
		"mean": mean(ordered),
		"min": ordered[0],
		"max": ordered[-1],
		"median": median(ordered),
		"stddev": stddev(ordered),
		"p10": percentile(ordered, 0.10),
		"p25": percentile(ordered, 0.25),
		"p50": percentile(ordered, 0.50),
		"p75": percentile(ordered, 0.75),
		"p90": percentile(ordered, 0.90),
		"range": ordered[-1] - ordered[0],
	}


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
	summary: dict[str, Any] = {
		"count": len(records),
		"inputs": sorted({record["input_file"] for record in records}),
	}
	for metric in METRIC_FIELDS:
		values = [record[metric] for record in records if record.get(metric) is not None]
		summary[metric] = summarize_metric(values)
	return summary


def build_group_summaries(records: list[dict[str, Any]]) -> dict[str, Any]:
	summaries: dict[str, Any] = {}
	for name, fields in GROUPINGS.items():
		grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
		for record in records:
			grouped[make_group_key(record, fields)].append(record)
		summaries[name] = {
			key: summarize_records(group_records) for key, group_records in sorted(grouped.items())
		}
	return summaries


def aggregate_group(records: list[dict[str, Any]], group_fields: list[str]) -> list[dict[str, Any]]:
	grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
	for record in records:
		grouped[make_group_key(record, group_fields)].append(record)

	results: list[dict[str, Any]] = []
	for key, group_records in sorted(grouped.items()):
		row: dict[str, Any] = {
			"group_key": key,
			"count": len(group_records),
			"inputs": sorted({record["input_file"] for record in group_records}),
		}
		exemplar = group_records[0]
		for field in group_fields:
			row[field] = exemplar.get(field)
		for metric in METRIC_FIELDS:
			values = [record[metric] for record in group_records if record.get(metric) is not None]
			if values:
				row[metric] = mean(values)
		results.append(row)
	return results


def rank_records(records: list[dict[str, Any]], metric: str, descending: bool) -> list[dict[str, Any]]:
	ordered = sorted(records, key=lambda record: record[metric], reverse=descending)
	output: list[dict[str, Any]] = []
	for index, record in enumerate(ordered, start=1):
		output.append(
			{
				"rank": index,
				"run_id": record["run_id"],
				"backend": record["backend"],
				"input_file": record["input_file"],
				"model": record["model"],
				"variant": record.get("variant"),
				"metric": metric,
				"value": record[metric],
			}
		)
	return output


def rank_aggregates(rows: list[dict[str, Any]], metric: str, descending: bool) -> list[dict[str, Any]]:
	eligible = [row for row in rows if row.get(metric) is not None]
	ordered = sorted(eligible, key=lambda row: row[metric], reverse=descending)
	results: list[dict[str, Any]] = []
	for index, row in enumerate(ordered, start=1):
		item = dict(row)
		item["rank"] = index
		item["metric"] = metric
		item["value"] = row[metric]
		results.append(item)
	return results


def normalized_rank_maps(rows: list[dict[str, Any]], metric: str) -> dict[str, float]:
	descending = not LOWER_IS_BETTER[metric]
	ranked = rank_aggregates(rows, metric, descending)
	total = max(len(ranked), 1)
	score_map: dict[str, float] = {}
	for item in ranked:
		if total == 1:
			score_map[item["group_key"]] = 1.0
			continue
		normalized = 1 - ((item["rank"] - 1) / (total - 1))
		score_map[item["group_key"]] = normalized
	return score_map


def build_rankings(records: list[dict[str, Any]]) -> dict[str, Any]:
	global_groups = aggregate_group(records, ["backend", "model", "variant"])
	accuracy_rank = normalized_rank_maps(global_groups, "word_error_rate")
	speed_rank = normalized_rank_maps(global_groups, "realtime_factor")

	balance_rows: list[dict[str, Any]] = []
	for row in global_groups:
		group_key = row["group_key"]
		row_copy = dict(row)
		row_copy["balance_score_accuracy_heavy"] = (
			0.7 * accuracy_rank.get(group_key, 0.0) + 0.3 * speed_rank.get(group_key, 0.0)
		)
		row_copy["balance_score_even"] = (
			0.5 * accuracy_rank.get(group_key, 0.0) + 0.5 * speed_rank.get(group_key, 0.0)
		)
		row_copy["balance_score_speed_heavy"] = (
			0.3 * accuracy_rank.get(group_key, 0.0) + 0.7 * speed_rank.get(group_key, 0.0)
		)
		balance_rows.append(row_copy)

	global_rankings = {
		metric: rank_aggregates(global_groups, metric, descending=not LOWER_IS_BETTER[metric])
		for metric in RANKING_METRICS
	}
	global_rankings["balance_score_accuracy_heavy"] = rank_aggregates(
		balance_rows, "balance_score_accuracy_heavy", descending=True
	)
	global_rankings["balance_score_even"] = rank_aggregates(
		balance_rows, "balance_score_even", descending=True
	)
	global_rankings["balance_score_speed_heavy"] = rank_aggregates(
		balance_rows, "balance_score_speed_heavy", descending=True
	)

	by_input: dict[str, Any] = {}
	input_names = sorted({record["input_file"] for record in records})
	for input_file in input_names:
		input_rows = aggregate_group(
			[record for record in records if record["input_file"] == input_file],
			["backend", "model", "variant"],
		)
		input_accuracy_rank = normalized_rank_maps(input_rows, "word_error_rate")
		input_speed_rank = normalized_rank_maps(input_rows, "realtime_factor")
		input_balance_rows: list[dict[str, Any]] = []
		for row in input_rows:
			group_key = row["group_key"]
			row_copy = dict(row)
			row_copy["balance_score_accuracy_heavy"] = (
				0.7 * input_accuracy_rank.get(group_key, 0.0)
				+ 0.3 * input_speed_rank.get(group_key, 0.0)
			)
			row_copy["balance_score_even"] = (
				0.5 * input_accuracy_rank.get(group_key, 0.0)
				+ 0.5 * input_speed_rank.get(group_key, 0.0)
			)
			row_copy["balance_score_speed_heavy"] = (
				0.3 * input_accuracy_rank.get(group_key, 0.0)
				+ 0.7 * input_speed_rank.get(group_key, 0.0)
			)
			input_balance_rows.append(row_copy)

		input_rankings = {
			metric: rank_aggregates(input_rows, metric, descending=not LOWER_IS_BETTER[metric])
			for metric in RANKING_METRICS
		}
		input_rankings["balance_score_accuracy_heavy"] = rank_aggregates(
			input_balance_rows, "balance_score_accuracy_heavy", descending=True
		)
		input_rankings["balance_score_even"] = rank_aggregates(
			input_balance_rows, "balance_score_even", descending=True
		)
		input_rankings["balance_score_speed_heavy"] = rank_aggregates(
			input_balance_rows, "balance_score_speed_heavy", descending=True
		)
		by_input[input_file] = input_rankings

	return {
		"global": global_rankings,
		"by_input": by_input,
	}


def pair_audio_ctx(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
	paired: dict[tuple[str, str, str], dict[bool, dict[str, Any]]] = defaultdict(dict)
	for record in records:
		if record["model_family"] != "acft":
			continue
		key = (record["backend"], record["input_file"], record["model"])
		paired[key][bool(record["audio_ctx_enabled"])] = record

	results: list[dict[str, Any]] = []
	for key, pair in sorted(paired.items()):
		if True not in pair or False not in pair:
			continue
		with_ctx = pair[True]
		without_ctx = pair[False]
		results.append(
			{
				"backend": key[0],
				"input_file": key[1],
				"model": key[2],
				"with_audio_ctx_run_id": with_ctx["run_id"],
				"without_audio_ctx_run_id": without_ctx["run_id"],
				"delta_word_error_rate": without_ctx["word_error_rate"] - with_ctx["word_error_rate"],
				"delta_char_error_rate": without_ctx["char_error_rate"] - with_ctx["char_error_rate"],
				"delta_punctuation_error_rate": (
					without_ctx["punctuation_error_rate"] - with_ctx["punctuation_error_rate"]
				),
				"delta_capitalization_accuracy": (
					with_ctx["capitalization_accuracy"]
					- without_ctx["capitalization_accuracy"]
				),
				"delta_runtime_seconds": without_ctx["runtime_seconds"] - with_ctx["runtime_seconds"],
				"delta_realtime_factor": without_ctx["realtime_factor"] - with_ctx["realtime_factor"],
				"delta_realtime_speed": (
					with_ctx["realtime_speed"]
					- without_ctx["realtime_speed"]
				),
			}
		)
	return results


def pair_quantization(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
	paired: dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
	for record in records:
		key = (
			record["backend"],
			record["input_file"],
			record["model_base_name"],
			str(record["variant"]),
			record["model_family"],
		)
		paired[key][record["quantization"]] = record

	results: list[dict[str, Any]] = []
	for key, variants in sorted(paired.items()):
		if "none" not in variants:
			continue
		baseline = variants["none"]
		for quantization, record in sorted(variants.items()):
			if quantization == "none":
				continue
			results.append(
				{
					"backend": key[0],
					"input_file": key[1],
					"model_base_name": key[2],
					"variant": None if key[3] == "None" else key[3],
					"model_family": key[4],
					"baseline_run_id": baseline["run_id"],
					"quantized_run_id": record["run_id"],
					"baseline_quantization": baseline["quantization"],
					"quantized_quantization": quantization,
					"delta_word_error_rate": record["word_error_rate"] - baseline["word_error_rate"],
					"delta_char_error_rate": record["char_error_rate"] - baseline["char_error_rate"],
					"delta_punctuation_error_rate": (
						record["punctuation_error_rate"] - baseline["punctuation_error_rate"]
					),
					"delta_capitalization_accuracy": (
						record["capitalization_accuracy"] - baseline["capitalization_accuracy"]
					),
					"delta_runtime_seconds": record["runtime_seconds"] - baseline["runtime_seconds"],
					"delta_realtime_factor": record["realtime_factor"] - baseline["realtime_factor"],
					"delta_realtime_speed": (
						record["realtime_speed"]
						- baseline["realtime_speed"]
					),
				}
			)
	return results


def pair_backends(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
	grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
	for record in records:
		grouped[(record["input_file"], record["model"], str(record["variant"]))].append(record)

	results: list[dict[str, Any]] = []
	for key, group_records in sorted(grouped.items()):
		by_backend = {record["backend"]: record for record in group_records}
		backends = sorted(by_backend)
		for index, backend_left in enumerate(backends):
			for backend_right in backends[index + 1 :]:
				left = by_backend[backend_left]
				right = by_backend[backend_right]
				results.append(
					{
						"input_file": key[0],
						"model": key[1],
						"variant": None if key[2] == "None" else key[2],
						"left_backend": backend_left,
						"right_backend": backend_right,
						"left_run_id": left["run_id"],
						"right_run_id": right["run_id"],
						"delta_word_error_rate": right["word_error_rate"] - left["word_error_rate"],
						"delta_char_error_rate": right["char_error_rate"] - left["char_error_rate"],
						"delta_runtime_seconds": right["runtime_seconds"] - left["runtime_seconds"],
						"delta_realtime_factor": right["realtime_factor"] - left["realtime_factor"],
						"delta_punctuation_error_rate": (
							right["punctuation_error_rate"] - left["punctuation_error_rate"]
						),
					}
				)
	return results


def pair_input_difficulty(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
	grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
	for record in records:
		grouped[(record["backend"], record["model"], str(record["variant"]))].append(record)

	results: list[dict[str, Any]] = []
	for key, group_records in sorted(grouped.items()):
		by_input = {record["input_file"]: record for record in group_records}
		inputs = sorted(by_input)
		for index, input_left in enumerate(inputs):
			for input_right in inputs[index + 1 :]:
				left = by_input[input_left]
				right = by_input[input_right]
				results.append(
					{
						"backend": key[0],
						"model": key[1],
						"variant": None if key[2] == "None" else key[2],
						"left_input": input_left,
						"right_input": input_right,
						"left_run_id": left["run_id"],
						"right_run_id": right["run_id"],
						"delta_word_error_rate": right["word_error_rate"] - left["word_error_rate"],
						"delta_char_error_rate": right["char_error_rate"] - left["char_error_rate"],
						"delta_runtime_seconds": right["runtime_seconds"] - left["runtime_seconds"],
						"delta_realtime_factor": right["realtime_factor"] - left["realtime_factor"],
					}
				)
	return results


def build_pareto_front(records: list[dict[str, Any]], x_metric: str, y_metric: str) -> list[dict[str, Any]]:
	candidates = [
		record for record in records if record.get(x_metric) is not None and record.get(y_metric) is not None
	]
	front: list[dict[str, Any]] = []
	for candidate in candidates:
		dominated = False
		for other in candidates:
			if other is candidate:
				continue
			x_better_or_equal = other[x_metric] <= candidate[x_metric]
			y_better_or_equal = other[y_metric] <= candidate[y_metric]
			strictly_better = other[x_metric] < candidate[x_metric] or other[y_metric] < candidate[y_metric]
			if x_better_or_equal and y_better_or_equal and strictly_better:
				dominated = True
				break
		if not dominated:
			front.append(
				{
					"run_id": candidate["run_id"],
					"backend": candidate["backend"],
					"input_file": candidate["input_file"],
					"model": candidate["model"],
					"variant": candidate.get("variant"),
					x_metric: candidate[x_metric],
					y_metric: candidate[y_metric],
				}
			)
	return sorted(front, key=lambda item: (item[x_metric], item[y_metric]))


def build_manifest(records: list[dict[str, Any]], output_dir: Path) -> dict[str, Any]:
	files = sorted(path.name for path in output_dir.iterdir() if path.is_file()) if output_dir.exists() else []
	if "manifest.json" not in files:
		files.append("manifest.json")
		files.sort()
	return {
		"row_count": len(records),
		"available_backends": sorted({record["backend"] for record in records}),
		"available_models": sorted({record["model"] for record in records}),
		"available_model_families": sorted({record["model_family"] for record in records}),
		"available_model_sizes": sorted({record["model_size"] for record in records}),
		"available_language_variants": sorted({record["language_variant"] for record in records}),
		"available_quantizations": sorted({record["quantization"] for record in records}),
		"available_variants": sorted({str(record.get("variant") or "default") for record in records}),
		"available_inputs": sorted({record["input_file"] for record in records}),
		"ranking_modes": sorted(RANKING_METRICS + [
			"balance_score_accuracy_heavy",
			"balance_score_even",
			"balance_score_speed_heavy",
		]),
		"artifact_files": files,
	}


def write_json(path: Path, payload: Any) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	with path.open("w", encoding="utf-8") as handle:
		json.dump(payload, handle, indent=2, ensure_ascii=False)
		handle.write("\n")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	with path.open("w", encoding="utf-8") as handle:
		for row in rows:
			handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def validate(
	records: list[dict[str, Any]],
	audio_ctx_pairs: list[dict[str, Any]],
	quant_pairs: list[dict[str, Any]],
	grouping_summaries: dict[str, Any],
) -> list[str]:
	issues: list[str] = []
	if not records:
		issues.append("no scored rows loaded")

	expected_acft_pairs = len([record for record in records if record["model_family"] == "acft"]) // 2
	if len(audio_ctx_pairs) != expected_acft_pairs:
		issues.append(
			f"audio-ctx pair count mismatch: expected {expected_acft_pairs}, got {len(audio_ctx_pairs)}"
		)

	if grouping_summaries.get("backend"):
		backend_total = sum(group["count"] for group in grouping_summaries["backend"].values())
		if backend_total != len(records):
			issues.append(
				f"backend grouping coverage mismatch: expected {len(records)}, got {backend_total}"
			)

	for pair in quant_pairs:
		if pair["baseline_quantization"] != "none":
			issues.append("invalid quantization pair baseline detected")
			break

	return issues


def main() -> int:
	args = parse_args()
	records = load_jsonl(args.input_jsonl)
	output_dir = args.output_dir

	grouping_summaries = build_group_summaries(records)
	rankings = build_rankings(records)
	audio_ctx_pairs = pair_audio_ctx(records)
	quantization_pairs = pair_quantization(records)
	backend_pairs = pair_backends(records)
	input_difficulty_pairs = pair_input_difficulty(records)
	pareto_accuracy_speed_global = build_pareto_front(records, "wer_normalized", "realtime_factor")
	pareto_accuracy_speed_global = build_pareto_front(records, "word_error_rate", "realtime_factor")

	pareto_accuracy_speed_by_input = {
		input_file: build_pareto_front(
			[record for record in records if record["input_file"] == input_file],
			"word_error_rate",
			"realtime_factor",
		)
		for input_file in sorted({record["input_file"] for record in records})
	}

	issues = validate(records, audio_ctx_pairs, quantization_pairs, grouping_summaries)

	print(f"row_count={len(records)}")
	print(f"audio_ctx_pair_count={len(audio_ctx_pairs)}")
	print(f"quantization_pair_count={len(quantization_pairs)}")
	print(f"backend_pair_count={len(backend_pairs)}")
	print(f"input_difficulty_pair_count={len(input_difficulty_pairs)}")
	print(f"pareto_accuracy_speed_global_count={len(pareto_accuracy_speed_global)}")
	print(f"issues={len(issues)}")

	if args.dry_run:
		for issue in issues:
			print(issue)
		return 1 if issues else 0

	write_json(output_dir / "summary_by_group.json", grouping_summaries)
	write_json(output_dir / "rankings_global_and_by_input.json", rankings)
	write_jsonl(output_dir / "comparisons_audio_ctx.jsonl", audio_ctx_pairs)
	write_jsonl(output_dir / "comparisons_quantization.jsonl", quantization_pairs)
	write_jsonl(output_dir / "comparisons_backend.jsonl", backend_pairs)
	write_jsonl(output_dir / "comparisons_input_difficulty.jsonl", input_difficulty_pairs)
	write_json(
		output_dir / "pareto_accuracy_speed.json",
		{
			"global": pareto_accuracy_speed_global,
			"by_input": pareto_accuracy_speed_by_input,
		},
	)
	write_json(output_dir / "validation.json", {"issues": issues})
	manifest = build_manifest(records, output_dir)
	write_json(output_dir / "manifest.json", manifest)

	for issue in issues:
		print(issue)
	return 1 if issues else 0


if __name__ == "__main__":
	raise SystemExit(main())
