<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import {
		api,
		type DebugContextPayload
	} from '$lib/api';

	type Tab = 'latest' | 'api' | 'cc';
	let tab = $state<Tab>('latest');

	let ctx = $state<DebugContextPayload | null>(null);
	let loading = $state(false);
	let err = $state<string | null>(null);
	let lastRefreshed = $state<string>('—');
	let expanded = $state<Record<string, boolean>>({});
	let pollTimer: ReturnType<typeof setInterval> | null = null;

	const labelColors: Record<string, string> = {
		constitution: 'var(--color-mauve)',
		self: 'var(--color-pink)',
		context: 'var(--color-peach)',
		'recall+carryover': 'var(--color-yellow)',
		'system-extra': 'var(--color-overlay1)',
		user: 'var(--color-blue)',
		assistant: 'var(--color-green)',
		system: 'var(--color-overlay1)'
	};

	function colorFor(label: string): string {
		return labelColors[label] ?? 'var(--color-subtext0)';
	}

	function fmtTime(t?: number | string): string {
		if (t === undefined || t === null || t === '') return '—';
		const n = typeof t === 'number' ? t : Number(t);
		if (!Number.isFinite(n)) return String(t);
		const d = new Date(n * 1000);
		return d.toLocaleString();
	}

	function fmtCost(c?: number): string {
		if (c === undefined || c === null) return '—';
		return `$${c.toFixed(4)}`;
	}

	function fmtNum(n?: number): string {
		if (n === undefined || n === null) return '—';
		return n.toLocaleString();
	}

	async function refreshAll() {
		loading = true;
		err = null;
		try {
			ctx = await api.debugContext(tab).catch((e) => ({ error: String(e) }) as DebugContextPayload);
			lastRefreshed = new Date().toLocaleTimeString();
		} catch (e) {
			err = String(e);
		} finally {
			loading = false;
		}
	}

	onMount(() => {
		refreshAll();
		pollTimer = setInterval(refreshAll, 3000);
	});

	$effect(() => {
		tab;
		refreshAll();
	});

	onDestroy(() => {
		if (pollTimer) clearInterval(pollTimer);
	});

	function togglePart(idx: number) {
		const k = `${tab}:${idx}`;
		expanded = { ...expanded, [k]: !expanded[k] };
	}
	function isOpen(idx: number): boolean {
		return !!expanded[`${tab}:${idx}`];
	}
</script>

