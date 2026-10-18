<script lang="ts">
	import type { PageData } from './$types';
	import { modelHref, type RankingRow } from '$lib';

	type RankingsViewMode = 'runs' | 'models';

	let { data }: { data: PageData } = $props();
	let search = $state('');
	let backendFilter = $state<'all' | string>('all');
	let variantFilter = $state<'all' | string>('all');
	let viewMode = $state<RankingsViewMode>('runs');
	let sortKey = $state<'rank' | 'model' | 'backend' | 'variant' | 'wer' | 'cap' | 'rts' | 'runs'>('rank');
	let sortDirection = $state<'asc' | 'desc'>('asc');
	let selectedKeys = $state<string[]>([]);

	const backendOptions = $derived(Array.from(new Set(data.rows.map((row) => row.backend))).sort());
	const variantOptions = $derived(
		Array.from(new Set(data.rows.map((row) => row.variant ?? 'default'))).sort()
	);

	const baseFilteredRows = $derived.by(() => {
		const needle = search.trim().toLowerCase();
		return data.rows.filter((row) => {
			const matchesSearch =
				needle.length === 0 ||
				row.model.toLowerCase().includes(needle) ||
				row.backend.toLowerCase().includes(needle) ||
				(row.variant ?? 'default').toLowerCase().includes(needle);
			const matchesBackend = backendFilter === 'all' || row.backend === backendFilter;
			const matchesVariant = variantFilter === 'all' || (row.variant ?? 'default') === variantFilter;
			return matchesSearch && matchesBackend && matchesVariant;
		});
	});

	const averagedRows = $derived.by(() => buildAveragedRows(baseFilteredRows, data.metric));

	const filteredRows = $derived.by(() => {
		const rows = viewMode === 'models' ? averagedRows : baseFilteredRows;
		return [...rows].sort((left, right) => compareRows(left, right, sortKey, sortDirection));
	});

	const selectedRows = $derived(
		filteredRows.filter((row) => selectedKeys.includes(row.group_key)).slice(0, 2)
	);

	function humanizeMetric(metric: string): string {
		return metric
			.split('_')
			.map((part) => part.charAt(0).toUpperCase() + part.slice(1))
			.join(' ');
	}

	function formatMetric(metric: string, value: number): string {
		if (metric === 'realtime_factor' || metric === 'runtime_seconds') {
			return value.toFixed(3);
		}

		if (metric === 'realtime_speed') {
			return value.toFixed(2);
		}

		return value.toFixed(4);
	}

	function metricDigits(metric: string): number {
		if (metric === 'realtime_speed') {
			return 2;
		}

		if (metric === 'realtime_factor' || metric === 'runtime_seconds') {
			return 3;
		}

		return 4;
	}

	function formatRealtimeSpeed(value: number | undefined): string {
		if (value == null) {
			return 'n/a';
		}

		return `${value.toFixed(2)}x`;
	}

	function stringValue(value: string | null | undefined): string {
		return (value ?? 'default').toLowerCase();
	}

	function numericValue(value: number | undefined): number {
		return value ?? Number.POSITIVE_INFINITY;
	}

	function averageValue(values: Array<number | undefined>): number | undefined {
		const numericValues = values.filter((value): value is number => value != null && !Number.isNaN(value));
		if (numericValues.length === 0) {
			return undefined;
		}

		return numericValues.reduce((total, value) => total + value, 0) / numericValues.length;
	}

	function prefersHigherMetric(metric: string): boolean {
		return metric === 'capitalization_accuracy' || metric === 'realtime_speed';
	}

	function compareMetricValues(left: number, right: number, metric: string): number {
		return prefersHigherMetric(metric) ? right - left : left - right;
	}

	function buildAveragedRows(rows: RankingRow[], metric: string): RankingRow[] {
		const grouped = new Map<string, RankingRow[]>();

		for (const row of rows) {
			const existing = grouped.get(row.model) ?? [];
			existing.push(row);
			grouped.set(row.model, existing);
		}

		const aggregated = Array.from(grouped.entries()).map(([model, groupRows]) => {
			const backends = Array.from(new Set(groupRows.map((row) => row.backend))).sort();
			const variants = Array.from(new Set(groupRows.map((row) => row.variant ?? 'default'))).sort();
			const inputs = Array.from(new Set(groupRows.flatMap((row) => row.inputs))).sort();

			return {
				group_key: `avg:${model}`,
				count: groupRows.reduce((total, row) => total + row.count, 0),
				inputs,
				backend: backends.length === 1 ? backends[0] : 'mixed',
				model,
				variant: variants.length === 1 ? variants[0] : 'mixed',
				word_error_rate: averageValue(groupRows.map((row) => row.word_error_rate)),
				char_error_rate: averageValue(groupRows.map((row) => row.char_error_rate)),
				punctuation_error_rate: averageValue(groupRows.map((row) => row.punctuation_error_rate)),
				capitalization_accuracy: averageValue(groupRows.map((row) => row.capitalization_accuracy)),
				runtime_seconds: averageValue(groupRows.map((row) => row.runtime_seconds)),
				realtime_factor: averageValue(groupRows.map((row) => row.realtime_factor)),
				realtime_speed: averageValue(groupRows.map((row) => row.realtime_speed)),
				metric,
				value: averageValue(groupRows.map((row) => row.value)) ?? Number.POSITIVE_INFINITY,
				rank: 0
			} satisfies RankingRow;
		});

		aggregated.sort((left, right) => {
			const valueOrder = compareMetricValues(left.value, right.value, metric);
			if (valueOrder !== 0) {
				return valueOrder;
			}

			return left.model.localeCompare(right.model);
		});

		return aggregated.map((row, index) => ({
			...row,
			rank: index + 1
		}));
	}

	function compareRows(
		left: RankingRow,
		right: RankingRow,
		key: 'rank' | 'model' | 'backend' | 'variant' | 'wer' | 'cap' | 'rts' | 'runs',
		direction: 'asc' | 'desc'
	): number {
		let result = 0;

		switch (key) {
			case 'model':
				result = left.model.localeCompare(right.model);
				break;
			case 'backend':
				result = left.backend.localeCompare(right.backend);
				break;
			case 'variant':
				result = stringValue(left.variant).localeCompare(stringValue(right.variant));
				break;
			case 'wer':
				result = numericValue(left.word_error_rate) - numericValue(right.word_error_rate);
				break;
			case 'cap':
				result = numericValue(right.capitalization_accuracy) - numericValue(left.capitalization_accuracy);
				break;
				case 'rts':
					result = numericValue(right.realtime_speed) - numericValue(left.realtime_speed);
				break;
			case 'runs':
				result = left.count - right.count;
				break;
			case 'rank':
			default:
				result = left.rank - right.rank;
		}

		return direction === 'asc' ? result : result * -1;
	}

	function toggleSort(key: 'rank' | 'model' | 'backend' | 'variant' | 'wer' | 'cap' | 'rts' | 'runs') {
		if (sortKey === key) {
			sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
			return;
		}

		sortKey = key;
		sortDirection = key === 'model' || key === 'backend' || key === 'variant' ? 'asc' : 'asc';
	}

	function clearLocalFilters() {
		search = '';
		backendFilter = 'all';
		variantFilter = 'all';
		viewMode = 'runs';
		selectedKeys = [];
		sortKey = 'rank';
		sortDirection = 'asc';
	}

	function setViewMode(mode: RankingsViewMode) {
		viewMode = mode;
		selectedKeys = [];
	}

	function toggleCompare(groupKey: string) {
		if (selectedKeys.includes(groupKey)) {
			selectedKeys = selectedKeys.filter((key) => key !== groupKey);
			return;
		}

		selectedKeys = [...selectedKeys.slice(-1), groupKey];
	}

	function delta(first: number | undefined, second: number | undefined, digits = 4): string {
		if (first == null || second == null) {
			return 'n/a';
		}

		const value = second - first;
		const prefix = value > 0 ? '+' : '';
		return `${prefix}${value.toFixed(digits)}`;
	}
