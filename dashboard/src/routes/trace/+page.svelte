<script lang="ts">
	import { onMount } from 'svelte';
	import { api, type TracePayload, type TraceRow } from '$lib/api';

	let payload = $state<TracePayload | null>(null);
	let turnId = $state('');
	let requestId = $state('');
	let sessionId = $state('');
	let phase = $state('');
	let status = $state('');
	let limit = $state(200);
	let loading = $state(false);
	let err = $state<string | null>(null);
	let lastRefreshed = $state('—');
	let expanded = $state<Record<string, boolean>>({});

	async function load() {
		loading = true;
		err = null;
		try {
			payload = await api.debugTrace({
				turn_id: turnId.trim(),
				request_id: requestId.trim(),
				session_id: sessionId.trim(),
				phase: phase.trim(),
				status,
				limit
			});
			lastRefreshed = new Date().toLocaleTimeString();
		} catch (e) {
			err = (e as Error).message;
		} finally {
			loading = false;
		}
	}

	onMount(load);

	function submit(event: SubmitEvent) {
		event.preventDefault();
		load();
	}

	function clearFilters() {
		turnId = '';
		requestId = '';
		sessionId = '';
		phase = '';
		status = '';
		load();
	}

	function fmtTime(value?: string): string {
		if (!value) return '—';
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return value;
		return date.toLocaleTimeString();
	}

	function fmtDuration(row: TraceRow): string {
		const ms = Number(row.duration_ms || 0);
		if (!ms) return '—';
		return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`;
	}

	function scene(row: TraceRow): string {
		const channel = row.channel || '—';
		return row.surface ? `${channel}/${row.surface}` : channel;
	}

	function statusColor(value?: string): string {
		if (value === 'ok') return 'var(--color-green)';
		if (value === 'error') return 'var(--color-red)';
		if (value === 'skipped') return 'var(--color-overlay1)';
		return 'var(--color-subtext0)';
	}

	function phaseColor(value?: string): string {
		if (value?.startsWith('dashboard.')) return 'var(--color-sapphire)';
		if (value?.startsWith('commit.')) return 'var(--color-mauve)';
		return 'var(--color-peach)';
	}

	function rowKey(row: TraceRow, index: number): string {
		return `${row.turn_id}:${row.phase}:${row.started_at}:${index}`;
	}

	function toggle(key: string) {
		expanded = { ...expanded, [key]: !expanded[key] };
	}

	function refsText(row: TraceRow): string {
		return JSON.stringify(row.refs ?? {}, null, 2);
	}

	function count(value?: Record<string, number>, key = ''): number {
		if (!value) return 0;
		return key ? Number(value[key] || 0) : Object.values(value).reduce((sum, item) => sum + Number(item || 0), 0);
	}
</script>

<div class="flex flex-col gap-4 max-w-7xl mx-auto">
	<div class="flex items-center gap-3 flex-wrap">
		<h1 class="text-lg font-mono text-[var(--color-mauve)]">trace</h1>
		<span class="text-xs font-mono text-[var(--color-overlay0)]">
			{payload?.returned ?? 0}/{payload?.total ?? 0} rows · {payload?.path ?? 'store/turn_traces.jsonl'} · {lastRefreshed}
		</span>
		<button
			onclick={load}
			disabled={loading}
			class="ml-auto px-3 py-1 text-xs font-mono rounded border border-[var(--color-surface1)] hover:bg-[var(--color-surface0)] cursor-pointer disabled:opacity-50"
		>
			{loading ? '…' : '↻ refresh'}
		</button>
	</div>

	<form class="grid grid-cols-1 md:grid-cols-6 gap-2 text-xs font-mono" onsubmit={submit}>
		<label class="grid gap-1 md:col-span-2">
			<span class="text-[var(--color-subtext0)]">turn_id</span>
			<input bind:value={turnId} class="bg-[var(--color-mantle)] border border-[var(--color-surface1)] rounded px-3 py-2 focus:outline-none focus:border-[var(--color-mauve)]" />
		</label>
		<label class="grid gap-1 md:col-span-2">
			<span class="text-[var(--color-subtext0)]">request_id</span>
			<input bind:value={requestId} class="bg-[var(--color-mantle)] border border-[var(--color-surface1)] rounded px-3 py-2 focus:outline-none focus:border-[var(--color-mauve)]" />
		</label>
		<label class="grid gap-1 md:col-span-2">
			<span class="text-[var(--color-subtext0)]">session_id</span>
			<input bind:value={sessionId} class="bg-[var(--color-mantle)] border border-[var(--color-surface1)] rounded px-3 py-2 focus:outline-none focus:border-[var(--color-mauve)]" />
		</label>
		<label class="grid gap-1 md:col-span-2">
			<span class="text-[var(--color-subtext0)]">phase</span>
			<input bind:value={phase} placeholder="commit.dispatch" class="bg-[var(--color-mantle)] border border-[var(--color-surface1)] rounded px-3 py-2 focus:outline-none focus:border-[var(--color-mauve)]" />
		</label>
		<label class="grid gap-1">
			<span class="text-[var(--color-subtext0)]">status</span>
			<select bind:value={status} class="bg-[var(--color-mantle)] border border-[var(--color-surface1)] rounded px-3 py-2 focus:outline-none focus:border-[var(--color-mauve)]">
				<option value="">any</option>
				<option value="ok">ok</option>
				<option value="error">error</option>
				<option value="skipped">skipped</option>
			</select>
		</label>
		<label class="grid gap-1">
			<span class="text-[var(--color-subtext0)]">limit</span>
			<input type="number" min="1" max="1000" bind:value={limit} class="bg-[var(--color-mantle)] border border-[var(--color-surface1)] rounded px-3 py-2 focus:outline-none focus:border-[var(--color-mauve)]" />
		</label>
		<div class="flex gap-2 md:col-span-2 self-end">
			<button type="submit" disabled={loading} class="px-3 py-2 rounded border border-[var(--color-surface1)] hover:bg-[var(--color-surface0)] cursor-pointer disabled:opacity-50">apply</button>
			<button type="button" disabled={loading} onclick={clearFilters} class="px-3 py-2 rounded border border-[var(--color-surface1)] hover:bg-[var(--color-surface0)] cursor-pointer disabled:opacity-50">clear</button>
		</div>
	</form>

	{#if err}
		<div class="text-xs font-mono text-[var(--color-red)] border border-[var(--color-red)]/60 bg-[var(--color-red)]/10 rounded p-3">
			{err}
		</div>
	{/if}

	<div class="grid grid-cols-2 md:grid-cols-5 gap-2 text-xs font-mono">
		<div class="border border-[var(--color-surface0)] rounded bg-[var(--color-mantle)] p-3">
			<div class="text-[var(--color-overlay0)]">rows</div>
			<div class="text-base text-[var(--color-text)]">{count(payload?.summary?.by_status)}</div>
		</div>
		<div class="border border-[var(--color-surface0)] rounded bg-[var(--color-mantle)] p-3">
			<div class="text-[var(--color-overlay0)]">errors</div>
			<div class="text-base text-[var(--color-red)]">{count(payload?.summary?.by_status, 'error')}</div>
		</div>
		<div class="border border-[var(--color-surface0)] rounded bg-[var(--color-mantle)] p-3">
			<div class="text-[var(--color-overlay0)]">retry/fail phases</div>
			<div class="text-base text-[var(--color-peach)]">{payload?.summary?.retry_phases ?? 0}</div>
		</div>
		<div class="border border-[var(--color-surface0)] rounded bg-[var(--color-mantle)] p-3">
			<div class="text-[var(--color-overlay0)]">duration</div>
			<div class="text-base text-[var(--color-sapphire)]">{payload?.summary?.total_duration_ms ?? 0} ms</div>
		</div>
		<div class="border border-[var(--color-surface0)] rounded bg-[var(--color-mantle)] p-3">
			<div class="text-[var(--color-overlay0)]">top phase</div>
			<div class="text-base text-[var(--color-mauve)] truncate">{payload?.summary?.slowest?.[0]?.phase ?? '—'}</div>
		</div>
	</div>

	<div class="border border-[var(--color-surface0)] rounded bg-[var(--color-mantle)] overflow-auto">
		<table class="min-w-[1200px]">
			<thead>
				<tr>
					<th class="w-28">time</th>
					<th class="w-44">phase</th>
					<th class="w-24">status</th>
					<th class="w-28">duration</th>
					<th class="w-48">scene</th>
					<th class="w-40">runtime</th>
					<th>ids</th>
				</tr>
			</thead>
			<tbody>
				{#each payload?.rows ?? [] as row, i (rowKey(row, i))}
					{@const key = rowKey(row, i)}
					<tr class="align-top hover:bg-[var(--color-surface0)]/30">
						<td class="font-mono text-xs text-[var(--color-subtext1)]">{fmtTime(row.started_at)}</td>
						<td class="font-mono text-xs">
							<button class="text-left break-all cursor-pointer" style:color={phaseColor(row.phase)} onclick={() => toggle(key)}>
								{expanded[key] ? '▾' : '▸'} {row.phase}
							</button>
							{#if row.error}
								<div class="text-[var(--color-red)] break-words mt-1">{row.error}</div>
							{/if}
						</td>
						<td class="font-mono text-xs">
							<span class="px-1.5 py-0.5 rounded border" style:color={statusColor(row.status)} style:border-color={statusColor(row.status)}>{row.status}</span>
						</td>
						<td class="font-mono text-xs">{fmtDuration(row)}</td>
						<td class="font-mono text-xs">
							<div>{scene(row)}</div>
							<div class="text-[var(--color-overlay0)]">{row.model || '—'}</div>
						</td>
						<td class="font-mono text-xs">
							<div>{row.runtime || '—'}</div>
							<div class="text-[var(--color-overlay0)]">attempt {row.attempt || 0}</div>
						</td>
						<td class="font-mono text-xs break-all">
							<div>{row.turn_id || '—'}</div>
							<div class="text-[var(--color-overlay0)]">{row.request_id || row.session_id || '—'}</div>
						</td>
					</tr>
					{#if expanded[key]}
						<tr>
							<td colspan="7" class="bg-[var(--color-base)]/50">
								<pre class="text-xs font-mono whitespace-pre-wrap break-words max-h-72 overflow-auto">{refsText(row)}</pre>
							</td>
						</tr>
					{/if}
				{/each}
				{#if !loading && (payload?.rows ?? []).length === 0}
					<tr>
						<td colspan="7" class="text-center text-xs text-[var(--color-overlay0)] py-8">no trace rows</td>
					</tr>
				{/if}
			</tbody>
		</table>
	</div>
</div>