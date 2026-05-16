<script lang="ts">
	import { onMount } from 'svelte';
	import {
		api,
		type RuntimeConfig,
		type CatalogEntry,
		type CatalogPayload,
		type PluginManifest
	} from '$lib/api';

	let config = $state<RuntimeConfig | null>(null);
	let catalog = $state<CatalogPayload | null>(null);
	let plugins = $state<PluginManifest[]>([]);
	let err = $state<string | null>(null);
	let busy = $state(false);
	let runtimeErr = $state<string | null>(null);
	let runtimeBusy = $state(false);
	let editingCatalog = $state(false);
	let catalogErr = $state<string | null>(null);
	let catalogBusy = $state(false);
	let runtimeForm = $state({
		default_runtime: 'auto' as 'auto' | 'api' | 'cc',
		recall_include_recent: true,
		cc_model: '',
		cc_effort: '',
		cc_disallowed_tools: ''
	});
	let catalogForm = $state({
		family: 'claude',
		provider: 'poe',
		model: '',
		fallbacks: '',
		extended_thinking: false,
		budget_tokens: 0
	});

	async function refresh() {
		busy = true;
		err = null;
		try {
			const [cfg, cat, pluginPayload] = await Promise.all([
				api.config().catch(() => null),
				api.catalog().catch(() => null),
				api.plugins().catch(() => ({ plugins: [] }))
			]);
			config = cfg;
			if (cfg) syncRuntimeForm(cfg);
			catalog = cat;
			plugins = pluginPayload.plugins || [];
		} catch (e) {
			err = (e as Error).message;
		} finally {
			busy = false;
		}
	}

	onMount(refresh);

	function syncRuntimeForm(cfg: RuntimeConfig) {
		runtimeForm = {
			default_runtime: cfg.app?.default_runtime || 'auto',
			recall_include_recent: Boolean(cfg.app?.recall_include_recent ?? true),
			cc_model: cfg.cc?.model || '',
			cc_effort: cfg.cc?.effort || '',
			cc_disallowed_tools: cfg.cc?.disallowed_tools || ''
		};
	}

	function defaultProviderForFamily(family: string) {
		if (family === 'gemini') return 'aistudio';
		if (family === 'deepseek') return 'deepseek';
		if (family === 'gpt') return 'openrouter';
		return 'poe';
	}

	async function saveRuntime(clearRouteState = false) {
		runtimeBusy = true;
		runtimeErr = null;
		try {
			const result = await api.saveRuntimeConfig({
				default_runtime: runtimeForm.default_runtime,
				recall_include_recent: runtimeForm.recall_include_recent,
				cc_model: runtimeForm.cc_model,
				cc_effort: runtimeForm.cc_effort,
				cc_disallowed_tools: runtimeForm.cc_disallowed_tools,
				clear_route_state: clearRouteState
			});
			config = result.config;
			syncRuntimeForm(result.config);
		} catch (e) {
			runtimeErr = (e as Error).message;
		} finally {
			runtimeBusy = false;
		}
	}

	async function setMemoryMode(mode: 'manual' | 'auto') {
		if (!config || config.memory_mode === mode) return;
		try {
			await api.setMemoryMode(mode);
			config = { ...config, memory_mode: mode };
		} catch (e) {
			err = (e as Error).message;
		}
	}

	async function togglePlugin(id: string, enabled: boolean) {
		try {
			await api.setPluginEnabled(id, enabled);
			plugins = plugins.map((plugin) => (plugin.id === id ? { ...plugin, enabled } : plugin));
		} catch (e) {
			err = (e as Error).message;
		}
	}

	function startCatalogEdit(family: string, entry?: CatalogEntry) {
		catalogForm = {
			family,
			provider: entry?.provider || defaultProviderForFamily(family),
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

<div class="max-w-6xl mx-auto flex flex-col gap-4">
	<div class="flex items-center gap-3">
		<h1 class="text-lg font-mono text-[var(--color-mauve)]">panel</h1>
		<button
			onclick={refresh}
			disabled={busy}
			class="ml-auto px-3 py-1 text-xs font-mono rounded border border-[var(--color-surface1)] hover:bg-[var(--color-surface0)] cursor-pointer disabled:opacity-50"
		>
			{busy ? '…' : '↻ refresh'}
		</button>
	</div>

	{#if err}
		<div class="text-xs font-mono text-[var(--color-red)] border border-[var(--color-red)]/60 bg-[var(--color-red)]/10 rounded p-3">
			{err}
		</div>
	{/if}

	<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
		<div class="bg-[var(--color-mantle)] border border-[var(--color-surface0)] p-3">
			<div class="flex items-center justify-between gap-3 mb-3">
				<h2 class="text-xs uppercase tracking-wide text-[var(--color-subtext0)]">runtime</h2>
				<span class="text-xs font-mono text-[var(--color-overlay0)]">{config?.cc?.transport || 'legacy'}</span>
			</div>
			<div class="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs font-mono">
				<label class="grid gap-1">
					<span class="text-[var(--color-subtext0)]">default</span>
					<select bind:value={runtimeForm.default_runtime} class="bg-[var(--color-base)] border border-[var(--color-surface1)] p-2">
						<option value="auto">auto</option>
						<option value="api">api</option>
						<option value="cc">cc</option>
					</select>
				</label>
				<label class="grid gap-1">
					<span class="text-[var(--color-subtext0)]">cc model</span>
					<input bind:value={runtimeForm.cc_model} placeholder="opus, sonnet, ..." class="bg-[var(--color-base)] border border-[var(--color-surface1)] p-2" />
				</label>
				<label class="grid gap-1">
					<span class="text-[var(--color-subtext0)]">cc effort</span>
					<input bind:value={runtimeForm.cc_effort} placeholder="max, high, ..." class="bg-[var(--color-base)] border border-[var(--color-surface1)] p-2" />
				</label>
				<label class="grid gap-1 sm:col-span-3">
					<span class="text-[var(--color-subtext0)]">cc disallowed tools</span>
					<input bind:value={runtimeForm.cc_disallowed_tools} placeholder="WebFetch,NotebookEdit" class="bg-[var(--color-base)] border border-[var(--color-surface1)] p-2" />
				</label>
			</div>
			<div class="flex flex-wrap items-center gap-4 text-xs font-mono mt-3">
				<label class="flex items-center gap-2">
					<input type="checkbox" bind:checked={runtimeForm.recall_include_recent} />
					<span>recent recall</span>
				</label>
				<label class="flex items-center gap-2">
					<input
						type="radio"
						name="memory_mode"
						checked={config?.memory_mode === 'manual'}
						onchange={() => setMemoryMode('manual')}
					/>
					<span>manual memory</span>
				</label>
				<label class="flex items-center gap-2">
					<input
						type="radio"
						name="memory_mode"
						checked={config?.memory_mode === 'auto'}
						onchange={() => setMemoryMode('auto')}
					/>
					<span>auto memory</span>
				</label>
			</div>
			<div class="flex items-center justify-between gap-3 mt-3 text-xs font-mono">
				<div class="min-w-0 text-[var(--color-overlay0)] truncate">
					route: {config?.route_state?.family || 'none'}
					{#if config?.route_state?.remaining_turns}
						· {config.route_state.remaining_turns} turns
					{/if}
				</div>
				<div class="flex gap-2">
					<button
						class="border border-[var(--color-surface1)] px-2 py-1 hover:bg-[var(--color-surface0)] disabled:opacity-50"
						disabled={runtimeBusy || !config?.route_state?.family}
						onclick={() => saveRuntime(true)}
					>
						clear route
					</button>
					<button
						class="border border-[var(--color-green)] px-3 py-1 hover:bg-[var(--color-green)]/10 disabled:opacity-50"
						disabled={runtimeBusy}
						onclick={() => saveRuntime(false)}
					>
						save
					</button>
				</div>
			</div>
			{#if runtimeErr}
				<p class="text-xs font-mono text-[var(--color-red)] mt-2">{runtimeErr}</p>
			{/if}
		</div>

		<div class="bg-[var(--color-mantle)] border border-[var(--color-surface0)] p-3">
			<h2 class="text-xs uppercase tracking-wide text-[var(--color-subtext0)] mb-2">plugins</h2>
			<div class="max-h-56 overflow-auto flex flex-col gap-2 pr-1">
				{#each plugins as plugin}
					<label class="flex items-center justify-between gap-3 text-xs font-mono border border-[var(--color-surface0)] rounded px-2 py-1.5">
						<span class="truncate">{plugin.id}</span>
						<input
							type="checkbox"
							checked={plugin.enabled}
							onchange={(e) => togglePlugin(plugin.id, (e.currentTarget as HTMLInputElement).checked)}
						/>
					</label>
				{/each}
				{#if plugins.length === 0}
					<p class="text-xs text-[var(--color-overlay0)]">no plugins</p>
				{/if}
			</div>
		</div>
	</div>

	<div class="bg-[var(--color-mantle)] border border-[var(--color-surface0)] p-3">
		<div class="flex items-center justify-between gap-3 mb-2">
			<h2 class="text-xs uppercase tracking-wide text-[var(--color-subtext0)]">catalog</h2>
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
							{#each catalog?.families || ['claude', 'gpt', 'deepseek', 'gemini'] as family}
								<option value={family}>{family}</option>
							{/each}
						</select>
					</label>
					<label class="grid gap-1">
						<span class="text-[var(--color-subtext0)]">provider</span>
						<select bind:value={catalogForm.provider} class="bg-[var(--color-mantle)] border border-[var(--color-surface1)] p-2">
							{#each catalog?.providers || ['openrouter', 'poe', 'deepseek', 'aistudio', 'vertex', 'anthropic'] as provider}
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
</div>
