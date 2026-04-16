<script lang="ts">
	import { onMount } from 'svelte';
	import { api, type ScheduleRow } from '$lib/api';

	let rows = $state<ScheduleRow[]>([]);
	let err = $state<string | null>(null);

	onMount(async () => {
		try {
			rows = await api.schedule();
		} catch (e) {
			err = (e as Error).message;
		}
	});
</script>

<div class="max-w-4xl">
	<h2 class="text-sm font-mono text-[var(--color-mauve)] mb-3">Scheduled wakes</h2>

	{#if err}
		<p class="text-[var(--color-red)] text-xs font-mono">{err}</p>
	{/if}

	<div class="bg-[var(--color-mantle)] border border-[var(--color-surface0)]">
		<table>
			<thead>
				<tr>
					<th class="w-40">wake_at</th>
					<th class="w-20">type</th>
					<th>reason</th>
				</tr>
			</thead>
			<tbody>
				{#each rows as r}
					<tr>
						<td class="font-mono text-xs text-[var(--color-sapphire)]">{r.wake_at}</td>
						<td class="font-mono text-xs text-[var(--color-peach)]">{r.type}</td>
						<td class="text-xs">{r.reason}</td>
					</tr>
				{/each}
				{#if !rows.length}
					<tr>
						<td colspan="3" class="text-xs text-[var(--color-overlay0)] text-center py-4">
							no pending wakes
						</td>
					</tr>
				{/if}
			</tbody>
		</table>
	</div>
</div>
