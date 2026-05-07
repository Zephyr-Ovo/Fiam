<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { api, type Status, type StateSnapshot, type EventRow, type TodoRow } from '$lib/api';

	let status = $state<Status | null>(null);
	let emotion = $state<StateSnapshot | null>(null);
	let recentEvents = $state<EventRow[]>([]);
	let upcoming = $state<TodoRow[]>([]);
	let err = $state<string | null>(null);
	let timer: ReturnType<typeof setInterval>;

	async function refresh() {
		try {
			const [s, e, ev, sc] = await Promise.all([
				api.status(),
				api.state().catch(() => null),
				api.events(10),
				api.todo().catch(() => [])
			]);
			status = s;
			emotion = e;
			recentEvents = ev;
			upcoming = sc.slice(0, 5);
			err = null;
		} catch (e) {
			err = (e as Error).message;
		}
	}

	onMount(() => {
		refresh();
		timer = setInterval(refresh, 5000);
	});
	onDestroy(() => clearInterval(timer));

	function tensionColor(t: number): string {
		if (t < 0.3) return 'var(--color-green)';
		if (t < 0.6) return 'var(--color-yellow)';
		return 'var(--color-red)';
	}
</script>

<div class="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-6xl">
	{#if err}
		<div
			class="col-span-full bg-[var(--color-red)]/10 border border-[var(--color-red)] px-3 py-2 text-sm font-mono"
		>
			{err} — backend not reachable?
		</div>
	{/if}

	<!-- Daemon status -->
	<div class="bg-[var(--color-mantle)] border border-[var(--color-surface0)] p-3">
		<h2 class="text-xs uppercase tracking-wide text-[var(--color-subtext0)] mb-2">Daemon</h2>
		{#if status}
			<div class="flex items-center gap-2 mb-2">
				<span
					class="w-2 h-2 rounded-full"
					style="background: {status.daemon === 'running'
						? 'var(--color-green)'
						: 'var(--color-red)'}"
				></span>
				<span class="font-mono text-sm">{status.daemon}</span>
				{#if status.pid}
					<span class="text-xs text-[var(--color-overlay0)] font-mono">PID {status.pid}</span>
				{/if}
			</div>
			<dl class="text-xs font-mono grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
				<dt class="text-[var(--color-subtext0)]">events</dt>
				<dd>{status.events}</dd>
				<dt class="text-[var(--color-subtext0)]">embeddings</dt>
				<dd>{status.embeddings}</dd>
				<dt class="text-[var(--color-subtext0)]">last</dt>
				<dd>{status.last_processed ?? '—'}</dd>
				<dt class="text-[var(--color-subtext0)]">home</dt>
				<dd class="truncate">{status.home}</dd>
			</dl>
		{:else}
			<p class="text-xs text-[var(--color-overlay0)]">loading…</p>
		{/if}
	</div>

	<!-- Emotional state -->
	<div class="bg-[var(--color-mantle)] border border-[var(--color-surface0)] p-3">
		<h2 class="text-xs uppercase tracking-wide text-[var(--color-subtext0)] mb-2">State</h2>
		{#if emotion}
			<div class="flex items-baseline gap-3 mb-2">
				<span class="font-mono text-lg text-[var(--color-peach)]">{emotion.mood}</span>
				<span class="text-xs font-mono" style="color: {tensionColor(emotion.tension)}">
					tension {emotion.tension.toFixed(2)}
				</span>
			</div>
			<p class="text-xs text-[var(--color-subtext1)] leading-relaxed">{emotion.reflection}</p>
			<p class="text-xs text-[var(--color-overlay0)] font-mono mt-2">
				updated {emotion.updated_at}
			</p>
		{:else}
			<p class="text-xs text-[var(--color-overlay0)]">no state.md yet</p>
		{/if}
	</div>

	<!-- Upcoming todo -->
	<div class="bg-[var(--color-mantle)] border border-[var(--color-surface0)] p-3">
		<h2 class="text-xs uppercase tracking-wide text-[var(--color-subtext0)] mb-2">
			Upcoming ({upcoming.length})
		</h2>
		{#if upcoming.length}
			<ul class="text-xs font-mono space-y-1">
				{#each upcoming as item}
					<li class="flex gap-2">
						<span class="text-[var(--color-sapphire)]">{item.at.slice(5, 16)}</span>
						<span class="text-[var(--color-overlay1)]">{item.type}</span>
						<span class="truncate">{item.reason}</span>
					</li>
				{/each}
			</ul>
		{:else}
			<p class="text-xs text-[var(--color-overlay0)]">no pending todos</p>
		{/if}
	</div>

	<!-- Recent events (spans 3 cols) -->
	<div class="md:col-span-3 bg-[var(--color-mantle)] border border-[var(--color-surface0)] p-3">
		<h2 class="text-xs uppercase tracking-wide text-[var(--color-subtext0)] mb-2">
			Recent events
		</h2>
		<table>
			<thead>
				<tr>
					<th class="w-32">time</th>
					<th class="w-16 text-right">int.</th>
					<th>preview</th>
				</tr>
			</thead>
			<tbody>
				{#each recentEvents as e}
					<tr>
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
						<td class="text-xs truncate max-w-[60ch]">{e.preview}</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
</div>
