<script lang="ts">
	import { onMount } from 'svelte';
	import { api, type EventRow } from '$lib/api';
	import EventDetail from '$lib/EventDetail.svelte';

	let events = $state<EventRow[]>([]);
	let filter = $state('');
	let err = $state<string | null>(null);
	let detailId = $state<string | null>(null);

	const filtered = $derived(
		events.filter(
			(e) => !filter || e.preview?.toLowerCase().includes(filter.toLowerCase()) || e.id.includes(filter)
		)
	);

	onMount(async () => {
		try {
			events = await api.events(200);
		} catch (e) {
			err = (e as Error).message;
		}
	});
</script>

<div class="max-w-6xl">
	<div class="flex items-center gap-3 mb-3">
		<input
			type="text"
			bind:value={filter}
			placeholder="filter…"
			class="bg-[var(--color-mantle)] border border-[var(--color-surface0)] px-2 py-1 text-sm font-mono rounded w-64 focus:outline-none focus:border-[var(--color-mauve)]"
		/>
		<span class="text-xs text-[var(--color-overlay0)] font-mono">
			{filtered.length} / {events.length}
		</span>
	</div>

	{#if err}
		<p class="text-[var(--color-red)] text-xs font-mono">{err}</p>
	{/if}

	<div class="bg-[var(--color-mantle)] border border-[var(--color-surface0)] overflow-auto">
		<table>
			<thead class="sticky top-0 bg-[var(--color-mantle)]">
				<tr>
					<th class="w-28">id</th>
					<th class="w-32">time</th>
					<th class="w-16 text-right">int.</th>
					<th>preview</th>
				</tr>
			</thead>
			<tbody>
				{#each filtered as e}
					<tr
						class="hover:bg-[var(--color-surface0)]/40 cursor-pointer"
						onclick={() => (detailId = e.id)}
					>
						<td class="font-mono text-xs text-[var(--color-overlay1)]">{e.id}</td>
						<td class="font-mono text-xs text-[var(--color-sapphire)]">{e.time?.slice(0, 16)}</td>
						<td
							class="font-mono text-xs text-right"
							style="color: {e.intensity > 0.7
								? 'var(--color-red)'
								: e.intensity > 0.4
									? 'var(--color-yellow)'
									: 'var(--color-overlay1)'}"
						>
							{e.intensity?.toFixed(2) ?? '—'}
						</td>
						<td class="text-xs">{e.preview}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>

	{#if detailId}
		<EventDetail id={detailId} onclose={() => (detailId = null)} />
	{/if}
</div>
