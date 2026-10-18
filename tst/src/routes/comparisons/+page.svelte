<script lang="ts">
	import type { PageData } from './$types';
	import type { AcftComparisonPairRow, AcftComparisonType } from '$lib';

	type ComparisonViewMode = 'pairs' | 'models';
	type PrimarySortMetric = 'wer' | 'char' | 'punctuation';
	type ComparisonDisplayRow = AcftComparisonPairRow & {
		pair_count: number;
		backends: string[];
		inputs: string[];
		left_models: string[];
		right_models: string[];
	};

	let { data }: { data: PageData } = $props();
	let comparisonType = $state<AcftComparisonType>('audio_ctx_only');
	let search = $state('');
	let backendFilter = $state<'all' | string>('all');
	let selectedInputs = $state<string[]>([]);
	let modelKeyFilter = $state<'all' | string>('all');
	let viewMode = $state<ComparisonViewMode>('pairs');
	let primarySortMetrics = $state<PrimarySortMetric[]>(['wer']);
	let sortKey = $state<'audio' | 'runtime' | 'relative' | 'wer' | 'char' | 'punctuation'>('runtime');
	let sortDirection = $state<'asc' | 'desc'>('asc');

	const primarySortOptions: Array<{ key: PrimarySortMetric; label: string }> = [
		{ key: 'wer', label: 'WER' },
		{ key: 'char', label: 'CER' },
		{ key: 'punctuation', label: 'Punct' }
	];

	const selectedOption = $derived(
		data.comparisonOptions.find((option) => option.value === comparisonType) ?? data.comparisonOptions[0]
	);
	const rows = $derived(data.rowsByType[comparisonType] ?? []);
	const backendOptions = $derived(Array.from(new Set(rows.map((row) => row.backend))).sort());
	const inputOptions = $derived(Array.from(new Set(rows.map((row) => row.input_file))).sort());
	const activeInputFilters = $derived.by(() => {
		const validSelections = selectedInputs.filter((input) => inputOptions.includes(input));
		return validSelections.length > 0 ? validSelections : inputOptions;
	});
	const modelKeyOptions = $derived(Array.from(new Set(rows.map((row) => row.model_key))).sort());
	const summary = $derived(data.summary.comparison_summaries[comparisonType]);
	const durationBreakdown = $derived(data.durationAnalysis[comparisonType]);
	const comparisonCards = $derived(data.comparisonCards[comparisonType]);
	const groupedByBackend = $derived(data.summary.grouped_by_backend[comparisonType]);
	const groupedByModelKey = $derived(data.summary.grouped_by_model_key[comparisonType]);
	const topModelKeyRows = $derived(
		Object.entries(groupedByModelKey)
			.sort((left, right) => {
				const leftMetric =
					(left[1].delta_runtime_seconds as { mean?: number } | undefined)?.mean ??
					Number.POSITIVE_INFINITY;
				const rightMetric =
					(right[1].delta_runtime_seconds as { mean?: number } | undefined)?.mean ??
					Number.POSITIVE_INFINITY;
				return leftMetric - rightMetric;
			})
			.slice(0, 6)
	);

	const baseFilteredRows = $derived.by(() => {
		const needle = search.trim().toLowerCase();
		return rows.filter((row) => {
			const matchesSearch =
				needle.length === 0 ||
				row.model_key.toLowerCase().includes(needle) ||
				row.left_model.toLowerCase().includes(needle) ||
				row.right_model.toLowerCase().includes(needle) ||
				row.backend.toLowerCase().includes(needle) ||
				row.input_file.toLowerCase().includes(needle);
			const matchesBackend = backendFilter === 'all' || row.backend === backendFilter;
			const matchesInput = activeInputFilters.length === 0 || activeInputFilters.includes(row.input_file);
			const matchesModelKey = modelKeyFilter === 'all' || row.model_key === modelKeyFilter;
			return matchesSearch && matchesBackend && matchesInput && matchesModelKey;
		});
	});

	const averagedRows = $derived.by(() => buildAveragedRows(baseFilteredRows));

	const filteredRows = $derived.by(() => {
		const source = viewMode === 'models' ? averagedRows : buildPairDisplayRows(baseFilteredRows);
		return [...source].sort((left, right) => compareRows(left, right, sortKey, sortDirection));
	});

	function averagePrimaryMetricValue(row: ComparisonDisplayRow, metrics: PrimarySortMetric[]): number {
		const values = metrics.map((metric) => metricValue(row, metric));
		return values.reduce((total, value) => total + value, 0) / values.length;
	}

	function numericSortValue(value: number | null | undefined): number {
		return value == null || Number.isNaN(value) ? Number.POSITIVE_INFINITY : value;
	}

	function metricValue(row: ComparisonDisplayRow, key: typeof sortKey): number {
		switch (key) {
			case 'audio':
				return row.audio_duration_seconds;
			case 'relative':
				return numericSortValue(row.relative_runtime_change);
			case 'wer':
				return viewMode === 'models'
					? numericSortValue(row.left_word_error_rate as number | null | undefined)
					: numericSortValue(row.delta_word_error_rate as number | null | undefined);
			case 'char':
				return viewMode === 'models'
					? numericSortValue(row.left_char_error_rate as number | null | undefined)
					: numericSortValue(row.delta_char_error_rate as number | null | undefined);
			case 'punctuation':
				return viewMode === 'models'
					? numericSortValue(row.left_punctuation_error_rate as number | null | undefined)
					: numericSortValue(row.delta_punctuation_error_rate as number | null | undefined);
			case 'runtime':
			default:
				return viewMode === 'models'
					? numericSortValue(row.left_runtime_seconds)
					: numericSortValue(row.delta_runtime_seconds as number | null | undefined);
		}
	}

	function compareRows(
		left: ComparisonDisplayRow,
		right: ComparisonDisplayRow,
		key: typeof sortKey,
		direction: 'asc' | 'desc'
	): number {
		if (primarySortMetrics.length > 0) {
			const primaryResult =
				averagePrimaryMetricValue(left, primarySortMetrics) -
				averagePrimaryMetricValue(right, primarySortMetrics);
			if (primaryResult !== 0) {
				return primaryResult;
			}
		}

		let result = 0;

		if (key === 'audio') {
			result = left.audio_duration_seconds - right.audio_duration_seconds;
		} else {
			result = metricValue(left, key) - metricValue(right, key);
		}

		result = direction === 'asc' ? result : result * -1;

		if (result !== 0) {
			return result;
		}

		const runtimeTieBreak =
			numericSortValue(left.left_runtime_seconds) - numericSortValue(right.left_runtime_seconds);
		if (runtimeTieBreak !== 0) {
			return runtimeTieBreak;
		}

		return left.model_key.localeCompare(right.model_key) || left.backend.localeCompare(right.backend);
	}

	function setComparisonType(value: AcftComparisonType) {
		comparisonType = value;
		viewMode = 'pairs';
		backendFilter = 'all';
		selectedInputs = [];
		modelKeyFilter = 'all';
		search = '';
		primarySortMetrics = ['wer'];
		sortKey = 'runtime';
		sortDirection = 'asc';
	}

	function togglePrimarySortMetric(metric: PrimarySortMetric) {
		if (primarySortMetrics.includes(metric)) {
			primarySortMetrics = primarySortMetrics.filter((value) => value !== metric);
			return;
		}

		primarySortMetrics = primarySortOptions
			.map((option) => option.key)
			.filter((key) => key === metric || primarySortMetrics.includes(key));
	}

	function toggleInputSelection(input: string) {
		const currentSelections = activeInputFilters;

		if (currentSelections.includes(input)) {
			if (currentSelections.length === 1) {
				return;
			}

			const nextSelections = currentSelections.filter((value) => value !== input);
			selectedInputs = nextSelections.length === inputOptions.length ? [] : nextSelections;
			return;
		}

		const nextSelections = [...currentSelections, input].sort(
			(left, right) => inputOptions.indexOf(left) - inputOptions.indexOf(right)
		);
		selectedInputs = nextSelections.length === inputOptions.length ? [] : nextSelections;
	}

	function selectAllInputs() {
		selectedInputs = [];
	}

	function averageValue(values: Array<number | null | undefined>): number | null {
		const numericValues = values.filter((value): value is number => value != null && !Number.isNaN(value));
		if (numericValues.length === 0) {
			return null;
		}

		return numericValues.reduce((total, value) => total + value, 0) / numericValues.length;
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

	function runtimeDetail(row: ComparisonDisplayRow): { delta: string; details: string | null } {
		return metricParts(row.left_runtime_seconds, row.right_runtime_seconds, row.delta_runtime_seconds, 3);
	}

	function werDetail(row: ComparisonDisplayRow): { delta: string; details: string | null } {
		return metricParts(
			row.left_word_error_rate as number | null | undefined,
			row.right_word_error_rate as number | null | undefined,
			row.delta_word_error_rate as number | null | undefined,
			4
		);
	}

	function cerDetail(row: ComparisonDisplayRow): { delta: string; details: string | null } {
		return metricParts(
			row.left_char_error_rate as number | null | undefined,
			row.right_char_error_rate as number | null | undefined,
			row.delta_char_error_rate as number | null | undefined,
			4
		);
	}

	function punctuationDetail(row: ComparisonDisplayRow): { delta: string; details: string | null } {
		return metricParts(
			row.left_punctuation_error_rate as number | null | undefined,
			row.right_punctuation_error_rate as number | null | undefined,
			row.delta_punctuation_error_rate as number | null | undefined,
			4
		);
	}

	function buildPairDisplayRows(rows: AcftComparisonPairRow[]): ComparisonDisplayRow[] {
		return rows.map((row) => ({
			...row,
			pair_count: 1,
			backends: [row.backend],
			inputs: [row.input_file],
			left_models: [row.left_model],
			right_models: [row.right_model]
		}));
	}

	function buildAveragedRows(rows: AcftComparisonPairRow[]): ComparisonDisplayRow[] {
		const grouped = new Map<string, AcftComparisonPairRow[]>();

		for (const row of rows) {
			const existing = grouped.get(row.model_key) ?? [];
			existing.push(row);
			grouped.set(row.model_key, existing);
		}

		return Array.from(grouped.entries()).map(([modelKey, groupRows]) => ({
			...groupRows[0],
			backend: groupRows.length === 1 ? groupRows[0].backend : 'mixed',
			input_file: groupRows.length === 1 ? groupRows[0].input_file : 'mixed',
			audio_duration_seconds: averageValue(groupRows.map((row) => row.audio_duration_seconds)) ?? 0,
			left_run_id: `avg-left:${modelKey}`,
			right_run_id: `avg-right:${modelKey}`,
			left_model: modelKey,
			right_model: modelKey,
			relative_runtime_change: averageValue(groupRows.map((row) => row.relative_runtime_change)),
			left_runtime_seconds: averageValue(groupRows.map((row) => row.left_runtime_seconds as number | null | undefined)),
			right_runtime_seconds: averageValue(groupRows.map((row) => row.right_runtime_seconds as number | null | undefined)),
			left_word_error_rate: averageValue(groupRows.map((row) => row.left_word_error_rate as number | null | undefined)),
			right_word_error_rate: averageValue(groupRows.map((row) => row.right_word_error_rate as number | null | undefined)),
			left_char_error_rate: averageValue(groupRows.map((row) => row.left_char_error_rate as number | null | undefined)),
			right_char_error_rate: averageValue(groupRows.map((row) => row.right_char_error_rate as number | null | undefined)),
			left_punctuation_error_rate: averageValue(groupRows.map((row) => row.left_punctuation_error_rate as number | null | undefined)),
			right_punctuation_error_rate: averageValue(groupRows.map((row) => row.right_punctuation_error_rate as number | null | undefined)),
			delta_runtime_seconds: averageValue(groupRows.map((row) => row.delta_runtime_seconds as number | null | undefined)),
			delta_realtime_factor: averageValue(groupRows.map((row) => row.delta_realtime_factor as number | null | undefined)),
			delta_realtime_speed: averageValue(groupRows.map((row) => row.delta_realtime_speed as number | null | undefined)),
			delta_word_error_rate: averageValue(groupRows.map((row) => row.delta_word_error_rate as number | null | undefined)),
			delta_char_error_rate: averageValue(groupRows.map((row) => row.delta_char_error_rate as number | null | undefined)),
			delta_punctuation_error_rate: averageValue(groupRows.map((row) => row.delta_punctuation_error_rate as number | null | undefined)),
			delta_capitalization_accuracy: averageValue(groupRows.map((row) => row.delta_capitalization_accuracy as number | null | undefined)),
			delta_mel_ms: averageValue(groupRows.map((row) => row.delta_mel_ms as number | null | undefined)),
			delta_sample_ms: averageValue(groupRows.map((row) => row.delta_sample_ms as number | null | undefined)),
			delta_decode_ms: averageValue(groupRows.map((row) => row.delta_decode_ms as number | null | undefined)),
			delta_prompt_ms: averageValue(groupRows.map((row) => row.delta_prompt_ms as number | null | undefined)),
			pair_count: groupRows.length,
			backends: Array.from(new Set(groupRows.map((row) => row.backend))).sort(),
			inputs: Array.from(new Set(groupRows.map((row) => row.input_file))).sort(),
			left_models: Array.from(new Set(groupRows.map((row) => row.left_model))).sort(),
			right_models: Array.from(new Set(groupRows.map((row) => row.right_model))).sort()
		}));
	}

	function setViewMode(mode: ComparisonViewMode) {
		viewMode = mode;
	}

	function toggleSort(key: typeof sortKey) {
		if (viewMode === 'models' && key === 'audio') {
			return;
		}

		if (sortKey === key) {
			sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
			return;
		}

		sortKey = key;
		sortDirection = 'asc';
	}

	function signed(value: number | null | undefined, digits = 3, percent = false): string {
		if (value == null || Number.isNaN(value)) {
			return 'n/a';
		}

		const rendered = percent ? `${(value * 100).toFixed(digits)}%` : value.toFixed(digits);
		return value > 0 ? `+${rendered}` : rendered;
	}

	function compactMetric(record: Record<string, unknown> | undefined, field: string): string {
		const value = record?.[field] as { mean?: number } | undefined;
		return signed(value?.mean, field === 'relative_runtime_change' ? 1 : 3, field === 'relative_runtime_change');
	}

	function comparisonLabel(row: ComparisonDisplayRow): string {
		return viewMode === 'models' ? row.model_key : `${row.model_key} | ${row.backends.join(', ')} | ${row.inputs.join(', ')}`;
	}
</script>

<svelte:head>
	<title>ACFT Comparisons | Whisper Bench Explorer</title>
	<meta
		name="description"
		content="Analysis view for ACFT audio-context and ACFT-vs-standard comparison artifacts."
	/>
</svelte:head>

<div class="comparisons-page">
	<section class="intro panel">
		<div>
			<p class="eyebrow">ACFT analysis</p>
			<h1>Inspect where ACFT gains speed, where it stalls, and what quality it gives up.</h1>
		</div>
		<div class="intro-copy">
			<p>
				This screen reads the dedicated ACFT comparison artifacts directly. Use it to inspect
				audio-context deltas, ACFT-vs-standard tradeoffs, and the duration slices behind them.
			</p>
			<a class="insight-link" href="/comparisons/insights">Open persisted ACFT insights</a>
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

	<section class="card-grid">
		{#each comparisonCards as card}
			<article class="metric-card">
				<p>{card.label}</p>
				<h2>{card.value}</h2>
				<span>{card.help}</span>
			</article>
		{/each}
	</section>

	<section class="split-layout">
		<article class="panel">
			<div class="panel-header">
				<div>
					<p class="eyebrow">Trend view</p>
					<h2>{selectedOption.label}</h2>
				</div>
				<p>{durationBreakdown.interpretation.notes}</p>
			</div>

			<div class="trend-note">
				<div>
					<span>Trend</span>
					<strong>{durationBreakdown.interpretation.trend_classification}</strong>
				</div>
				<div>
					<span>Curve fit</span>
					<strong>{durationBreakdown.interpretation.sufficient_for_curve_fit ? 'yes' : 'no'}</strong>
				</div>
				<div>
					<span>Durations</span>
					<strong>{durationBreakdown.unique_duration_values.join(', ')}</strong>
				</div>
			</div>

			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th class="num-col">Duration</th>
							<th class="num-col">Pairs</th>
							<th class="num-col">Mean runtime delta</th>
							<th class="num-col">Mean relative runtime</th>
							<th class="num-col">Mean WER delta</th>
							<th class="num-col">Mean CER delta</th>
						</tr>
					</thead>
					<tbody>
						{#each durationBreakdown.per_duration as row}
							<tr>
								<td class="num-col">{row.audio_duration_seconds.toFixed(2)} s</td>
								<td class="num-col">{row.count}</td>
								<td class="num-col">{compactMetric(row, 'delta_runtime_seconds')}</td>
								<td class="num-col">{compactMetric(row, 'relative_runtime_change')}</td>
								<td class="num-col">{compactMetric(row, 'delta_word_error_rate')}</td>
								<td class="num-col">{compactMetric(row, 'delta_char_error_rate')}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</article>

		<article class="panel">
			<div class="panel-header">
				<div>
					<p class="eyebrow">Grouped summary</p>
					<h2>Backend and model slices</h2>
				</div>
				<p>Use these aggregates to see whether the overall effect is broad or isolated.</p>
			</div>

			<div class="mini-grid">
				<div class="summary-box">
					<h3>By backend</h3>
					<ul>
						{#each Object.entries(groupedByBackend) as [backend, record]}
							<li>
								<strong>{backend}</strong>
								<span>runtime {compactMetric(record, 'delta_runtime_seconds')}</span>
								<span>WER {compactMetric(record, 'delta_word_error_rate')}</span>
							</li>
						{/each}
					</ul>
				</div>

				<div class="summary-box">
					<h3>Top model keys by speedup</h3>
					<ul>
						{#each topModelKeyRows as entry}
							<li>
								<strong>{entry[0]}</strong>
								<span>runtime {compactMetric(entry[1], 'delta_runtime_seconds')}</span>
								<span>WER {compactMetric(entry[1], 'delta_word_error_rate')}</span>
							</li>
						{/each}
					</ul>
				</div>
			</div>
		</article>
	</section>

	<section class="filters panel">
		<div class="tool-grid">
			<fieldset class="view-mode">
				<legend>Mode</legend>
				<label>
					<input type="radio" name="comparison-view-mode" checked={viewMode === 'pairs'} onchange={() => setViewMode('pairs')} />
					<span>Pairs</span>
				</label>
				<label>
					<input type="radio" name="comparison-view-mode" checked={viewMode === 'models'} onchange={() => setViewMode('models')} />
					<span>Average by model</span>
				</label>
			</fieldset>

			<label>
				<span>Search</span>
				<input bind:value={search} placeholder="model key, backend, input" />
			</label>

			<label>
				<span>Backend</span>
				<select bind:value={backendFilter}>
					<option value="all">All backends</option>
					{#each backendOptions as backend}
						<option value={backend}>{backend}</option>
					{/each}
				</select>
			</label>

			<fieldset class="multi-filter">
				<div class="filter-head">
					<legend>Inputs</legend>
					<button type="button" class="filter-reset" onclick={selectAllInputs}>All</button>
				</div>
				<div class="check-grid">
					{#each inputOptions as input}
						<label class="check-pill">
							<input
								type="checkbox"
								checked={activeInputFilters.includes(input)}
								onchange={() => toggleInputSelection(input)}
							/>
							<span>{input}</span>
						</label>
					{/each}
				</div>
				<small>
					{#if activeInputFilters.length === inputOptions.length}
						All inputs included
					{:else}
						{activeInputFilters.length} of {inputOptions.length} inputs included
					{/if}
				</small>
			</fieldset>

			<label>
				<span>Model key</span>
				<select bind:value={modelKeyFilter}>
					<option value="all">All model keys</option>
					{#each modelKeyOptions as modelKey}
						<option value={modelKey}>{modelKey}</option>
					{/each}
				</select>
			</label>

			<div class="tool-actions">
				<span>{viewMode === 'models' ? 'Visible models' : 'Visible pairs'}</span>
				<strong>{filteredRows.length}</strong>
				<small>{summary.count} total in this comparison view</small>
			</div>
		</div>
	</section>

	<section class="panel">
		<div class="panel-header">
			<div>
				<p class="eyebrow">{viewMode === 'models' ? 'Model averages' : 'Pair rows'}</p>
				<h2>{viewMode === 'models' ? 'Averaged model slices' : 'Matched runs'}</h2>
			</div>
			<p>
				Delta convention is always left - right. For runtime and error metrics, negative usually
				means the left side is better.
			</p>
		</div>

		<div class="sort-row">
			<span>Primary sort</span>
			<div>
				{#each primarySortOptions as option}
					<button
						type="button"
						class:active={primarySortMetrics.includes(option.key)}
						onclick={() => togglePrimarySortMetric(option.key)}
					>
						{option.label}
					</button>
				{/each}
			</div>
		</div>

		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th class="text-col">Match</th>
						{#if viewMode === 'pairs'}
							<th class="num-col">
								<button type="button" class="sorter num-col" onclick={() => toggleSort('audio')}>Duration</button>
							</th>
						{/if}
						<th class="num-col">
							<button type="button" class="sorter num-col" onclick={() => toggleSort('runtime')}>
								{viewMode === 'models' ? 'Runtime' : 'Runtime delta'}
							</button>
						</th>
						<th class="num-col">
							<button type="button" class="sorter num-col" onclick={() => toggleSort('relative')}>Relative runtime</button>
						</th>
						<th class="num-col">
							<button type="button" class="sorter num-col" onclick={() => toggleSort('wer')}>
								{viewMode === 'models' ? 'WER' : 'WER delta'}
							</button>
						</th>
						<th class="num-col">
							<button type="button" class="sorter num-col" onclick={() => toggleSort('char')}>
								{viewMode === 'models' ? 'CER' : 'CER delta'}
							</button>
						</th>
						<th class="num-col">
							<button type="button" class="sorter num-col" onclick={() => toggleSort('punctuation')}>
								{viewMode === 'models' ? 'Punct' : 'Punct delta'}
							</button>
						</th>
					</tr>
					</thead>
					<tbody>
						{#each filteredRows as row}
							<tr>
								<td class="text-col match-col">
									<strong>{comparisonLabel(row)}</strong>
									<span>{viewMode === 'models' ? `${row.pair_count} pairs averaged` : `${row.left_model} (${row.left_variant ?? 'default'})`}</span>
									<span>{viewMode === 'models' ? `inputs: ${row.inputs.join(', ')}` : `${row.right_model} (${row.right_variant ?? 'default'})`}</span>
								</td>
								{#if viewMode === 'pairs'}
									<td class="num-col">{row.audio_duration_seconds.toFixed(2)} s</td>
								{/if}
								<td class="num-col metric-detail">
									<div class="metric-stack">
										<strong>{runtimeDetail(row).delta}</strong>
										{#if runtimeDetail(row).details}
											<span>{runtimeDetail(row).details}</span>
										{/if}
									</div>
								</td>
								<td class="num-col">{signed(row.relative_runtime_change, 1, true)}</td>
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
										<strong>{punctuationDetail(row).delta}</strong>
										{#if punctuationDetail(row).details}
											<span>{punctuationDetail(row).details}</span>
										{/if}
									</div>
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</section>
</div>

<style>
	.comparisons-page {
		padding: 1rem;
		display: grid;
		gap: 0.8rem;
	}

	.panel,
	.metric-card,
	.selector-grid button {
		border: 1px solid var(--border);
		background: var(--bg-panel);
		backdrop-filter: blur(10px);
		box-shadow: 0 14px 36px rgba(0, 0, 0, 0.24);
	}

	.panel,
	.metric-card {
		padding: 0.9rem;
		border-radius: 1rem;
	}

	.intro {
		display: grid;
		grid-template-columns: minmax(0, 1.5fr) minmax(18rem, 1fr);
		gap: 0.9rem;
		align-items: end;
	}

	.intro-copy {
		display: grid;
		gap: 0.75rem;
	}

	.insight-link {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: fit-content;
		padding: 0.55rem 0.8rem;
		border-radius: 999px;
		text-decoration: none;
		background: var(--accent-soft);
		border: 1px solid var(--border);
		font-weight: 700;
	}

	h1,
	h2,
	h3 {
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

	.selector-grid {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 0.75rem;
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
		color: var(--text-muted);
		font-size: 0.88rem;
		line-height: 1.4;
	}

	.card-grid {
		display: grid;
		grid-template-columns: repeat(5, minmax(0, 1fr));
		gap: 0.75rem;
	}

	.metric-card p,
	.metric-card span {
		margin: 0;
	}

	.metric-card p {
		text-transform: uppercase;
		letter-spacing: 0.12em;
		font-size: 0.66rem;
		font-weight: 700;
		color: var(--accent);
	}

	.metric-card h2 {
		margin: 0.35rem 0;
		font-size: 1.5rem;
	}

	.metric-card span {
		color: var(--text-muted);
		font-size: 0.84rem;
		line-height: 1.45;
	}

	.split-layout {
		display: grid;
		grid-template-columns: minmax(0, 1.35fr) minmax(20rem, 0.95fr);
		gap: 0.8rem;
	}

	.panel-header {
		display: flex;
		justify-content: space-between;
		gap: 1rem;
		align-items: start;
		margin-bottom: 0.75rem;
	}

	.panel-header p:last-child {
		margin: 0;
		max-width: 34rem;
		color: var(--text-muted);
		line-height: 1.45;
	}

	.trend-note {
		display: grid;
		grid-template-columns: repeat(3, minmax(0, 1fr));
		gap: 0.6rem;
		margin-bottom: 0.75rem;
	}

	.trend-note div,
		.summary-box {
		padding: 0.75rem;
		border-radius: 0.9rem;
		background: var(--bg-soft);
		border: 1px solid var(--border);
	}

	.trend-note span,
		.tool-actions span,
		.summary-box span {
		display: block;
		color: var(--text-muted);
		font-size: 0.8rem;
	}

	.trend-note strong,
		.tool-actions strong {
		display: block;
		margin-top: 0.2rem;
	}

	.mini-grid {
		display: grid;
		gap: 0.75rem;
	}

	.summary-box ul {
		list-style: none;
		padding: 0;
		margin: 0.7rem 0 0;
		display: grid;
		gap: 0.6rem;
	}

	.summary-box li {
		display: grid;
		gap: 0.18rem;
	}

	.summary-box strong {
		font-size: 0.95rem;
	}

	.filters .tool-grid {
		display: grid;
		grid-template-columns:
			minmax(11rem, 1fr)
			minmax(0, 1fr)
			minmax(0, 0.9fr)
			minmax(16rem, 1.6fr)
			minmax(0, 1fr)
			minmax(11rem, 1fr);
		gap: 0.75rem;
		align-items: end;
	}

	.view-mode {
		margin: 0;
		padding: 0.75rem;
		border-radius: 0.9rem;
		border: 1px solid var(--border);
		background: var(--bg-soft);
		display: grid;
		gap: 0.4rem;
	}

	.view-mode legend {
		padding: 0;
		font-size: 0.76rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--text-dim);
	}

	.view-mode label {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.view-mode input {
		width: auto;
		margin: 0;
	}

	.multi-filter {
		margin: 0;
		padding: 0.75rem;
		border-radius: 0.9rem;
		border: 1px solid var(--border);
		background: var(--bg-soft);
		display: grid;
		gap: 0.55rem;
		min-height: 100%;
	}

	.multi-filter legend {
		padding: 0;
		font-size: 0.76rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--text-dim);
	}

	.filter-head {
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 0.75rem;
	}

	.filter-reset {
		padding: 0.18rem 0.5rem;
		border-radius: 999px;
		border: 1px solid var(--border);
		background: rgba(255, 255, 255, 0.04);
		color: var(--text-muted);
		font: inherit;
		font-size: 0.72rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		cursor: pointer;
	}

	.check-grid {
		display: flex;
		flex-wrap: wrap;
		gap: 0.45rem;
	}

	.check-pill {
		display: inline-flex;
		align-items: center;
		gap: 0.45rem;
		padding: 0.42rem 0.65rem;
		border-radius: 999px;
		border: 1px solid var(--border);
		background: rgba(255, 255, 255, 0.04);
		font-size: 0.82rem;
	}

	.check-pill input {
		width: auto;
		margin: 0;
	}

	.check-pill span {
		font-size: 0.82rem;
		text-transform: none;
		letter-spacing: normal;
		color: var(--text);
	}

	.multi-filter small {
		color: var(--text-dim);
		font-size: 0.76rem;
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

	input,
	select,
	.sort-row button {
		border-radius: 0.7rem;
		border: 1px solid var(--border);
		background: rgba(255, 255, 255, 0.04);
		color: var(--text);
		padding: 0.65rem 0.75rem;
		font: inherit;
	}

	.tool-actions {
		display: grid;
		gap: 0.2rem;
		padding: 0.75rem;
		border-radius: 0.9rem;
		background: var(--bg-soft);
		border: 1px solid var(--border);
	}

	.tool-actions small {
		color: var(--text-dim);
	}

	.sort-row {
		display: flex;
		justify-content: space-between;
		gap: 0.75rem;
		align-items: center;
		margin-bottom: 0.75rem;
	}

	.sort-row div {
		display: flex;
		gap: 0.4rem;
		flex-wrap: wrap;
	}

	.sort-row button {
		cursor: pointer;
	}

	.sort-row button.active {
		border-color: var(--accent);
		background: var(--accent-soft);
	}

	.sorter {
		display: block;
		width: 100%;
		padding: 0;
		border: none;
		background: transparent;
		color: inherit;
		font: inherit;
		text-transform: inherit;
		letter-spacing: inherit;
		cursor: pointer;
	}

	.table-wrap {
		overflow-x: auto;
	}

	table {
		width: 100%;
		border-collapse: collapse;
		min-width: 62rem;
	}

	th,
	td {
		padding: 0.7rem 0.6rem;
		border-bottom: 1px solid var(--border);
		vertical-align: top;
	}

	th {
		font-size: 0.74rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--text-dim);
	}

	.text-col {
		text-align: left;
	}

	.metric-detail {
		font-size: 0.84rem;
		line-height: 1.05;
	}

	.metric-stack {
		display: inline-grid;
		justify-items: end;
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
	}

	.num-col {
		text-align: right;
		font-variant-numeric: tabular-nums;
	}

	.match-col strong,
		.match-col span {
		display: block;
	}

	.match-col span {
		font-size: 0.82rem;
		color: var(--text-muted);
		margin-top: 0.16rem;
	}

	@media (max-width: 1080px) {
		.selector-grid,
		.card-grid,
		.split-layout,
		.filters .tool-grid,
		.trend-note {
			grid-template-columns: 1fr;
		}

		.panel-header,
		.sort-row {
			flex-direction: column;
			align-items: start;
		}
	}
</style>