<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import {
		api,
		type CatalogEntry,
		type CatalogPayload,
		type Status,
		type StateSnapshot,
		type EventRow,
		type TodoRow
	} from '$lib/api';

	let status = $state<Status | null>(null);
	let emotion = $state<StateSnapshot | null>(null);
	let recentEvents = $state<EventRow[]>([]);
	let upcoming = $state<TodoRow[]>([]);
	let catalog = $state<CatalogPayload | null>(null);
	let err = $state<string | null>(null);
	let catalogErr = $state<string | null>(null);
	let catalogBusy = $state(false);
	let editingCatalog = $state(false);
	let catalogForm = $state({
		family: 'claude',
		provider: 'poe',
		model: '',
		fallbacks: '',
		extended_thinking: false,
		budget_tokens: 0
	});
	let timer: ReturnType<typeof setInterval>;

	async function refresh() {
		try {
			const [s, e, ev, sc, cat] = await Promise.all([
				api.status(),
				api.state().catch(() => null),
				api.events(10),
				api.todo().catch(() => []),
				api.catalog().catch(() => null)
			]);
			status = s;
			emotion = e;
			recentEvents = ev;
			upcoming = sc.slice(0, 5);
			catalog = cat;
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

	function startCatalogEdit(family: string, entry?: CatalogEntry) {
		catalogForm = {
			family,
			provider: entry?.provider || (family === 'gemini' ? 'aistudio' : 'poe'),
			model: entry?.model || '',
			fallbacks: (entry?.fallbacks || []).join(', '),
			extended_thinking: Boolean(entry?.extended_thinking),
			budget_tokens: entry?.budget_tokens || 0
		};
		catalogErr = null;
		editingCatalog = true;
	}

	function modelOptions(provider: string): string[] {
		return catalog?.cache?.[provider]?.models || [];
	}

	async function refreshProvider() {
		catalogBusy = true;
		catalogErr = null;
		try {
			await api.refreshCatalog(catalogForm.provider);
			catalog = await api.catalog();
		} catch (e) {
			catalogErr = (e as Error).message;
		} finally {
			catalogBusy = false;
		}
	}

	async function saveCatalog() {
		catalogBusy = true;
		catalogErr = null;
		try {
			await api.saveCatalog({
				family: catalogForm.family,
				provider: catalogForm.provider,
				model: catalogForm.model,
				fallbacks: catalogForm.fallbacks
					.split(',')
					.map((item) => item.trim())
					.filter(Boolean),
				extended_thinking: catalogForm.extended_thinking,
				budget_tokens: Number(catalogForm.budget_tokens) || 0
			});
			catalog = await api.catalog();
			editingCatalog = false;
		} catch (e) {
			catalogErr = (e as Error).message;
		} finally {
			catalogBusy = false;
		}
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

	<!-- Model catalog -->
	<div class="md:col-span-3 bg-[var(--color-mantle)] border border-[var(--color-surface0)] p-3">
		<div class="flex items-center justify-between gap-3 mb-2">
			<h2 class="text-xs uppercase tracking-wide text-[var(--color-subtext0)]">Catalog</h2>
			<button
				class="text-xs font-mono border border-[var(--color-surface1)] px-2 py-1 hover:bg-[var(--color-surface0)]"
				onclick={() => startCatalogEdit('claude', catalog?.catalog?.claude)}
			>
				edit
			</button>
		</div>
		{#if catalog}
			<div class="grid grid-cols-1 md:grid-cols-2 gap-3">
				{#each catalog.families as family}
					{@const entry = catalog.catalog[family]}
					<div class="border border-[var(--color-surface0)] p-2">
						<div class="flex items-center justify-between gap-2">
							<div class="font-mono text-sm text-[var(--color-peach)]">{family}</div>
							<button
								class="text-xs font-mono border border-[var(--color-surface1)] px-2 py-1 hover:bg-[var(--color-surface0)]"
								onclick={() => startCatalogEdit(family, entry)}
							>
								edit
							</button>
						</div>
						<dl class="text-xs font-mono grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 mt-2">
							<dt class="text-[var(--color-subtext0)]">provider</dt>
							<dd>{entry?.provider || '—'}</dd>
							<dt class="text-[var(--color-subtext0)]">model</dt>
							<dd class="truncate">{entry?.model || '—'}</dd>
							<dt class="text-[var(--color-subtext0)]">fallbacks</dt>
							<dd class="truncate">{entry?.fallbacks?.join(' → ') || '—'}</dd>
							<dt class="text-[var(--color-subtext0)]">thinking</dt>
							<dd>{entry?.extended_thinking ? `on · ${entry.budget_tokens || 0}` : 'off'}</dd>
						</dl>
					</div>
				{/each}
			</div>
		{:else}
			<p class="text-xs text-[var(--color-overlay0)]">loading catalog…</p>
		{/if}
	</div>

	{#if editingCatalog}
		<div class="fixed inset-0 z-20 bg-black/60 flex items-center justify-center p-4">
			<div class="w-full max-w-xl bg-[var(--color-base)] border border-[var(--color-surface1)] p-4">
				<div class="flex items-center justify-between gap-3 mb-4">
					<h2 class="text-xs uppercase tracking-wide text-[var(--color-subtext0)]">Edit Catalog</h2>
					<button
						class="text-xs font-mono border border-[var(--color-surface1)] px-2 py-1 hover:bg-[var(--color-surface0)]"
						onclick={() => (editingCatalog = false)}
					>
						close
					</button>
				</div>
				<div class="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs font-mono">
					<label class="grid gap-1">
						<span class="text-[var(--color-subtext0)]">family</span>
						<select bind:value={catalogForm.family} class="bg-[var(--color-mantle)] border border-[var(--color-surface1)] p-2">
							{#each catalog?.families || ['claude', 'gemini'] as family}
								<option value={family}>{family}</option>
							{/each}
						</select>
					</label>
					<label class="grid gap-1">
						<span class="text-[var(--color-subtext0)]">provider</span>
						<select bind:value={catalogForm.provider} class="bg-[var(--color-mantle)] border border-[var(--color-surface1)] p-2">
							{#each catalog?.providers || ['poe', 'anthropic', 'aistudio'] as provider}
								<option value={provider}>{provider}</option>
							{/each}
						</select>
					</label>
					<label class="grid gap-1 md:col-span-2">
						<span class="text-[var(--color-subtext0)]">model</span>
						<input
							list="catalog-model-options"
							bind:value={catalogForm.model}
							class="bg-[var(--color-mantle)] border border-[var(--color-surface1)] p-2"
						/>
						<datalist id="catalog-model-options">
							{#each modelOptions(catalogForm.provider) as model}
								<option value={model}></option>
							{/each}
						</datalist>
					</label>
					<label class="grid gap-1 md:col-span-2">
						<span class="text-[var(--color-subtext0)]">fallback chain</span>
						<input
							bind:value={catalogForm.fallbacks}
							placeholder="model-a, model-b"
							class="bg-[var(--color-mantle)] border border-[var(--color-surface1)] p-2"
						/>
					</label>
					<label class="flex items-center gap-2">
						<input type="checkbox" bind:checked={catalogForm.extended_thinking} />
						<span>extended thinking</span>
					</label>
					<label class="grid gap-1">
						<span class="text-[var(--color-subtext0)]">budget tokens</span>
						<input
							type="number"
							min="0"
							bind:value={catalogForm.budget_tokens}
							class="bg-[var(--color-mantle)] border border-[var(--color-surface1)] p-2"
						/>
					</label>
				</div>
				{#if catalogErr}
					<p class="text-xs font-mono text-[var(--color-red)] mt-3">{catalogErr}</p>
				{/if}
				<div class="flex justify-end gap-2 mt-4">
					<button
						class="text-xs font-mono border border-[var(--color-surface1)] px-3 py-2 hover:bg-[var(--color-surface0)] disabled:opacity-50"
						disabled={catalogBusy}
						onclick={refreshProvider}
					>
						refresh
					</button>
					<button
						class="text-xs font-mono border border-[var(--color-green)] px-3 py-2 hover:bg-[var(--color-green)]/10 disabled:opacity-50"
						disabled={catalogBusy || !catalogForm.model}
						onclick={saveCatalog}
					>
						save
					</button>
				</div>
			</div>
		</div>
	{/if}

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
