<script lang="ts">
	import favicon from '$lib/assets/favicon.svg';

	let { children } = $props();

	const navigation = [
		{ href: '/', label: 'Overview' },
		{ href: '/rankings', label: 'Rankings' },
		{ href: '/comparisons', label: 'ACFT Comparisons' },
		{ href: '/comparisons/acft', label: 'ACFT vs Standard' },
		{ href: '/comparisons/insights', label: 'ACFT Insights' }
	];
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
</svelte:head>

<div class="app-shell">
	<header class="topbar">
		<a class="brand" href="/">
			<span>WB</span>
			<div>
				<strong>Whisper Bench</strong>
				<small>Derived benchmark explorer</small>
			</div>
		</a>

		<nav>
			{#each navigation as item}
				<a href={item.href}>{item.label}</a>
			{/each}
		</nav>
	</header>

{@render children()}
</div>

<style>
	:global(:root) {
		color-scheme: dark;
		--bg: #091017;
		--bg-elevated: #0f1822;
		--bg-panel: rgba(17, 25, 36, 0.9);
		--bg-panel-strong: #111b26;
		--bg-soft: rgba(126, 158, 191, 0.08);
		--border: rgba(143, 175, 206, 0.16);
		--border-strong: rgba(143, 175, 206, 0.28);
		--text: #edf4ff;
		--text-muted: #8ea0b5;
		--text-dim: #688097;
		--accent: #5ec3ff;
		--accent-soft: rgba(94, 195, 255, 0.14);
		--success: #68f0b6;
		--warning: #ffd66e;
		--danger: #ff8f7a;
	}

	:global(html) {
		background: var(--bg);
	}

	:global(body) {
		margin: 0;
		font-family: 'Space Grotesk', 'IBM Plex Sans', system-ui, sans-serif;
		background:
			radial-gradient(circle at top left, rgba(94, 195, 255, 0.12), transparent 26%),
			radial-gradient(circle at top right, rgba(104, 240, 182, 0.08), transparent 22%),
			linear-gradient(180deg, #081019 0%, #0a121c 48%, #081019 100%);
		color: var(--text);
	}

	:global(*) {
		box-sizing: border-box;
	}

	:global(a) {
		color: inherit;
	}

	:global(code) {
		font-family: 'IBM Plex Mono', 'SFMono-Regular', monospace;
		font-size: 0.9em;
		padding: 0.1rem 0.35rem;
		border-radius: 0.4rem;
		background: rgba(255, 255, 255, 0.06);
	}

	:global(select),
	:global(option),
	:global(optgroup) {
		color-scheme: dark;
		background: var(--bg-panel-strong);
		color: var(--text);
	}

	.app-shell {
		min-height: 100vh;
	}

	.topbar {
		position: sticky;
		top: 0;
		z-index: 10;
		display: flex;
		justify-content: space-between;
		align-items: center;
		gap: 0.9rem;
		padding: 0.65rem 0.9rem;
		background: rgba(8, 15, 23, 0.84);
		backdrop-filter: blur(14px);
		border-bottom: 1px solid var(--border);
	}

	.brand {
		display: inline-flex;
		align-items: center;
		gap: 0.65rem;
		color: var(--text);
		text-decoration: none;
	}

	.brand span {
		display: grid;
		place-items: center;
		width: 2rem;
		height: 2rem;
		border-radius: 0.7rem;
		background: linear-gradient(135deg, #19384a, #102a38);
		border: 1px solid var(--border-strong);
		color: #f7fbff;
		font-weight: 800;
	}

	.brand strong,
	.brand small {
		display: block;
	}

	.brand strong {
		font-size: 0.95rem;
		line-height: 1.1;
	}

	.brand small {
		color: var(--text-dim);
		font-size: 0.76rem;
	}

	nav {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
	}

	nav a {
		padding: 0.4rem 0.75rem;
		border-radius: 999px;
		text-decoration: none;
		color: var(--text-muted);
		background: var(--bg-soft);
		border: 1px solid transparent;
		font-weight: 600;
		font-size: 0.86rem;
	}

	nav a:hover {
		background: var(--accent-soft);
		border-color: var(--border);
		color: var(--text);
	}

	@media (max-width: 720px) {
		.topbar {
			flex-direction: column;
			align-items: start;
		}
	}
</style>
