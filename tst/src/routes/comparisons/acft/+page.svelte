<script lang="ts">
	import type { PageData } from './$types';
	import type { AcftComparisonPairRow } from '$lib';

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

	const comparisonType = 'acft_with_ctx_vs_standard' as const;
	let search = $state('');
	let selectedBackends = $state<string[]>([]);
	let selectedInputs = $state<string[]>([]);
	let modelKeyFilter = $state<'all' | string>('all');
	let viewMode = $state<ComparisonViewMode>('models');
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
	const summary = $derived(data.summary.comparison_summaries[comparisonType]);
	const backendOptions = $derived(Array.from(new Set(rows.map((row) => row.backend))).sort());
	const inputOptions = $derived(Array.from(new Set(rows.map((row) => row.input_file))).sort());
	const modelKeyOptions = $derived(Array.from(new Set(rows.map((row) => row.model_key))).sort());
	const activeBackendFilters = $derived.by(() => {
		const validSelections = selectedBackends.filter((backend) => backendOptions.includes(backend));
		return validSelections.length > 0 ? validSelections : backendOptions;
	});
	const activeInputFilters = $derived.by(() => {
		const validSelections = selectedInputs.filter((input) => inputOptions.includes(input));
		return validSelections.length > 0 ? validSelections : inputOptions;
	});

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
			const matchesBackend =
				activeBackendFilters.length === 0 || activeBackendFilters.includes(row.backend);
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

	function averagePrimaryMetricValue(row: ComparisonDisplayRow, metrics: PrimarySortMetric[]): number {
		const values = metrics.map((metric) => metricValue(row, metric));
		return values.reduce((total, value) => total + value, 0) / values.length;
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

	function toggleBackendSelection(backend: string) {
		const currentSelections = activeBackendFilters;

		if (currentSelections.includes(backend)) {
			if (currentSelections.length === 1) {
				return;
			}

			const nextSelections = currentSelections.filter((value) => value !== backend);
			selectedBackends = nextSelections.length === backendOptions.length ? [] : nextSelections;
			return;
		}

		const nextSelections = [...currentSelections, backend].sort(
			(left, right) => backendOptions.indexOf(left) - backendOptions.indexOf(right)
		);
		selectedBackends = nextSelections.length === backendOptions.length ? [] : nextSelections;
	}

	function selectAllBackends() {
		selectedBackends = [];
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

		const formatValue = (value: number) =>
			percent ? `${(value * 100).toFixed(digits)}%` : value.toFixed(digits);
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

	function togglePrimarySortMetric(metric: PrimarySortMetric) {
		if (primarySortMetrics.includes(metric)) {
			primarySortMetrics = primarySortMetrics.filter((value) => value !== metric);
			return;
		}

		primarySortMetrics = primarySortOptions
			.map((option) => option.key)
			.filter((key) => key === metric || primarySortMetrics.includes(key));
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

	function comparisonLabel(row: ComparisonDisplayRow): string {
		return viewMode === 'models'
			? row.model_key
			: `${row.model_key} | ${row.backends.join(', ')} | ${row.inputs.join(', ')}`;
	}
</script>

<svelte:head>
	<title>ACFT vs Standard | Whisper Bench Explorer</title>
	<meta
		name="description"
		content="Focused ACFT with-context versus standard comparisons with filtering, multi-input selection, and model-level sorting."
	/>
</svelte:head>

<div class="acft-page">
	<section class="intro panel">
		<div>
			<p class="eyebrow">Focused comparison</p>
			<h1>Inspect ACFT with context against matching standard models without the extra comparison noise.</h1>
		</div>
		<div class="intro-copy">
			<p>
				This page is fixed to {selectedOption.label}. It only compares ACFT with audio context against
				matching standard non-ACFT models, while keeping the comparisons page filtering, multi-input selection,
				mode switching, and sorting controls.
			</p>
			<div class="bucket-note">
				<div>
					<span>Compared rows</span>
					<strong>{summary.count}</strong>
				</div>
				<div>
					<span>Scope</span>
					<strong>ACFT with ctx vs standard</strong>
				</div>
			</div>
		</div>
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

			<fieldset class="multi-filter">
				<div class="filter-head">
					<legend>Backends</legend>
					<button type="button" class="filter-reset" onclick={selectAllBackends}>All</button>
				</div>
				<div class="check-grid">
					{#each backendOptions as backend}
						<label class="check-pill">
							<input
								type="checkbox"
								checked={activeBackendFilters.includes(backend)}
								onchange={() => toggleBackendSelection(backend)}
							/>
							<span>{backend}</span>
						</label>
					{/each}
				</div>
				<small>
					{#if activeBackendFilters.length === backendOptions.length}
						All backends included
					{:else}
						{activeBackendFilters.length} of {backendOptions.length} backends included
					{/if}
				</small>
			</fieldset>

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

			<div class="tool-summary">
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
				<h2>{viewMode === 'models' ? 'Averaged ACFT vs standard slices' : 'Matched ACFT vs standard runs'}</h2>
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
							<button type="button" class="sorter num-col" onclick={() => toggleSort('runtime')}>Runtime</button>
						</th>
						<th class="num-col">
							<button type="button" class="sorter num-col" onclick={() => toggleSort('relative')}>Relative runtime</button>
						</th>
						<th class="num-col">
							<button type="button" class="sorter num-col" onclick={() => toggleSort('wer')}>WER</button>
						</th>
						<th class="num-col">
							<button type="button" class="sorter num-col" onclick={() => toggleSort('char')}>CER</button>
						</th>
						<th class="num-col">
							<button type="button" class="sorter num-col" onclick={() => toggleSort('punctuation')}>Punct</button>
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
	.acft-page {
		padding: 1rem;
		display: grid;
		gap: 0.8rem;
	}

	.panel {
		border: 1px solid var(--border);
		background: var(--bg-panel);
		backdrop-filter: blur(10px);
		box-shadow: 0 14px 36px rgba(0, 0, 0, 0.24);
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

	.bucket-note {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 0.6rem;
	}

	.bucket-note div,
	.tool-summary,
	.view-mode,
	.multi-filter {
		padding: 0.75rem;
		border-radius: 0.9rem;
		background: var(--bg-soft);
		border: 1px solid var(--border);
	}

	.bucket-note span,
	.tool-summary span,
	.match-col span,
	.multi-filter small {
		display: block;
		color: var(--text-muted);
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
		display: grid;
		gap: 0.4rem;
	}

	.view-mode legend,
	.multi-filter legend,
	label span {
		padding: 0;
		font-size: 0.76rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--text-dim);
	}

	.view-mode label,
	label {
		display: grid;
		gap: 0.35rem;
	}

	.view-mode label {
		display: flex;
		align-items: center;
		gap: 0.5rem;
	}

	.view-mode input,
	.check-pill input {
		width: auto;
		margin: 0;
	}

	input,
	select,
	.sort-row button,
	.filter-reset {
		border-radius: 0.7rem;
		border: 1px solid var(--border);
		background: rgba(255, 255, 255, 0.04);
		color: var(--text);
		padding: 0.65rem 0.75rem;
		font: inherit;
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

	.check-pill span {
		font-size: 0.82rem;
		text-transform: none;
		letter-spacing: normal;
		color: var(--text);
	}

	.tool-summary strong {
		display: block;
		margin-top: 0.18rem;
	}

	.tool-summary small {
		color: var(--text-dim);
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
		max-width: 34rem;
		color: var(--text-muted);
		line-height: 1.45;
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

	.num-col {
		text-align: right;
		font-variant-numeric: tabular-nums;
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

	.match-col strong,
	.match-col span {
		display: block;
	}

	.match-col span {
		font-size: 0.82rem;
		margin-top: 0.16rem;
	}

	@media (max-width: 1080px) {
		.intro,
		.bucket-note,
		.filters .tool-grid {
			grid-template-columns: 1fr;
		}

		.panel-header,
		.sort-row {
			flex-direction: column;
			align-items: start;
		}
	}
</style>