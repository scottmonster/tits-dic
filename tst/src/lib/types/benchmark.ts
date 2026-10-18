export interface ManifestData {
	row_count: number;
	available_backends: string[];
	available_models: string[];
	available_model_families: string[];
	available_model_sizes: string[];
	available_language_variants: string[];
	available_quantizations: string[];
	available_variants: string[];
	available_inputs: string[];
	ranking_modes: string[];
	artifact_files: string[];
}

export interface MetricSummary {
	mean: number;
	min: number;
	max: number;
	median: number;
	stddev: number;
	p10: number;
	p25: number;
	p50: number;
	p75: number;
	p90: number;
	range: number;
}

export interface GroupSummaryRecord {
	count: number;
	inputs: string[];
	word_error_rate?: MetricSummary;
	char_error_rate?: MetricSummary;
	punctuation_error_rate?: MetricSummary;
	capitalization_accuracy?: MetricSummary;
	runtime_seconds?: MetricSummary;
	realtime_factor?: MetricSummary;
	realtime_speed?: MetricSummary;
	[field: string]: unknown;
}

export type SummaryByGroup = Record<string, Record<string, GroupSummaryRecord>>;

export interface RankingRow {
	group_key: string;
	count: number;
	inputs: string[];
	backend: string;
	model: string;
	variant: string | null;
	word_error_rate?: number;
	char_error_rate?: number;
	punctuation_error_rate?: number;
	capitalization_accuracy?: number;
	runtime_seconds?: number;
	realtime_factor?: number;
	realtime_speed?: number;
	rank: number;
	metric: string;
	value: number;
	[field: string]: unknown;
}

export interface RankingsByMetric {
	[metric: string]: RankingRow[];
}

export interface RankingsData {
	global: RankingsByMetric;
	by_input: Record<string, RankingsByMetric>;
}

export interface OverviewMetricCard {
	label: string;
	value: string;
	help: string;
}

export interface BackendOverviewRow {
	backend: string;
	runs: number;
	meanWer: number | null;
	meanCer: number | null;
	meanRuntime: number | null;
	meanRealtimeFactor: number | null;
	meanPunctuationErrorRate: number | null;
	meanCapitalizationAccuracy: number | null;
}

export interface TopRankingPreview {
	title: string;
	metric: string;
	rows: RankingRow[];
}

export interface OverviewPageData {
	manifest: ManifestData;
	overviewCards: OverviewMetricCard[];
	backendRows: BackendOverviewRow[];
	topRankings: TopRankingPreview[];
	globalBestBalance: RankingRow | null;
	globalFastest: RankingRow | null;
	globalMostAccurate: RankingRow | null;
}

export interface RankingsPageData {
	manifest: ManifestData;
	metric: string;
	input: string;
	availableMetrics: string[];
	availableInputs: string[];
	rows: RankingRow[];
}

export interface ModelMetricSummary {
	mean: number | null;
	median: number | null;
	min: number | null;
	max: number | null;
}

export interface ModelOverviewMetric {
	label: string;
	value: number | null;
	format: 'percent' | 'number';
	help: string;
}

export interface ModelBreakdownRow {
	label: string;
	count: number;
	meanWer: number | null;
	meanCer: number | null;
	meanRuntime: number | null;
	meanRealtimeFactor: number | null;
	meanPunctuationErrorRate: number | null;
	meanCapitalizationAccuracy: number | null;
}

export interface ModelRankingGroup {
	title: string;
	metric: string;
	rows: RankingRow[];
}

export interface ModelDetailPageData {
	manifest: ManifestData;
	model: string;
	inputs: string[];
	overviewMetrics: ModelOverviewMetric[];
	backendRows: ModelBreakdownRow[];
	variantRows: ModelBreakdownRow[];
	rankingGroups: ModelRankingGroup[];
}

export type AcftComparisonType =
	| 'audio_ctx_only'
	| 'acft_with_ctx_vs_standard'
	| 'acft_without_ctx_vs_standard';

export interface AcftMetricSummary {
	count: number;
	mean: number;
	median: number;
	min: number;
	max: number;
}

export interface AcftGroupedSummaryRecord {
	count: number;
	[field: string]: AcftMetricSummary | number | undefined;
}

export interface AcftComparisonSummaryData {
	row_counts: Record<AcftComparisonType, number>;
	available_inputs: string[];
	available_backends: string[];
	available_comparable_model_keys: string[];
	comparison_summaries: Record<AcftComparisonType, AcftGroupedSummaryRecord>;
	grouped_by_input_file: Record<AcftComparisonType, Record<string, AcftGroupedSummaryRecord>>;
	grouped_by_backend: Record<AcftComparisonType, Record<string, AcftGroupedSummaryRecord>>;
	grouped_by_model_key: Record<AcftComparisonType, Record<string, AcftGroupedSummaryRecord>>;
}