</script>

<svelte:head>
	<title>Rankings | Whisper Bench Explorer</title>
</svelte:head>

<div class="rankings-page">
	<section class="intro panel">
		<div>
			<p class="eyebrow">Leaderboards</p>
			<h1>Compact rankings for the metrics that actually decide the winner.</h1>
		</div>
		<p>
			Switch metric and input scope. This screen reads the precomputed ranking artifact directly.
		</p>
	</section>

	<section class="filters panel">
		<form method="GET">
			<label>
				<span>Ranking metric</span>
				<select name="metric" value={data.metric}>
					{#each data.availableMetrics as metric}
						<option value={metric}>{humanizeMetric(metric)}</option>
					{/each}
				</select>
			</label>

			<label>
				<span>Input scope</span>
				<select name="input" value={data.input}>
					{#each data.availableInputs as input}
						<option value={input}>{input === 'global' ? 'Global' : input}</option>
					{/each}
				</select>
			</label>

			<button type="submit">Apply filters</button>
		</form>
	</section>

	<section class="filters panel local-tools">
		<div class="tool-grid">
			<fieldset class="view-mode">
				<legend>Mode</legend>
				<label>
					<input
						type="radio"
						name="view-mode"
						checked={viewMode === 'runs'}
						onchange={() => setViewMode('runs')}
					/>
					<span>Runs</span>
				</label>
				<label>
					<input
						type="radio"
						name="view-mode"
						checked={viewMode === 'models'}
						onchange={() => setViewMode('models')}
					/>
					<span>Average by model</span>
				</label>
			</fieldset>

			<label>
				<span>Search</span>
				<input bind:value={search} placeholder="model, backend, variant" />
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

			<label>
				<span>Variant</span>
				<select bind:value={variantFilter}>
					<option value="all">All variants</option>
					{#each variantOptions as variant}
						<option value={variant}>{variant}</option>
					{/each}
				</select>
			</label>

			<div class="tool-actions">
				<span>{viewMode === 'models' ? 'Visible models' : 'Visible rows'}</span>
				<strong>{filteredRows.length}</strong>
				<button type="button" class="secondary" onclick={clearLocalFilters}>Reset local tools</button>
			</div>
		</div>
	</section>

	{#if selectedRows.length > 0}
		<section class="panel compare-panel">
			<div class="panel-header">
				<div>
					<p class="eyebrow">Comparison</p>
					<h2>{selectedRows.length === 2 ? 'Two-row delta view' : 'Select one more row to compare'}</h2>
				</div>
				<p>Pick up to two rows from the table. The panel compares the currently visible selection.</p>
			</div>

			<div class="compare-grid">
				{#each selectedRows as row}
					<article class="compare-card">
						<strong><a href={modelHref(row.model)}>{row.model}</a></strong>
						<span>{row.backend} / {row.variant ?? 'default'}</span>
						<dl>
							<div><dt>Rank</dt><dd>{row.rank}</dd></div>
							<div><dt>{humanizeMetric(data.metric)}</dt><dd>{formatMetric(data.metric, row.value)}</dd></div>
							<div><dt>WER</dt><dd>{row.word_error_rate?.toFixed(4) ?? 'n/a'}</dd></div>
							<div><dt>CAP</dt><dd>{row.capitalization_accuracy?.toFixed(4) ?? 'n/a'}</dd></div>
							<div><dt>RTS</dt><dd>{formatRealtimeSpeed(row.realtime_speed)}</dd></div>
						</dl>
					</article>
				{/each}

				{#if selectedRows.length === 2}
					<article class="compare-card delta-card">
						<strong>Delta</strong>
						<span><a href={modelHref(selectedRows[0].model)}>{selectedRows[0].model}</a> -> <a href={modelHref(selectedRows[1].model)}>{selectedRows[1].model}</a></span>
						<dl>
							<div><dt>{humanizeMetric(data.metric)}</dt><dd>{delta(selectedRows[0].value, selectedRows[1].value, metricDigits(data.metric))}</dd></div>
							<div><dt>WER</dt><dd>{delta(selectedRows[0].word_error_rate, selectedRows[1].word_error_rate)}</dd></div>
							<div><dt>CAP</dt><dd>{delta(selectedRows[0].capitalization_accuracy, selectedRows[1].capitalization_accuracy)}</dd></div>
							<div><dt>RTS</dt><dd>{delta(selectedRows[0].realtime_speed, selectedRows[1].realtime_speed, 2)}x</dd></div>
							<div><dt>Runs</dt><dd>{selectedRows[1].count - selectedRows[0].count}</dd></div>
						</dl>
					</article>
				{/if}
			</div>
		</section>
	{/if}

	<section class="panel">
		<div class="panel-header">
			<div>
				<p class="eyebrow">{data.input === 'global' ? 'Global view' : data.input}</p>
				<h2>{humanizeMetric(data.metric)}</h2>
			</div>
			<p>{filteredRows.length} {viewMode === 'models' ? 'model averages' : 'ranked entries'} after local filtering and sorting.</p>
		</div>

		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th class="compare-col">Compare</th>
						<th class="num-col"><button type="button" class="sorter num-col" onclick={() => toggleSort('rank')}>Rank</button></th>
						<th class="text-col"><button type="button" class="sorter text-col" onclick={() => toggleSort('model')}>Model</button></th>
						<th class="text-col"><button type="button" class="sorter text-col" onclick={() => toggleSort('backend')}>Backend</button></th>
						<th class="text-col"><button type="button" class="sorter text-col" onclick={() => toggleSort('variant')}>Variant</button></th>
						<th class="num-col"><button type="button" class="sorter num-col" onclick={() => toggleSort('wer')}>WER</button></th>
						<th class="num-col"><button type="button" class="sorter num-col" onclick={() => toggleSort('cap')}>CAP</button></th>
						<th class="num-col"><button type="button" class="sorter num-col" onclick={() => toggleSort('rts')}>RTS</button></th>
						<th class="num-col"><button type="button" class="sorter num-col" onclick={() => toggleSort('runs')}>Runs</button></th>
					</tr>
					</thead>
					<tbody>
						{#each filteredRows as row}
							<tr class:selected={selectedKeys.includes(row.group_key)}>
								<td class="compare-col">
									<input
										type="checkbox"
										checked={selectedKeys.includes(row.group_key)}
										onchange={() => toggleCompare(row.group_key)}
									/>
								</td>
								<td class="num-col">{row.rank}</td>
								<td class="text-col">
									<strong><a href={modelHref(row.model)}>{row.model}</a></strong>
								</td>
								<td class="text-col">{row.backend}</td>
								<td class="text-col">{row.variant ?? 'default'}</td>
								<td class="num-col">{row.word_error_rate?.toFixed(4) ?? 'n/a'}</td>
								<td class="num-col">{row.capitalization_accuracy?.toFixed(4) ?? 'n/a'}</td>
								<td class="num-col">{formatRealtimeSpeed(row.realtime_speed)}</td>
								<td class="num-col">{row.count}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
	</section>
</div>

<style>
	.rankings-page {
		padding: 1rem;
		display: grid;
		gap: 0.75rem;
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
		grid-template-columns: minmax(0, 1.5fr) minmax(15rem, 1fr);
		gap: 0.9rem;
		align-items: end;
	}

	.intro h1,
	h2 {
		font-family: 'Fraunces', Georgia, serif;
		margin: 0;
		line-height: 1;
	}

	.intro h1 {
		font-size: clamp(1.6rem, 3vw, 2.6rem);
	}

	.eyebrow {
		text-transform: uppercase;
		letter-spacing: 0.12em;
		font-size: 0.66rem;
		font-weight: 700;
		margin: 0 0 0.35rem;
		color: var(--accent);
	}

	.filters form {
		display: flex;
		gap: 0.75rem;
		flex-wrap: wrap;
		align-items: end;
	}

	label {
		display: grid;
		gap: 0.35rem;
		min-width: 12rem;
	}

	label span,
	.panel-header p:last-child {
		color: var(--text-muted);
		font-size: 0.84rem;
	}

	select,
	button {
		font: inherit;
		padding: 0.55rem 0.75rem;
		border-radius: 0.7rem;
		border: 1px solid var(--border);
		background: var(--bg-panel-strong);
		color: var(--text);
	}

	button {
		background: linear-gradient(135deg, #143547, #0f2734);
		color: var(--text);
		font-weight: 700;
		cursor: pointer;
	}

	.panel-header {
		display: flex;
		justify-content: space-between;
		gap: 0.75rem;
		align-items: end;
		margin-bottom: 0.75rem;
	}

	.table-wrap {
		overflow-x: auto;
	}

	.tool-grid {
		display: grid;
		grid-template-columns: minmax(11rem, 1fr) 1.3fr repeat(2, minmax(10rem, 1fr)) minmax(10rem, 0.9fr);
		gap: 0.75rem;
		align-items: end;
	}

	.view-mode {
		margin: 0;
		padding: 0.55rem 0.75rem;
		border-radius: 0.7rem;
		border: 1px solid var(--border);
		background: var(--bg-panel-strong);
		display: grid;
		gap: 0.4rem;
		min-width: 11rem;
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
		min-width: 0;
	}

	.view-mode input {
		width: auto;
		margin: 0;
	}

	.tool-actions {
		display: grid;
		gap: 0.28rem;
	}

	.tool-actions strong {
		font-size: 1rem;
	}

	input,
	select,
	button {
		width: 100%;
	}

	input {
		font: inherit;
		padding: 0.55rem 0.75rem;
		border-radius: 0.7rem;
		border: 1px solid var(--border);
		background: var(--bg-panel-strong);
		color: var(--text);
	}

	button.secondary {
		margin-top: 0.25rem;
		background: transparent;
		border-color: var(--border);
		color: var(--text-muted);
	}

	.compare-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
		gap: 0.75rem;
	}

	.compare-card {
		padding: 0.8rem;
		border: 1px solid rgba(143, 175, 206, 0.12);
		background: rgba(255, 255, 255, 0.03);
		border-radius: 0.9rem;
	}

	.compare-card span {
		display: block;
		margin-top: 0.18rem;
		color: var(--text-muted);
		font-size: 0.82rem;
	}

	.compare-card strong a,
	td strong a {
		text-decoration: none;
	}

	.compare-card strong a:hover,
	.compare-card span a:hover,
	td strong a:hover {
		color: var(--accent);
	}

	.compare-card dl {
		margin: 0.75rem 0 0;
		display: grid;
		gap: 0.45rem;
	}

	.compare-card dl div {
		display: flex;
		justify-content: space-between;
		gap: 0.75rem;
		font-size: 0.86rem;
	}

	.compare-card dt {
		color: var(--text-dim);
	}

	.compare-card dd {
		margin: 0;
		font-weight: 700;
	}

	.delta-card {
		border-color: rgba(94, 195, 255, 0.22);
		background: rgba(94, 195, 255, 0.08);
	}

	table {
		width: 100%;
		border-collapse: collapse;
	}

	th,
	td {
		padding: 0.5rem 0.45rem;
		text-align: left;
		border-bottom: 1px solid rgba(143, 175, 206, 0.08);
		font-size: 0.86rem;
	}

	th {
		font-size: 0.66rem;
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

	.compare-col {
		text-align: center;
		width: 4.25rem;
	}

	td strong {
		font-size: 0.9rem;
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

	tbody tr.selected {
		background: rgba(94, 195, 255, 0.08);
	}

	@media (max-width: 820px) {
		.intro,
		.panel-header {
			grid-template-columns: 1fr;
			flex-direction: column;
			align-items: start;
		}

		.rankings-page {
			padding: 1rem;
		}

		.tool-grid {
			grid-template-columns: 1fr;
		}
	}
</style>