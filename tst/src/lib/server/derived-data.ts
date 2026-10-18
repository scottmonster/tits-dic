import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';

import type {
	AcftComparisonCard,
	AcftComparisonOption,
	AcftComparisonPairRow,
	AcftComparisonSummaryData,
	AcftComparisonsPageData,
	AcftComparisonType,
	AcftDurationAnalysisData,
	AcftInsightsData,
	AcftInsightsPageData,
	AcftManifestData,
	BackendOverviewRow,
	GroupSummaryRecord,
	ManifestData,
	ModelBreakdownRow,
	ModelDetailPageData,
	ModelOverviewMetric,
	ModelRankingGroup,
	OverviewMetricCard,
	OverviewPageData,
	RankingsPageData,
	RankingRow,
	RankingsData,
	SummaryByGroup,
	TopRankingPreview
} from '$lib/types/benchmark';

const DERIVED_DIR = resolve(process.cwd(), 'data', 'extracted_data', 'derived');

const ACFT_COMPARISON_OPTIONS: AcftComparisonOption[] = [
	{
		value: 'audio_ctx_only',
		label: 'ACFT with vs without context',
		description: 'Shows what changes when audio context is the only difference.'
	},
	{
		value: 'acft_with_ctx_vs_standard',
		label: 'ACFT with context vs standard',
		description: 'Highlights the short-latency tradeoff between ACFT and matching standard models.'
	},
	{
		value: 'acft_without_ctx_vs_standard',
		label: 'ACFT without context vs standard',
		description: 'Separates the ACFT family effect from the audio-context flag.'
	}
];

async function readJsonFile<T>(fileName: string): Promise<T> {
	const filePath = resolve(DERIVED_DIR, fileName);
	const contents = await readFile(filePath, 'utf8');
	return JSON.parse(contents) as T;
}

async function readJsonlFile<T>(fileName: string): Promise<T[]> {
	const filePath = resolve(DERIVED_DIR, fileName);
	const contents = await readFile(filePath, 'utf8');
	return contents
		.split('\n')
		.filter((line) => line.trim().length > 0)
		.map((line) => JSON.parse(line) as T);
}

function formatPercent(value: number | null | undefined, digits = 1): string {
	if (value == null || Number.isNaN(value)) {
		return 'n/a';
	}

	return `${(value * 100).toFixed(digits)}%`;
}

function formatNumber(value: number | null | undefined, digits = 2): string {
	if (value == null || Number.isNaN(value)) {
		return 'n/a';
	}

	return value.toFixed(digits);
}

function pickMetric(summary: GroupSummaryRecord, metric: string): number | null {
	const metricSummary = summary[metric] as { mean?: number } | undefined;
	return metricSummary?.mean ?? null;
}

function pickMetricField(summary: GroupSummaryRecord, metric: string, field: 'mean' | 'median' | 'min' | 'max'): number | null {
	const metricSummary = summary[metric] as Record<string, number> | undefined;
	return metricSummary?.[field] ?? null;
}

function buildOverviewCards(manifest: ManifestData, backendRows: BackendOverviewRow[]): OverviewMetricCard[] {
	const lowestWer = backendRows.reduce<number | null>((best, row) => {
		if (row.meanWer == null) {
			return best;
		}
		if (best == null || row.meanWer < best) {
			return row.meanWer;
		}
		return best;
	}, null);

	const fastestRtf = backendRows.reduce<number | null>((best, row) => {
		if (row.meanRealtimeFactor == null) {
			return best;
		}
		if (best == null || row.meanRealtimeFactor < best) {
			return row.meanRealtimeFactor;
		}
		return best;
	}, null);

	return [
		{
			label: 'Scored runs',
			value: String(manifest.row_count),
			help: 'All derived metrics are based on the extracted 486-run benchmark set.'
		},
		{
			label: 'Model configs',
			value: String(manifest.available_models.length),
			help: 'Distinct model names currently present in the derived manifest.'
		},
		{
			label: 'Best mean WER',
			value: formatPercent(lowestWer),
			help: 'Lowest backend-level mean normalized word error rate.'
		},
		{
			label: 'Fastest mean RTF',
			value: formatNumber(fastestRtf, 3),
			help: 'Lowest backend-level mean realtime factor. Lower is faster.'
		}
	];
}

