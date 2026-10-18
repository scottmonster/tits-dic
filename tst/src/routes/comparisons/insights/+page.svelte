<script lang="ts">
	import type { PageData } from './$types';
	import type {
		AcftComparisonPairRow,
		AcftComparisonType,
		AcftInsightAggregate,
		AcftInsightBackendRow,
		AcftInsightModelRow
	} from '$lib';

	let { data }: { data: PageData } = $props();
	let comparisonType = $state<AcftComparisonType>('acft_with_ctx_vs_standard');
	let backendFilter = $state<'all' | string>('all');
	let inputFilter = $state<'all' | string>('all');

	const selectedOption = $derived(
		data.comparisonOptions.find((option) => option.value === comparisonType) ?? data.comparisonOptions[0]
	);
	const comparisonInsights = $derived(data.insights.comparisons[comparisonType]);
	const comparisonRows = $derived(data.rowsByType[comparisonType] ?? []);
	const backendOptions = $derived(
		Array.from(
			new Set(
				[
					...comparisonRows.map((row) => row.backend),
					...comparisonInsights.backend_breakdown.map((row) => row.backend)
				].filter(Boolean)
			)
		).sort()
	);
	const inputOptions = $derived(
		Array.from(
			new Set(
				[
					...comparisonRows.map((row) => row.input_file),
					...comparisonInsights.backend_input_breakdown.map((row) => row.input_file ?? '')
				].filter(Boolean)
			)
		).sort()
	);

	const filteredComparisonRows = $derived(
		comparisonRows.filter(
			(row) =>
				(backendFilter === 'all' || row.backend === backendFilter) &&
				(inputFilter === 'all' || row.input_file === inputFilter)
		)
	);
	const shortClipRows = $derived(
		buildShortClipRows(filteredComparisonRows, data.insights.duration_buckets.short_clip_max_seconds)
	);
	const longClipRows = $derived(
		buildLongClipRows(filteredComparisonRows, data.insights.duration_buckets.long_clip_min_seconds)
	);
	const backendRows = $derived.by(() => {
		const source =
			inputFilter === 'all'
				? comparisonInsights.backend_breakdown
				: comparisonInsights.backend_input_breakdown.filter((row) => row.input_file === inputFilter);
		return source.filter((row) => backendFilter === 'all' || row.backend === backendFilter);
	});

	function setComparisonType(value: AcftComparisonType) {
		comparisonType = value;
		backendFilter = 'all';
		inputFilter = 'all';
	}

	function signed(value: number | null | undefined, digits = 3, percent = false): string {
		if (value == null || Number.isNaN(value)) {
			return 'n/a';
		}

		const rendered = percent ? `${(value * 100).toFixed(digits)}%` : value.toFixed(digits);
		return value > 0 ? `+${rendered}` : rendered;
	}

	function meanValue(rows: AcftComparisonPairRow[], key: keyof AcftComparisonPairRow): number | null {
		const values = rows
			.map((row) => row[key])
			.filter((value): value is number => typeof value === 'number' && !Number.isNaN(value));

		if (values.length === 0) {
			return null;
		}

		return values.reduce((total, value) => total + value, 0) / values.length;
	}

	function medianValue(rows: AcftComparisonPairRow[], key: keyof AcftComparisonPairRow): number | null {
		const values = rows
			.map((row) => row[key])
			.filter((value): value is number => typeof value === 'number' && !Number.isNaN(value))
			.sort((left, right) => left - right);

		if (values.length === 0) {
			return null;
		}

		const middle = Math.floor(values.length / 2);
		return values.length % 2 === 0 ? (values[middle - 1] + values[middle]) / 2 : values[middle];
	}

	function modelFamilyFromKey(modelKey: string): string {
		const normalized = modelKey.startsWith('acft.') ? modelKey.slice(5) : modelKey;
		return normalized.split('.')[0] ?? normalized;
	}

	function summarizeFocusRows(rows: AcftComparisonPairRow[]): AcftInsightAggregate {
		return {
			count: rows.length,
			durations: Array.from(new Set(rows.map((row) => row.audio_duration_seconds))).sort((left, right) => left - right),
			mean_left_runtime_seconds: meanValue(rows, 'left_runtime_seconds'),
			mean_right_runtime_seconds: meanValue(rows, 'right_runtime_seconds'),
			mean_left_word_error_rate: meanValue(rows, 'left_word_error_rate'),
			mean_right_word_error_rate: meanValue(rows, 'right_word_error_rate'),
			mean_left_char_error_rate: meanValue(rows, 'left_char_error_rate'),
			mean_right_char_error_rate: meanValue(rows, 'right_char_error_rate'),
			mean_delta_runtime_seconds: meanValue(rows, 'delta_runtime_seconds'),
			median_delta_runtime_seconds: medianValue(rows, 'delta_runtime_seconds'),
			mean_relative_runtime_change: meanValue(rows, 'relative_runtime_change'),
			mean_delta_word_error_rate: meanValue(rows, 'delta_word_error_rate'),
			mean_delta_char_error_rate: meanValue(rows, 'delta_char_error_rate'),
			mean_delta_punctuation_error_rate: meanValue(rows, 'delta_punctuation_error_rate'),
			mean_delta_capitalization_accuracy: meanValue(rows, 'delta_capitalization_accuracy'),
			mean_delta_encode_ms: meanValue(rows, 'delta_encode_ms'),
			mean_delta_batchd_ms: meanValue(rows, 'delta_batchd_ms')
		};
	}

	function buildModelRows(
		rows: AcftComparisonPairRow[],
		predicate: (row: AcftComparisonPairRow) => boolean,
		sortRows: (left: AcftInsightModelRow, right: AcftInsightModelRow) => number,
		limit = 8
	): AcftInsightModelRow[] {
		const grouped = new Map<string, AcftComparisonPairRow[]>();

		for (const row of rows) {
			if (!predicate(row)) {
				continue;
			}

			const existing = grouped.get(row.model_key) ?? [];
			existing.push(row);
			grouped.set(row.model_key, existing);
		}

		const results = Array.from(grouped.entries()).map(([modelKey, groupRows]) => ({
			model_key: modelKey,
			model_family: modelFamilyFromKey(modelKey),
			left_models: Array.from(new Set(groupRows.map((row) => row.left_model))).sort(),
			backends: Array.from(new Set(groupRows.map((row) => row.backend))).sort(),
			inputs: Array.from(new Set(groupRows.map((row) => row.input_file))).sort(),
			...summarizeFocusRows(groupRows)
		}));

		results.sort(sortRows);
		return results.slice(0, limit);
	}

	function metricOrInfinity(value: number | null | undefined, fallback: number): number {
		return value == null || Number.isNaN(value) ? fallback : value;
	}

	function buildShortClipRows(rows: AcftComparisonPairRow[], shortClipMaxSeconds: number): AcftInsightModelRow[] {
		return buildModelRows(
			rows,
			(row) => row.audio_duration_seconds <= shortClipMaxSeconds,
			(left, right) => {
				const runtimeOrder = metricOrInfinity(left.mean_delta_runtime_seconds, Number.POSITIVE_INFINITY) - metricOrInfinity(right.mean_delta_runtime_seconds, Number.POSITIVE_INFINITY);
				if (runtimeOrder !== 0) {
					return runtimeOrder;
				}

				return metricOrInfinity(left.mean_relative_runtime_change, Number.POSITIVE_INFINITY) - metricOrInfinity(right.mean_relative_runtime_change, Number.POSITIVE_INFINITY);
			}
		);
	}

	function buildLongClipRows(rows: AcftComparisonPairRow[], longClipMinSeconds: number): AcftInsightModelRow[] {
		return buildModelRows(
			rows,
			(row) => row.audio_duration_seconds >= longClipMinSeconds,
			(left, right) => {
				const werOrder = metricOrInfinity(right.mean_delta_word_error_rate, Number.NEGATIVE_INFINITY) - metricOrInfinity(left.mean_delta_word_error_rate, Number.NEGATIVE_INFINITY);
				if (werOrder !== 0) {
					return werOrder;
				}

				const cerOrder = metricOrInfinity(right.mean_delta_char_error_rate, Number.NEGATIVE_INFINITY) - metricOrInfinity(left.mean_delta_char_error_rate, Number.NEGATIVE_INFINITY);
				if (cerOrder !== 0) {
					return cerOrder;
				}

				return metricOrInfinity(right.mean_delta_punctuation_error_rate, Number.NEGATIVE_INFINITY) - metricOrInfinity(left.mean_delta_punctuation_error_rate, Number.NEGATIVE_INFINITY);
			}
		);
	}

	function metricParts(
		left: number | null | undefined,
		right: number | null | undefined,
		delta: number | null | undefined,
		digits: number,
		percent = false
	): { delta: string; details: string | null } {
		if (
			left == null ||
			right == null ||
			delta == null ||
			Number.isNaN(left) ||
			Number.isNaN(right) ||
			Number.isNaN(delta)
		) {
			return { delta: 'n/a', details: null };
		}

		const formatValue = (value: number) => (percent ? `${(value * 100).toFixed(digits)}%` : value.toFixed(digits));
		return {
			delta: signed(delta, digits, percent),
			details: `(${formatValue(left)} - ${formatValue(right)})`
		};
	}

	function runtimeDetail(record: AcftInsightAggregate): { delta: string; details: string | null } {
		return metricParts(
			record.mean_left_runtime_seconds as number | null | undefined,
			record.mean_right_runtime_seconds as number | null | undefined,
			record.mean_delta_runtime_seconds as number | null | undefined,
			3
		);
	}

	function werDetail(record: AcftInsightAggregate): { delta: string; details: string | null } {
		return metricParts(
			record.mean_left_word_error_rate as number | null | undefined,
			record.mean_right_word_error_rate as number | null | undefined,
			record.mean_delta_word_error_rate as number | null | undefined,
			4
		);
	}

	function cerDetail(record: AcftInsightAggregate): { delta: string; details: string | null } {
		return metricParts(
			record.mean_left_char_error_rate as number | null | undefined,
			record.mean_right_char_error_rate as number | null | undefined,
			record.mean_delta_char_error_rate as number | null | undefined,
			4
		);
	}
