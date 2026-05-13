<script lang="ts">
	import { onMount } from 'svelte';
	import { api, type ObjectRecord, type ObjectsPayload } from '$lib/api';

	let payload = $state<ObjectsPayload | null>(null);
	let query = $state('');
	let token = $state('');
	let limit = $state(50);
	let loading = $state(false);
	let err = $state<string | null>(null);
	let lastRefreshed = $state('—');

	async function load() {
		loading = true;
		err = null;
		try {
			payload = await api.objects({ query: query.trim(), token: token.trim(), limit });
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

	function fmtTime(value?: string): string {
		if (!value) return '—';
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return value;
		return date.toLocaleString();
	}

	function fmtSize(value?: number): string {
		const bytes = Number(value || 0);
		if (!bytes) return '—';
		if (bytes < 1024) return `${bytes} B`;
		if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
		return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
	}

	function scene(record: ObjectRecord): string {
		const channel = record.channel || '—';
		return record.surface ? `${channel}/${record.surface}` : channel;
	}

	function recordTitle(record: ObjectRecord): string {
		return record.name || record.summary || record.token || record.object_hash.slice(0, 12);
	}

	function tagColor(tag: string): string {
		if (tag === 'inbound') return 'var(--color-blue)';
		if (tag === 'outbound') return 'var(--color-peach)';
		if (tag === 'events') return 'var(--color-green)';
		if (tag === 'manifest') return 'var(--color-yellow)';
		return 'var(--color-subtext0)';
	}
</script>

<div class="flex flex-col gap-4 max-w-7xl mx-auto">
	<div class="flex items-center gap-3 flex-wrap">
		<h1 class="text-lg font-mono text-[var(--color-mauve)]">objects</h1>
		<span class="text-xs font-mono text-[var(--color-overlay0)]">{payload?.returned ?? 0} shown · {lastRefreshed}</span>
		<button
			onclick={load}
			disabled={loading}
			class="ml-auto px-3 py-1 text-xs font-mono rounded border border-[var(--color-surface1)] hover:bg-[var(--color-surface0)] cursor-pointer disabled:opacity-50"
		>
			{loading ? '…' : '↻ refresh'}
		</button>
	</div>

	<form class="grid grid-cols-1 md:grid-cols-[1fr_16rem_8rem_auto] gap-2 text-xs font-mono" onsubmit={submit}>
		<label class="grid gap-1">
			<span class="text-[var(--color-subtext0)]">search</span>
			<input
				bind:value={query}
				class="bg-[var(--color-mantle)] border border-[var(--color-surface1)] rounded px-3 py-2 focus:outline-none focus:border-[var(--color-mauve)]"
			/>
		</label>
		<label class="grid gap-1">
			<span class="text-[var(--color-subtext0)]">token</span>
			<input
				bind:value={token}
				placeholder="obj:..."
				class="bg-[var(--color-mantle)] border border-[var(--color-surface1)] rounded px-3 py-2 focus:outline-none focus:border-[var(--color-mauve)]"
			/>
		</label>
		<label class="grid gap-1">
			<span class="text-[var(--color-subtext0)]">limit</span>
			<input
				type="number"
				min="1"
				max="100"
				bind:value={limit}
				class="bg-[var(--color-mantle)] border border-[var(--color-surface1)] rounded px-3 py-2 focus:outline-none focus:border-[var(--color-mauve)]"
			/>
		</label>
		<button
			type="submit"
			disabled={loading}
			class="self-end px-3 py-2 rounded border border-[var(--color-surface1)] hover:bg-[var(--color-surface0)] cursor-pointer disabled:opacity-50"
		>
			search
		</button>
	</form>

	{#if err}
		<div class="text-xs font-mono text-[var(--color-red)] border border-[var(--color-red)]/60 bg-[var(--color-red)]/10 rounded p-3">
			{err}
		</div>
	{/if}

	{#if payload?.token}
		<div class="border border-[var(--color-surface1)] rounded p-3 text-xs font-mono flex flex-wrap gap-x-4 gap-y-1">
			<span class="text-[var(--color-overlay0)]">token</span>
			<span class="text-[var(--color-text)] break-all">{payload.token}</span>
			<span class="text-[var(--color-overlay0)]">hash</span>
			<span class="text-[var(--color-green)] break-all">{payload.object_hash || 'unresolved'}</span>
		</div>
	{/if}

	<div class="border border-[var(--color-surface0)] rounded bg-[var(--color-mantle)] overflow-auto">
		<table class="min-w-[1100px]">
			<thead>
				<tr>
					<th class="w-48">object</th>
					<th>summary</th>
					<th class="w-36">type</th>
					<th class="w-44">scene</th>
					<th class="w-44">fact</th>
					<th class="w-40">time</th>
				</tr>
			</thead>
			<tbody>
				{#each payload?.records ?? [] as record (record.object_hash)}
					<tr class="align-top hover:bg-[var(--color-surface0)]/30">
						<td class="font-mono text-xs">
							<div class="text-[var(--color-sapphire)] break-all">{record.token}</div>
							<div class="text-[10px] text-[var(--color-overlay0)] break-all mt-1">{record.object_hash}</div>
						</td>
						<td class="text-xs">
							<div class="text-[var(--color-text)] break-words">{recordTitle(record)}</div>
							{#if record.summary && record.summary !== recordTitle(record)}
								<div class="text-[var(--color-subtext1)] break-words mt-1">{record.summary}</div>
							{/if}
							{#if record.tags?.length}
								<div class="flex flex-wrap gap-1 mt-2">
									{#each record.tags as tag}
										<span class="px-1.5 py-0.5 rounded border text-[10px] text-[var(--color-overlay1)] border-[var(--color-surface1)]">{tag}</span>
									{/each}
								</div>
							{/if}
						</td>
						<td class="font-mono text-xs">
							<div>{record.mime || '—'}</div>
							<div class="text-[var(--color-overlay0)]">{fmtSize(record.size)}</div>
						</td>
						<td class="font-mono text-xs">
							<div>{scene(record)}</div>
							<div class="text-[var(--color-overlay0)]">{record.actor || '—'} · {record.kind || '—'}</div>
						</td>
						<td class="font-mono text-xs">
							<div class="flex flex-wrap gap-1 mb-1">
								{#each [record.direction, record.source, record.provenance].filter(Boolean) as chip}
									<span class="px-1.5 py-0.5 rounded border" style:color={tagColor(chip || '')} style:border-color={tagColor(chip || '')}>{chip}</span>
								{/each}
							</div>
							<div class="text-[var(--color-overlay0)] break-all">{record.dispatch_id || record.turn_id || record.event_id || '—'}</div>
						</td>
						<td class="font-mono text-xs text-[var(--color-subtext1)]">{fmtTime(record.t)}</td>
					</tr>
				{/each}
				{#if !loading && (payload?.records ?? []).length === 0}
					<tr>
						<td colspan="6" class="text-center text-xs text-[var(--color-overlay0)] py-8">no objects</td>
					</tr>
				{/if}
			</tbody>
		</table>
	</div>
</div>