function buildBackendRows(summaryByGroup: SummaryByGroup): BackendOverviewRow[] {
	const backendSummary = summaryByGroup.backend ?? {};

	return Object.entries(backendSummary)
		.map(([backend, summary]) => ({
			backend,
			runs: summary.count,
			meanWer: pickMetric(summary, 'word_error_rate'),
			meanCer: pickMetric(summary, 'char_error_rate'),
			meanRuntime: pickMetric(summary, 'runtime_seconds'),
			meanRealtimeFactor: pickMetric(summary, 'realtime_factor'),
			meanPunctuationErrorRate: pickMetric(summary, 'punctuation_error_rate'),
			meanCapitalizationAccuracy: pickMetric(summary, 'capitalization_accuracy')
		}))
		.sort((left, right) => (left.meanWer ?? Number.POSITIVE_INFINITY) - (right.meanWer ?? Number.POSITIVE_INFINITY));
}

function previewRanking(title: string, metric: string, rows: RankingRow[], count = 5): TopRankingPreview {
	return {
		title,
		metric,
		rows: rows.slice(0, count)
	};
}

function formatSigned(value: number | null | undefined, digits = 3, percent = false): string {
	if (value == null || Number.isNaN(value)) {
		return 'n/a';
	}

	const rendered = percent ? `${(value * 100).toFixed(digits)}%` : value.toFixed(digits);
	return value > 0 ? `+${rendered}` : rendered;
}

function humanizeAcftComparison(type: AcftComparisonType): string {
	return ACFT_COMPARISON_OPTIONS.find((option) => option.value === type)?.label ?? type;
}

function acftComparisonCards(summary: AcftComparisonSummaryData): Record<AcftComparisonType, AcftComparisonCard[]> {
	const result = {} as Record<AcftComparisonType, AcftComparisonCard[]>;

	for (const option of ACFT_COMPARISON_OPTIONS) {
		const aggregate = summary.comparison_summaries[option.value];
		const runtimeDelta = aggregate.delta_runtime_seconds as { mean?: number } | undefined;
		const runtimePct = aggregate.relative_runtime_change as { mean?: number } | undefined;
		const werDelta = aggregate.delta_word_error_rate as { mean?: number } | undefined;
		const encodeDelta = aggregate.delta_encode_ms as { mean?: number } | undefined;

		result[option.value] = [
			{
				label: 'Matched pairs',
				value: String(aggregate.count),
				help: `${humanizeAcftComparison(option.value)} available in the derived artifacts.`
			},
			{
				label: 'Mean runtime delta',
				value: formatSigned(runtimeDelta?.mean, 3),
				help: 'Delta uses left - right. Negative runtime means the left side is faster.'
			},
			{
				label: 'Mean relative runtime',
				value: formatSigned(runtimePct?.mean, 1, true),
				help: 'Relative runtime change computed as (left - right) / right.'
			},
			{
				label: 'Mean WER delta',
				value: formatSigned(werDelta?.mean, 4),
				help: 'Negative WER delta favors the left side. Positive means a regression.'
			},
			{
				label: 'Mean encode delta',
				value: formatSigned(encodeDelta?.mean, 1),
				help: 'Useful for spotting whether the runtime change is mostly coming from encode work.'
			}
		];
	}

	return result;
}