export interface AcftDurationInterpretation {
	trend_classification: string;
	sufficient_for_curve_fit: boolean;
	notes: string;
}

export interface AcftDurationRow {
	audio_duration_seconds: number;
	count: number;
	[field: string]: AcftMetricSummary | number | undefined;
}

export interface AcftDurationBreakdown {
	unique_duration_values: number[];
	per_duration: AcftDurationRow[];
	interpretation: AcftDurationInterpretation;
}

export type AcftDurationAnalysisData = Record<AcftComparisonType, AcftDurationBreakdown>;

export interface AcftManifestData {
	input_files_used: {
		scored_jsonl: string;
		raw_jsonl: string;
	};
	output_files_produced: string[];
	counts_by_comparison_type: Record<AcftComparisonType, number>;
	available_backends: string[];
	available_inputs: string[];
	available_comparable_model_keys: string[];
	issue_count: number;
}

export interface AcftComparisonPairRow {
	comparison_type: AcftComparisonType;
	backend: string;
	input_file: string;
	audio_duration_seconds: number;
	model_key: string;
	left_run_id: string;
	right_run_id: string;
	left_model: string;
	right_model: string;
	left_variant: string | null;
	right_variant: string | null;
	left_model_family: string;
	right_model_family: string;
	relative_runtime_change: number | null;
	left_runtime_seconds?: number | null;
	right_runtime_seconds?: number | null;
	delta_runtime_seconds?: number | null;
	delta_realtime_factor?: number | null;
	delta_realtime_speed?: number | null;
	delta_word_error_rate?: number | null;
	delta_char_error_rate?: number | null;
	delta_punctuation_error_rate?: number | null;
	delta_capitalization_accuracy?: number | null;
	delta_mel_ms?: number | null;
	delta_sample_ms?: number | null;
	delta_encode_ms?: number | null;
	delta_decode_ms?: number | null;
	delta_batchd_ms?: number | null;
	delta_prompt_ms?: number | null;
	[field: string]: unknown;
}

export interface AcftComparisonOption {
	value: AcftComparisonType;
	label: string;
	description: string;
}

export interface AcftComparisonCard {
	label: string;
	value: string;
	help: string;
}

export interface AcftComparisonsPageData {
	manifest: AcftManifestData;
	summary: AcftComparisonSummaryData;
	durationAnalysis: AcftDurationAnalysisData;
	comparisonOptions: AcftComparisonOption[];
	comparisonCards: Record<AcftComparisonType, AcftComparisonCard[]>;
	rowsByType: Record<AcftComparisonType, AcftComparisonPairRow[]>;
}

export interface AcftInsightAggregate {
	count: number;
	durations?: number[];
	mean_delta_runtime_seconds?: number | null;
	median_delta_runtime_seconds?: number | null;
	mean_relative_runtime_change?: number | null;
	mean_delta_word_error_rate?: number | null;
	mean_delta_char_error_rate?: number | null;
	mean_delta_punctuation_error_rate?: number | null;
	mean_delta_capitalization_accuracy?: number | null;
	mean_delta_encode_ms?: number | null;
	mean_delta_batchd_ms?: number | null;
	helpful_left_side?: boolean;
	helps_left_side?: boolean;
	[field: string]: unknown;
}

export interface AcftInsightModelRow extends AcftInsightAggregate {
	model_key: string;
	model_family: string;
	left_models: string[];
	backends: string[];
	inputs: string[];
}

export interface AcftInsightBackendRow {
	backend: string;
	input_file?: string;
	all_rows: number;
	short_clip: AcftInsightAggregate;
	long_clip: AcftInsightAggregate;
	stops_helping_on_long_clips: boolean;
}

export interface AcftInsightsComparisonData {
	short_clip_speed_wins: AcftInsightModelRow[];
	long_clip_quality_regressions: AcftInsightModelRow[];
	backend_breakdown: AcftInsightBackendRow[];
	backend_input_breakdown: AcftInsightBackendRow[];
}

export interface AcftInsightsData {
	duration_buckets: {
		short_clip_max_seconds: number;
		long_clip_min_seconds: number;
	};
	comparisons: Record<AcftComparisonType, AcftInsightsComparisonData>;
}

export interface AcftInsightsPageData {
	manifest: AcftManifestData;
	insights: AcftInsightsData;
	comparisonOptions: AcftComparisonOption[];
	rowsByType: Record<AcftComparisonType, AcftComparisonPairRow[]>;
}