</script>

<svelte:head>
	<title>ACFT Insights | Whisper Bench Explorer</title>
	<meta
		name="description"
		content="Persisted ACFT spotlights for short-clip speed wins, long-clip regressions, and backend breakpoints."
	/>
</svelte:head>

<div class="insights-page">
	<section class="intro panel">
		<div>
			<p class="eyebrow">Persisted spotlights</p>
			<h1>Keep the ACFT trouble spots and best wins visible without recomputing them.</h1>
		</div>
		<div class="intro-copy">
			<p>
				This page reads the persisted ACFT insights artifact. It focuses on the models with the
				largest short-clip speed wins, the worst long-clip quality regressions, and the backend
				boundaries where ACFT stops helping.
			</p>
			<div class="bucket-note">
				<div>
					<span>Short clips</span>
					<strong>up to {data.insights.duration_buckets.short_clip_max_seconds.toFixed(1)} s</strong>
				</div>
				<div>
					<span>Long clips</span>
					<strong>{data.insights.duration_buckets.long_clip_min_seconds.toFixed(1)} s and above</strong>
				</div>
			</div>
		</div>
	</section>

	<section class="selector-grid">
		{#each data.comparisonOptions as option}
			<button
				type="button"
				class:selected={comparisonType === option.value}
				onclick={() => setComparisonType(option.value)}
			>
				<strong>{option.label}</strong>
				<span>{option.description}</span>
			</button>
		{/each}
	</section>

	<section class="filters panel">
		<div class="tool-grid">
			<label>
				<span>Backend</span>
				<select bind:value={backendFilter}>
					<option value="all">All backends</option>
					{#each backendOptions as backend}
						<option value={backend}>{backend}</option>
					{/each}
				</select>
			</label>

			<label>
				<span>Input</span>
				<select bind:value={inputFilter}>
					<option value="all">All inputs</option>
					{#each inputOptions as input}
						<option value={input}>{input}</option>
					{/each}
				</select>
			</label>

			<div class="tool-summary">
				<span>Visible short wins</span>
				<strong>{shortClipRows.length}</strong>
			</div>

			<div class="tool-summary">
				<span>Visible regressions</span>
				<strong>{longClipRows.length}</strong>
			</div>
		</div>
	</section>

	<section class="split-layout">
		<article class="panel">
			<div class="panel-header">
				<div>
					<p class="eyebrow">Short clips</p>
					<h2>Biggest speed wins</h2>
				</div>
				<p>{selectedOption.label}</p>
			</div>

			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th class="text-col" rowspan="2">Model</th>
							<th class="num-col" rowspan="2">Rows</th>
							<th class="num-col" colspan="2">Runtime</th>
							<th class="num-col" rowspan="2">WER</th>
							<th class="num-col" rowspan="2">CER</th>
						</tr>
						<tr class="subhead-row">
							<th class="num-col">Delta</th>
							<th class="num-col">Rel</th>
						</tr>
					</thead>
					<tbody>
						{#each shortClipRows as row}
							<tr>
								<td class="text-col">
									<strong>{row.model_key}</strong>
									<span>{row.model_family} family</span>
									<small>{row.backends.join(', ')}</small>
									<small>{row.inputs.join(', ')}</small>
								</td>
								<td class="num-col">{row.count}</td>
								<td class="num-col metric-detail">
									<div class="metric-stack">
										<strong>{runtimeDetail(row).delta}</strong>
										{#if runtimeDetail(row).details}
											<span>{runtimeDetail(row).details}</span>
										{/if}
									</div>
								</td>
								<td class="num-col">{signed(row.mean_relative_runtime_change, 1, true)}</td>
								<td class="num-col metric-detail">
									<div class="metric-stack">
										<strong>{werDetail(row).delta}</strong>
										{#if werDetail(row).details}
											<span>{werDetail(row).details}</span>
										{/if}
									</div>
								</td>
								<td class="num-col metric-detail">
									<div class="metric-stack">
										<strong>{cerDetail(row).delta}</strong>
										{#if cerDetail(row).details}
											<span>{cerDetail(row).details}</span>
										{/if}
									</div>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</article>

		<article class="panel">
			<div class="panel-header">
				<div>
					<p class="eyebrow">Long clips</p>
					<h2>Worst quality regressions</h2>
				</div>
				<p>Sorted by higher positive WER and CER deltas on long-duration rows.</p>
			</div>

			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th class="text-col" rowspan="2">Model</th>
							<th class="num-col" rowspan="2">Rows</th>
							<th class="num-col" rowspan="2">WER</th>
							<th class="num-col" rowspan="2">CER</th>
							<th class="num-col" colspan="2">Runtime</th>
						</tr>
						<tr class="subhead-row">
							<th class="num-col">Delta</th>
							<th class="num-col">Rel</th>
						</tr>
					</thead>
					<tbody>
						{#each longClipRows as row}
							<tr>
								<td class="text-col">
									<strong>{row.model_key}</strong>
									<span>{row.model_family} family</span>
									<small>{row.backends.join(', ')}</small>
									<small>{row.inputs.join(', ')}</small>
								</td>
								<td class="num-col">{row.count}</td>
								<td class="num-col metric-detail">
									<div class="metric-stack">
										<strong>{werDetail(row).delta}</strong>
										{#if werDetail(row).details}
											<span>{werDetail(row).details}</span>
										{/if}
									</div>
								</td>
								<td class="num-col metric-detail">
									<div class="metric-stack">
										<strong>{cerDetail(row).delta}</strong>
										{#if cerDetail(row).details}
											<span>{cerDetail(row).details}</span>
										{/if}
									</div>
								</td>
								<td class="num-col metric-detail">
									<div class="metric-stack">
										<strong>{runtimeDetail(row).delta}</strong>
										{#if runtimeDetail(row).details}
											<span>{runtimeDetail(row).details}</span>
										{/if}
									</div>
								</td>
								<td class="num-col">{signed(row.mean_relative_runtime_change, 1, true)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</article>
	</section>

	<section class="panel">
		<div class="panel-header">
			<div>
				<p class="eyebrow">Backends</p>
				<h2>Where ACFT still helps and where it stops helping</h2>
			</div>
			<p>
				The short bucket captures the speed win that matters for latency. The long bucket shows
				whether that win survives beyond {data.insights.duration_buckets.long_clip_min_seconds.toFixed(1)} seconds.
			</p>
		</div>

		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th class="text-col">Backend</th>
						<th class="text-col">Input</th>
						<th class="num-col">Short rt</th>
						<th class="num-col">Short WER</th>
						<th class="num-col">Short CER</th>
						<th class="num-col">Long rt</th>
						<th class="num-col">Long WER</th>
						<th class="num-col">Long CER</th>
						<th class="text-col">State</th>
					</tr>
					</thead>
					<tbody>
						{#each backendRows as row}
							<tr>
								<td class="text-col">
									<strong>{row.backend}</strong>
									<span>{row.all_rows} matched rows</span>
								</td>
								<td class="text-col">{row.input_file ?? 'all inputs'}</td>
								<td class="num-col metric-detail">
									<div class="metric-stack">
										<strong>{runtimeDetail(row.short_clip).delta}</strong>
										{#if runtimeDetail(row.short_clip).details}
											<span>{runtimeDetail(row.short_clip).details}</span>
										{/if}
									</div>
								</td>
								<td class="num-col metric-detail">
									<div class="metric-stack">
										<strong>{werDetail(row.short_clip).delta}</strong>
										{#if werDetail(row.short_clip).details}
											<span>{werDetail(row.short_clip).details}</span>
										{/if}
									</div>
								</td>
								<td class="num-col metric-detail">
									<div class="metric-stack">
										<strong>{cerDetail(row.short_clip).delta}</strong>
										{#if cerDetail(row.short_clip).details}
											<span>{cerDetail(row.short_clip).details}</span>
										{/if}
									</div>
								</td>
								<td class="num-col metric-detail">
									<div class="metric-stack">
										<strong>{runtimeDetail(row.long_clip).delta}</strong>
										{#if runtimeDetail(row.long_clip).details}
											<span>{runtimeDetail(row.long_clip).details}</span>
										{/if}
									</div>
								</td>
								<td class="num-col metric-detail">
									<div class="metric-stack">
										<strong>{werDetail(row.long_clip).delta}</strong>
										{#if werDetail(row.long_clip).details}
											<span>{werDetail(row.long_clip).details}</span>
										{/if}
									</div>
								</td>
								<td class="num-col metric-detail">
									<div class="metric-stack">
										<strong>{cerDetail(row.long_clip).delta}</strong>
										{#if cerDetail(row.long_clip).details}
											<span>{cerDetail(row.long_clip).details}</span>
										{/if}
									</div>
								</td>
								<td class="text-col breakpoint-cell">
									{#if row.stops_helping_on_long_clips}
										<strong class="warning">Stops helping</strong>
									{:else if row.long_clip.helps_left_side}
										<strong class="success">Still helps</strong>
									{:else}
										<strong>Mixed / flat</strong>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
</div>

<style>
	.insights-page {
		padding: 1rem;
		display: grid;
		gap: 0.8rem;
	}

	.panel,
	.selector-grid button {
		border: 1px solid var(--border);
		background: var(--bg-panel);
		backdrop-filter: blur(10px);
		box-shadow: 0 14px 36px rgba(0, 0, 0, 0.24);
	}

	.panel {
		padding: 0.9rem;
		border-radius: 1rem;
	}

	.intro {
		display: grid;
		grid-template-columns: minmax(0, 1.5fr) minmax(18rem, 1fr);
		gap: 0.9rem;
		align-items: end;
	}

	h1,
	h2 {
		font-family: 'Fraunces', Georgia, serif;
		margin: 0;
		line-height: 1;
	}

	h1 {
		font-size: clamp(1.8rem, 3vw, 3rem);
	}

	.eyebrow {
		text-transform: uppercase;
		letter-spacing: 0.12em;
		font-size: 0.66rem;
		font-weight: 700;
		margin: 0 0 0.35rem;
		color: var(--accent);
	}

	.intro-copy {
		display: grid;
		gap: 0.75rem;
	}

	.bucket-note {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 0.6rem;
	}

	.bucket-note div {
		padding: 0.75rem;
		border-radius: 0.9rem;
		background: var(--bg-soft);
		border: 1px solid var(--border);
	}

	.bucket-note span,
		.text-col span,
		.text-col small {
		display: block;
		color: var(--text-muted);
	}

	.text-col span,
	.text-col small {
		line-height: 1.15;
	}

	.text-col small {
		font-size: 0.8rem;
		margin-top: 0.08rem;
	}

	.selector-grid {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 0.75rem;
	}

	.filters .tool-grid {
		display: grid;
		grid-template-columns: repeat(4, minmax(0, 1fr));
		gap: 0.75rem;
		align-items: end;
	}

	label {
		display: grid;
		gap: 0.35rem;
	}

	label span {
		font-size: 0.76rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--text-dim);
	}

	select {
		border-radius: 0.7rem;
		border: 1px solid var(--border);
		background: rgba(255, 255, 255, 0.04);
		color: var(--text);
		padding: 0.65rem 0.75rem;
		font: inherit;
	}

	.tool-summary {
		padding: 0.75rem;
		border-radius: 0.9rem;
		background: var(--bg-soft);
		border: 1px solid var(--border);
	}

	.tool-summary span {
		display: block;
		color: var(--text-muted);
		font-size: 0.8rem;
	}

	.tool-summary strong {
		display: block;
		margin-top: 0.18rem;
	}

	.selector-grid button {
		padding: 0.9rem;
		border-radius: 1rem;
		color: var(--text);
		text-align: left;
		cursor: pointer;
	}

	.selector-grid button.selected {
		border-color: var(--accent);
		background: linear-gradient(180deg, rgba(94, 195, 255, 0.18), rgba(17, 25, 36, 0.9));
	}

	.selector-grid strong,
	.selector-grid span {
		display: block;
	}

	.selector-grid span {
		margin-top: 0.35rem;
		font-size: 0.88rem;
		line-height: 1.4;
		color: var(--text-muted);
	}

	.split-layout {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 0.8rem;
	}

	.panel-header {
		display: flex;
		justify-content: space-between;
		gap: 1rem;
		align-items: start;
		margin-bottom: 0.75rem;
		text-align: center;
	}

	.panel-header p:last-child {
		margin: 0;
		max-width: 32rem;
		color: var(--text-muted);
		line-height: 1.45;
	}

	.table-wrap {
		overflow-x: auto;
	}

	table {
		width: 100%;
		border-collapse: collapse;
		min-width: 42rem;
	}

	th,
	td {
		padding: 0.4rem 0.45rem;
		border-bottom: 1px solid var(--border);
		vertical-align: top;
		text-align: center;
	}

	th {
		font-size: 0.74rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--text-dim);
	}

	.subhead-row th {
		font-size: 0.68rem;
		letter-spacing: 0.1em;
		padding-top: 0;
	}

	.text-col {
		text-align: center;
	}

	.metric-detail {
		font-size: 0.84rem;
		line-height: 1.05;
	}

	.metric-stack {
		display: inline-grid;
		justify-items: center;
		gap: 0.05rem;
	}

	.metric-stack strong {
		font-size: 0.94rem;
		font-weight: 700;
		line-height: 1;
	}

	.metric-stack span {
		color: var(--text-muted);
		font-size: 0.75rem;
		line-height: 1.05;
		white-space: nowrap;
		white-space: nowrap;
	}

	.num-col {
		text-align: center;
		font-variant-numeric: tabular-nums;
	}

	.warning {
		color: var(--warning);
	}

	.success {
		color: var(--success);
	}

	.breakpoint-cell strong {
		display: inline-block;
	}

	@media (max-width: 1500px) {
		.split-layout {
			grid-template-columns: 1fr;
		}
	}

	@media (max-width: 1080px) {
		.intro,
		.selector-grid,
		.bucket-note,
		.filters .tool-grid {
			grid-template-columns: 1fr;
		}

		.panel-header {
			flex-direction: column;
			align-items: start;
		}
	}
</style>