export async function loadOverviewData(): Promise<OverviewPageData> {
	const [manifest, summaryByGroup, rankings] = await Promise.all([
		readJsonFile<ManifestData>('manifest.json'),
		readJsonFile<SummaryByGroup>('summary_by_group.json'),
		readJsonFile<RankingsData>('rankings_global_and_by_input.json')
	]);

	const backendRows = buildBackendRows(summaryByGroup);
	const overviewCards = buildOverviewCards(manifest, backendRows);
	const topRankings = [
		previewRanking('Most accurate', 'word_error_rate', rankings.global.word_error_rate ?? []),
		previewRanking('Fastest', 'realtime_factor', rankings.global.realtime_factor ?? []),
		previewRanking(
			'Best balance',
			'balance_score_accuracy_heavy',
			rankings.global.balance_score_accuracy_heavy ?? []
		)
	];

	return {
		manifest,
		overviewCards,
		backendRows,
		topRankings,
		globalBestBalance: rankings.global.balance_score_accuracy_heavy?.[0] ?? null,
		globalFastest: rankings.global.realtime_factor?.[0] ?? null,
		globalMostAccurate: rankings.global.word_error_rate?.[0] ?? null
	};
}

export async function loadRankingsData(metric?: string | null, input?: string | null): Promise<RankingsPageData> {
	const [manifest, rankings] = await Promise.all([
		readJsonFile<ManifestData>('manifest.json'),
		readJsonFile<RankingsData>('rankings_global_and_by_input.json')
	]);

	const selectedMetric = metric && manifest.ranking_modes.includes(metric) ? metric : 'word_error_rate';
	const selectedInput = input && manifest.available_inputs.includes(input) ? input : 'global';
	const source =
		selectedInput === 'global'
			? rankings.global[selectedMetric] ?? []
			: rankings.by_input[selectedInput]?.[selectedMetric] ?? [];

	return {
		manifest,
		metric: selectedMetric,
		input: selectedInput,
		availableMetrics: manifest.ranking_modes,
		availableInputs: ['global', ...manifest.available_inputs],
		rows: source
	};
}

function buildModelOverviewMetrics(summary: GroupSummaryRecord): ModelOverviewMetric[] {
	return [
		{
			label: 'Mean WER',
			value: pickMetric(summary, 'word_error_rate'),
			format: 'percent',
			help: 'Lower normalized word error rate is better.'
		},
		{
			label: 'Mean CER',
			value: pickMetric(summary, 'char_error_rate'),
			format: 'percent',
			help: 'Lower normalized character error rate is better.'
		},
		{
			label: 'Mean capitalization',
			value: pickMetric(summary, 'capitalization_accuracy'),
			format: 'percent',
			help: 'Higher capitalization accuracy means casing is preserved more reliably.'
		},
		{
			label: 'Mean runtime',
			value: pickMetric(summary, 'runtime_seconds'),
			format: 'number',
			help: 'Average runtime in seconds across all runs for this model.'
		},
		{
			label: 'Mean RTF',
			value: pickMetric(summary, 'realtime_factor'),
			format: 'number',
			help: 'Lower realtime factor means faster inference.'
		}
	];
}

function breakdownRow(label: string, summary: GroupSummaryRecord): ModelBreakdownRow {
	return {
		label,
		count: summary.count,
		meanWer: pickMetric(summary, 'word_error_rate'),
		meanCer: pickMetric(summary, 'char_error_rate'),
		meanRuntime: pickMetric(summary, 'runtime_seconds'),
		meanRealtimeFactor: pickMetric(summary, 'realtime_factor'),
		meanPunctuationErrorRate: pickMetric(summary, 'punctuation_error_rate'),
		meanCapitalizationAccuracy: pickMetric(summary, 'capitalization_accuracy')
	};
}

function buildBreakdownRows(
	section: Record<string, GroupSummaryRecord>,
	model: string,
	type: 'backend' | 'variant'
): ModelBreakdownRow[] {
	const rows = Object.entries(section)
		.filter(([key]) => (type === 'backend' ? key.endsWith(`|${model}`) : key.startsWith(`${model}|`)))
		.map(([key, summary]) => {
			const label = type === 'backend' ? key.split('|', 1)[0] : key.slice(model.length + 1);
			return breakdownRow(label || 'default', summary);
		})
		.sort((left, right) => (left.meanWer ?? Number.POSITIVE_INFINITY) - (right.meanWer ?? Number.POSITIVE_INFINITY));

	return rows;
}