<div class="flex flex-col gap-4 max-w-6xl mx-auto">
	<div class="flex items-center gap-3 flex-wrap">
		<h1 class="text-lg font-mono text-[var(--color-mauve)]">context · actual prompt</h1>
		<div class="flex gap-1 ml-2">
			{#each ['latest', 'api', 'cc'] as t (t)}
				<button
					class="px-2 py-1 text-xs font-mono rounded border cursor-pointer"
					class:bg-active={tab === t}
					style:border-color={tab === t ? 'var(--color-mauve)' : 'var(--color-surface1)'}
					style:color={tab === t ? 'var(--color-mauve)' : 'var(--color-subtext0)'}
					onclick={() => (tab = t as Tab)}
				>
					{t}
				</button>
			{/each}
		</div>
		<button
			onclick={refreshAll}
			disabled={loading}
			class="ml-auto px-3 py-1 text-xs font-mono rounded border border-[var(--color-surface1)] hover:bg-[var(--color-surface0)] cursor-pointer disabled:opacity-50"
		>
			{loading ? '…' : '↻ refresh'}
		</button>
		<span class="text-xs font-mono text-[var(--color-overlay0)]">{lastRefreshed}</span>
	</div>

	{#if err}
		<div class="text-xs font-mono text-[var(--color-red)]">{err}</div>
	{/if}

	{#if !ctx}
			<div class="text-xs font-mono text-[var(--color-overlay0)]">loading…</div>
	{:else if ctx.error}
			<div class="text-xs font-mono text-[var(--color-red)]">error: {ctx.error}</div>
	{:else if ctx.empty}
			<div class="text-xs font-mono text-[var(--color-overlay0)]">
				no context snapshot yet
			</div>
	{:else}
			<!-- meta -->
			<div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs font-mono">
				<div class="border border-[var(--color-surface1)] rounded p-2">
					<div class="text-[var(--color-overlay0)]">runtime</div>
					<div class="text-[var(--color-text)]">{ctx.runtime ?? '—'}</div>
				</div>
				<div class="border border-[var(--color-surface1)] rounded p-2">
					<div class="text-[var(--color-overlay0)]">channel</div>
					<div class="text-[var(--color-text)]">{ctx.channel ?? '—'}</div>
				</div>
				<div class="border border-[var(--color-surface1)] rounded p-2">
					<div class="text-[var(--color-overlay0)]">timestamp</div>
					<div class="text-[var(--color-text)]">{fmtTime(ctx.timestamp)}</div>
				</div>
				<div class="border border-[var(--color-surface1)] rounded p-2">
					<div class="text-[var(--color-overlay0)]">session_id</div>
					<div class="text-[var(--color-text)] truncate" title={ctx.session_id ?? ''}>
						{ctx.session_id ?? '—'}
					</div>
				</div>
			</div>

			<!-- metrics -->
			{@const m = ctx.metrics ?? {}}
				<div
					class="border border-[var(--color-surface1)] rounded p-3 text-xs font-mono flex flex-wrap gap-x-6 gap-y-1"
				>
					<div>
						<span class="text-[var(--color-overlay0)]">model</span>
						<span class="ml-1 text-[var(--color-text)]">{m.model ?? '—'}</span>
					</div>
					<div>
						<span class="text-[var(--color-overlay0)]">latency</span>
						<span class="ml-1 text-[var(--color-text)]">
							{m.latency_ms !== undefined ? `${m.latency_ms} ms` : '—'}
						</span>
					</div>
					<div>
						<span class="text-[var(--color-overlay0)]">cost</span>
						<span class="ml-1 text-[var(--color-text)]">{fmtCost(m.cost_usd)}</span>
					</div>
					<div>
						<span class="text-[var(--color-overlay0)]">tokens in</span>
						<span class="ml-1 text-[var(--color-blue)]">{fmtNum(m.tokens_in)}</span>
					</div>
					<div>
						<span class="text-[var(--color-overlay0)]">tokens out</span>
						<span class="ml-1 text-[var(--color-green)]">{fmtNum(m.tokens_out)}</span>
					</div>
					<div>
						<span class="text-[var(--color-overlay0)]">cache read</span>
						<span class="ml-1 text-[var(--color-yellow)]">{fmtNum(m.tokens_cache_read)}</span>
					</div>
					<div>
						<span class="text-[var(--color-overlay0)]">cache write</span>
						<span class="ml-1 text-[var(--color-peach)]">{fmtNum(m.tokens_cache_creation)}</span>
					</div>
				</div>

			<!-- parts -->
			<div class="flex flex-col gap-2">
				{#each ctx.parts ?? [] as part, i (i)}
					{@const open = isOpen(i)}
					<div class="border border-[var(--color-surface1)] rounded">
						<button
							class="w-full text-left px-3 py-2 flex items-center gap-2 text-xs font-mono cursor-pointer hover:bg-[var(--color-surface0)]"
							onclick={() => togglePart(i)}
						>
							<span class="w-3 text-[var(--color-overlay0)]">{open ? '▾' : '▸'}</span>
							<span
								class="px-1.5 py-0.5 rounded text-[10px]"
								style:background-color="color-mix(in srgb, {colorFor(part.label)} 20%, transparent)"
								style:color={colorFor(part.label)}
							>
								{part.label}
							</span>
							<span class="text-[var(--color-overlay0)]">{part.role}</span>
							{#if part.cache}
								<span class="text-[10px] text-[var(--color-yellow)]">⚡cache</span>
							{/if}
							<span class="ml-auto text-[var(--color-overlay0)]">{fmtNum(part.length)} chars</span>
						</button>
						{#if open}
							<pre
								class="px-3 py-2 text-xs font-mono whitespace-pre-wrap break-words border-t border-[var(--color-surface1)] text-[var(--color-text)] bg-[var(--color-mantle)] max-h-96 overflow-auto">{part.text}</pre>
						{/if}
					</div>
				{/each}
			</div>
	{/if}
</div>

<style>
	.bg-active {
		background-color: color-mix(in srgb, var(--color-mauve) 15%, transparent);
	}
</style>
