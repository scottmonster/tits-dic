<script lang="ts">
	import { modelHref } from '$lib';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	function percent(value: number | null | undefined, digits = 1): string {
		if (value == null || Number.isNaN(value)) {
			return 'n/a';
		}

		return `${(value * 100).toFixed(digits)}%`;
	}

	function number(value: number | null | undefined, digits = 2): string {
		if (value == null || Number.isNaN(value)) {
			return 'n/a';
		}

		return value.toFixed(digits);
	}

	function rankingDigits(metric: string): number {
		if (metric === 'realtime_speed') {
			return 2;
		}

		if (metric === 'realtime_factor' || metric === 'runtime_seconds') {
			return 3;
		}

		return 4;
	}
</script>

<svelte:head>
	<title>Whisper Bench Explorer</title>
	<meta
		name="description"
		content="Interactive overview of benchmarked Whisper model accuracy, speed, and comparison data."
	/>
</svelte:head>

<div class="page-shell">
	<section class="hero">
		<div class="hero-copy">
			<p class="eyebrow">Whisper Benchmark Explorer</p>
			<h1>Fast answers on model accuracy and speed.</h1>
			<p class="lede">
				The UI reads the derived benchmark outputs directly. Focus on backend deltas,
				leaderboards, and the balance between accuracy and runtime.
			</p>
			<div class="hero-actions">
				<a href="/rankings?metric=word_error_rate&input=global">Open accuracy rankings</a>
				<a href="/rankings?metric=balance_score_accuracy_heavy&input=global">Open balance rankings</a>
				<a href="/comparisons">Open ACFT comparisons</a>
			</div>
		</div>
		<div class="hero-panel">
			<div>
				<span class="panel-label">Current dataset</span>
				<strong>{data.manifest.row_count} scored runs</strong>
			</div>
			<div>
				<span class="panel-label">Backends</span>
				<strong>{data.manifest.available_backends.join(', ')}</strong>
			</div>
			<div>
				<span class="panel-label">Inputs</span>
				<strong>{data.manifest.available_inputs.join(', ')}</strong>
			</div>
		</div>
	</section>

	<section class="card-grid">
		{#each data.overviewCards as card}
			<article class="metric-card">
				<p>{card.label}</p>
				<h2>{card.value}</h2>
				<span>{card.help}</span>
			</article>
		{/each}
	</section>

	<section class="split-layout">
		<article class="panel panel-table">
			<div class="panel-header">
				<div>
					<p class="eyebrow">Backend profile</p>
					<h2>Backend summary</h2>
				</div>
				<p>Mean scores across all runs, sorted by lower mean WER.</p>
			</div>

			<div class="table-wrap">
				<table>
					<thead>
						<tr>
							<th class="text-col">Backend</th>
							<th class="num-col">Runs</th>
							<th class="num-col">Mean WER</th>
							<th class="num-col">Mean CER</th>
							<th class="num-col">Mean CAP</th>
							<th class="num-col">Mean RTF</th>
							<th class="num-col">Mean runtime</th>
						</tr>
					</thead>
					<tbody>
						{#each data.backendRows as row}
							<tr>
								<td class="text-col">{row.backend}</td>
								<td class="num-col">{row.runs}</td>
								<td class="num-col">{percent(row.meanWer)}</td>
								<td class="num-col">{percent(row.meanCer)}</td>
								<td class="num-col">{percent(row.meanCapitalizationAccuracy)}</td>
								<td class="num-col">{number(row.meanRealtimeFactor, 3)}</td>
								<td class="num-col">{number(row.meanRuntime, 2)} s</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		</article>

		<article class="panel panel-highlights">
			<div class="panel-header">
				<div>
					<p class="eyebrow">Shortlist</p>
					<h2>Global leaders</h2>
				</div>
				<p>The current best configurations from the precomputed ranking views.</p>
			</div>

			<div class="highlight-stack">
				{#if data.globalMostAccurate}
					<div class="highlight-card accent-gold">
						<span>Most accurate</span>
						<strong><a href={modelHref(data.globalMostAccurate.model)}>{data.globalMostAccurate.model}</a></strong>
						<small>{data.globalMostAccurate.backend} / {data.globalMostAccurate.variant ?? 'default'}</small>
						<p>{percent(data.globalMostAccurate.word_error_rate)} mean WER</p>
					</div>
				{/if}

				{#if data.globalFastest}
					<div class="highlight-card accent-cyan">
						<span>Fastest</span>
						<strong><a href={modelHref(data.globalFastest.model)}>{data.globalFastest.model}</a></strong>
						<small>{data.globalFastest.backend} / {data.globalFastest.variant ?? 'default'}</small>
						<p>{number(data.globalFastest.realtime_factor, 3)} realtime factor</p>
					</div>
				{/if}

				{#if data.globalBestBalance}
					<div class="highlight-card accent-rose">
						<span>Best balance</span>
						<strong><a href={modelHref(data.globalBestBalance.model)}>{data.globalBestBalance.model}</a></strong>
						<small>{data.globalBestBalance.backend} / {data.globalBestBalance.variant ?? 'default'}</small>
						<p>Accuracy-heavy profile rank #{data.globalBestBalance.rank}</p>
					</div>
				{/if}
			</div>
		</article>
	</section>

	<section class="signal-grid">
		<article class="panel dense-panel">
			<div class="panel-header compact">
				<div>
					<p class="eyebrow">Filter surface</p>
					<h2>Data dimensions</h2>
				</div>
			</div>

			<div class="token-grid">
				<div>
					<span>Backends</span>
					<strong>{data.manifest.available_backends.length}</strong>
				</div>
				<div>
					<span>Models</span>
					<strong>{data.manifest.available_models.length}</strong>
				</div>
				<div>
					<span>Quantizations</span>
					<strong>{data.manifest.available_quantizations.join(', ')}</strong>
				</div>
				<div>
					<span>Variants</span>
					<strong>{data.manifest.available_variants.join(', ')}</strong>
				</div>
			</div>
		</article>

		<article class="panel dense-panel">
			<div class="panel-header compact">
				<div>
					<p class="eyebrow">Why these views</p>
					<h2>What matters</h2>
				</div>
			</div>
			<ul class="compact-list">
				<li>Overview for backend-level signal</li>
				<li>Rankings for best and worst configs</li>
				<li>ACFT comparisons for pairwise delta analysis</li>
			</ul>
		</article>
	</section>

	<section class="leaderboard-grid">
		{#each data.topRankings as ranking}
			<article class="panel">
				<div class="panel-header compact">
					<div>
						<p class="eyebrow">{ranking.metric}</p>
						<h2>
							<a href={`/rankings?metric=${ranking.metric}&input=global`}>{ranking.title}</a>
						</h2>
					</div>
					<p>Top five global entries. Click through for filtering, sorting, and comparison.</p>
				</div>

				<ol class="ranking-list">
					{#each ranking.rows as row}
						<li>
							<div>
								<strong>
									<a href={modelHref(row.model)}>{row.model}</a>
								</strong>
								<span>{row.backend} / {row.variant ?? 'default'}</span>
							</div>
							<mark>{number(row.value, rankingDigits(ranking.metric))}</mark>
						</li>
					{/each}
				</ol>
			</article>
		{/each}
	</section>
</div>

<style>
	.page-shell {
		padding: 1rem;
		display: grid;
		gap: 0.9rem;
	}

	.hero,
	.panel,
	.metric-card {
		border: 1px solid var(--border);
		background: var(--bg-panel);
		backdrop-filter: blur(10px);
		box-shadow: 0 14px 36px rgba(0, 0, 0, 0.24);
	}

	.hero {
		display: grid;
		grid-template-columns: minmax(0, 1.7fr) minmax(15rem, 0.9fr);
		gap: 0.9rem;
		padding: 1rem;
		border-radius: 1.1rem;
	}

	.hero h1,
	.panel h2,
	.metric-card h2 {
		font-family: 'Fraunces', Georgia, serif;
		line-height: 0.98;
		margin: 0;
	}

	.hero h1 {
		font-size: clamp(1.9rem, 3.5vw, 3.6rem);
		max-width: 10ch;
	}

	.lede {
		max-width: 58ch;
		font-size: 0.92rem;
		line-height: 1.45;
		margin: 0.55rem 0 0;
		color: var(--text-muted);
	}

	.hero-actions {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
		margin-top: 0.8rem;
	}

	.hero-actions a,
	.highlight-card strong a,
	.panel h2 a,
	.ranking-list strong a {
		text-decoration: none;
	}

	.hero-actions a {
		padding: 0.45rem 0.7rem;
		border-radius: 999px;
		background: var(--accent-soft);
		border: 1px solid var(--border);
		font-size: 0.84rem;
		font-weight: 700;
	}

	.hero-actions a:hover,
	.highlight-card strong a:hover,
	.panel h2 a:hover,
	.ranking-list strong a:hover {
		color: var(--accent);
	}

	.hero-panel {
		display: grid;
		gap: 0.8rem;
		padding: 0.95rem;
		border-radius: 0.95rem;
		background: linear-gradient(180deg, rgba(15, 31, 43, 0.98), rgba(10, 20, 30, 0.98));
		border: 1px solid var(--border-strong);
		color: var(--text);
	}

	.hero-panel strong {
		display: block;
		margin-top: 0.12rem;
		font-size: 0.98rem;
	}

	.panel-label,
	.eyebrow,
	.metric-card p,
	.panel-header p:first-child {
		text-transform: uppercase;
		letter-spacing: 0.12em;
		font-size: 0.66rem;
		font-weight: 700;
		margin: 0;
		color: var(--text-dim);
	}

	.hero .eyebrow {
		color: var(--accent);
	}

	.card-grid,
	.leaderboard-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
		gap: 0.75rem;
	}

	.metric-card {
		padding: 0.85rem;
		border-radius: 0.95rem;
	}

	.metric-card h2 {
		font-size: 1.6rem;
		margin: 0.3rem 0;
	}

	.metric-card span {
		display: block;
		line-height: 1.35;
		color: var(--text-muted);
		font-size: 0.82rem;
	}

	.split-layout {
		display: grid;
		grid-template-columns: minmax(0, 1.7fr) minmax(15rem, 0.9fr);
		gap: 0.75rem;
	}

	.signal-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 0.75rem;
	}

	.panel {
		padding: 0.9rem;
		border-radius: 1rem;
	}

	.dense-panel {
		padding: 0.8rem;
	}

	.panel-header {
		display: flex;
		justify-content: space-between;
		gap: 0.75rem;
		align-items: end;
		margin-bottom: 0.7rem;
	}

	.panel-header.compact {
		align-items: start;
	}

	.panel-header p:last-child {
		margin: 0;
		max-width: 28rem;
		line-height: 1.35;
		color: var(--text-muted);
		font-size: 0.85rem;
	}

	.table-wrap {
		overflow-x: auto;
	}

	table {
		width: 100%;
		border-collapse: collapse;
	}

	th,
	td {
		padding: 0.55rem 0.5rem;
		text-align: left;
		border-bottom: 1px solid rgba(143, 175, 206, 0.08);
		font-size: 0.88rem;
	}

	th {
		font-size: 0.68rem;
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

	.highlight-stack {
		display: grid;
		gap: 0.6rem;
	}

	.highlight-card {
		padding: 0.75rem;
		border-radius: 0.85rem;
		color: #081019;
	}

	.highlight-card span,
	.highlight-card small {
		display: block;
	}

	.highlight-card strong {
		display: block;
		font-size: 1rem;
		margin: 0.2rem 0;
	}

	.accent-gold {
		background: linear-gradient(135deg, #f7d26b, #f0b15b);
	}

	.accent-cyan {
		background: linear-gradient(135deg, #8de0ec, #4ea7d8);
	}

	.accent-rose {
		background: linear-gradient(135deg, #f3b6a6, #ef8a6d);
	}

	.ranking-list {
		list-style: none;
		padding: 0;
		margin: 0;
		display: grid;
		gap: 0.55rem;
	}

	.ranking-list li {
		display: flex;
		justify-content: space-between;
		gap: 0.7rem;
		align-items: center;
		padding-bottom: 0.55rem;
		border-bottom: 1px solid rgba(143, 175, 206, 0.08);
	}

	.ranking-list li:last-child {
		border-bottom: none;
		padding-bottom: 0;
	}

	.ranking-list span {
		display: block;
		margin-top: 0.15rem;
		color: var(--text-muted);
		font-size: 0.8rem;
	}

	mark {
		background: rgba(255, 255, 255, 0.06);
		color: var(--text);
		padding: 0.25rem 0.5rem;
		border-radius: 999px;
		font-weight: 700;
	}

	.token-grid {
		display: grid;
		grid-template-columns: repeat(2, minmax(0, 1fr));
		gap: 0.5rem;
	}

	.token-grid div {
		padding: 0.65rem;
		border-radius: 0.8rem;
		background: var(--bg-soft);
		border: 1px solid rgba(143, 175, 206, 0.08);
	}

	.token-grid span {
		display: block;
		font-size: 0.7rem;
		text-transform: uppercase;
		letter-spacing: 0.08em;
		color: var(--text-dim);
	}

	.token-grid strong {
		display: block;
		margin-top: 0.2rem;
		font-size: 0.92rem;
	}

	.compact-list {
		margin: 0;
		padding-left: 1rem;
		display: grid;
		gap: 0.45rem;
		color: var(--text-muted);
		font-size: 0.86rem;
	}

	@media (max-width: 900px) {
		.page-shell {
			padding: 1rem;
		}

		.hero,
		.split-layout,
		.signal-grid {
			grid-template-columns: 1fr;
		}

		.hero h1 {
			max-width: none;
		}

		.panel-header {
			flex-direction: column;
			align-items: start;
		}

		.token-grid {
			grid-template-columns: 1fr;
		}
	}
</style>