function buildModelRankingGroups(rankings: RankingsData, model: string): ModelRankingGroup[] {
	const specs = [
		{ title: 'Accuracy placements', metric: 'word_error_rate' },
		{ title: 'Speed placements', metric: 'realtime_factor' },
		{ title: 'Balance placements', metric: 'balance_score_accuracy_heavy' }
	];

	return specs.map(({ title, metric }) => ({
		title,
		metric,
		rows: (rankings.global[metric] ?? []).filter((row) => row.model === model).slice(0, 6)
	}));
}

export async function loadModelDetailData(model: string): Promise<ModelDetailPageData | null> {
	const [manifest, summaryByGroup, rankings] = await Promise.all([
		readJsonFile<ManifestData>('manifest.json'),
		readJsonFile<SummaryByGroup>('summary_by_group.json'),
		readJsonFile<RankingsData>('rankings_global_and_by_input.json')
	]);

	const modelSummary = summaryByGroup.model?.[model];
	if (!modelSummary) {
		return null;
	}

	return {
		manifest,
		model,
		inputs: modelSummary.inputs,
		overviewMetrics: buildModelOverviewMetrics(modelSummary),
		backendRows: buildBreakdownRows(summaryByGroup.backend__model ?? {}, model, 'backend'),
		variantRows: buildBreakdownRows(summaryByGroup.model__variant ?? {}, model, 'variant'),
		rankingGroups: buildModelRankingGroups(rankings, model)
	};
}

export async function loadAcftComparisonsData(): Promise<AcftComparisonsPageData> {
	const [manifest, summary, durationAnalysis, audioCtxRows, withCtxRows, withoutCtxRows] =
		await Promise.all([
			readJsonFile<AcftManifestData>('acft_compare_manifest.json'),
			readJsonFile<AcftComparisonSummaryData>('acft_compare_summary.json'),
			readJsonFile<AcftDurationAnalysisData>('acft_compare_duration_analysis.json'),
			readJsonlFile<AcftComparisonPairRow>('acft_compare_audio_ctx_pairs.jsonl'),
			readJsonlFile<AcftComparisonPairRow>('acft_compare_vs_standard_with_ctx.jsonl'),
			readJsonlFile<AcftComparisonPairRow>('acft_compare_vs_standard_without_ctx.jsonl')
		]);

	return {
		manifest,
		summary,
		durationAnalysis,
		comparisonOptions: ACFT_COMPARISON_OPTIONS,
		comparisonCards: acftComparisonCards(summary),
		rowsByType: {
			audio_ctx_only: audioCtxRows,
			acft_with_ctx_vs_standard: withCtxRows,
			acft_without_ctx_vs_standard: withoutCtxRows
		}
	};
}

export async function loadAcftInsightsData(): Promise<AcftInsightsPageData> {
	const [manifest, insights, audioCtxRows, withCtxRows, withoutCtxRows] = await Promise.all([
		readJsonFile<AcftManifestData>('acft_compare_manifest.json'),
		readJsonFile<AcftInsightsData>('acft_compare_insights.json'),
		readJsonlFile<AcftComparisonPairRow>('acft_compare_audio_ctx_pairs.jsonl'),
		readJsonlFile<AcftComparisonPairRow>('acft_compare_vs_standard_with_ctx.jsonl'),
		readJsonlFile<AcftComparisonPairRow>('acft_compare_vs_standard_without_ctx.jsonl')
	]);

	return {
		manifest,
		insights,
		comparisonOptions: ACFT_COMPARISON_OPTIONS,
		rowsByType: {
			audio_ctx_only: audioCtxRows,
			acft_with_ctx_vs_standard: withCtxRows,
			acft_without_ctx_vs_standard: withoutCtxRows
		}
	};
}