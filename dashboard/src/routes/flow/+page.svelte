<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { api, type FlowPayload } from '$lib/api';

	let beats = $state<FlowPayload['beats']>([]);
	let total = $state(0);
	let loading = $state(true);
	let err = $state<string | null>(null);
	let autoScroll = $state(true);
	let container = $state<HTMLDivElement | undefined>();
	let pollTimer: ReturnType<typeof setInterval> | null = null;

	const sourceColors: Record<string, string> = {
		cc: 'var(--color-blue)',
		action: 'var(--color-peach)',
		tg: 'var(--color-green)',
		email: 'var(--color-yellow)',
		favilla: 'var(--color-pink)',
		schedule: 'var(--color-teal)'
	};

	function fmtTime(iso: string): string {
		try {
			const d = new Date(iso);
			return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
		} catch {
			return iso.slice(11, 19);
		}
	}

	function isThinking(beat: FlowPayload['beats'][number]): boolean {
		return beat.meta?.kind === 'thinking';
	}

	function thinkingTitle(text: string): string {
		const idx = text.indexOf('我想：');
		const body = idx >= 0 ? text.slice(0, idx + 3) : 'thinking';
		return body.length > 80 ? body.slice(0, 77) + '…' : body;
	}

	async function load() {
		try {
			const r = await api.flow(0, 200);
			beats = r.beats;
			total = r.total;
			loading = false;
			if (autoScroll && container) {
				requestAnimationFrame(() => {
					container!.scrollTop = container!.scrollHeight;
				});
			}
		} catch (e) {
			err = (e as Error).message;
			loading = false;
		}
	}

	onMount(async () => {
		await load();
		pollTimer = setInterval(load, 10000);
	});

	onDestroy(() => {
		if (pollTimer) clearInterval(pollTimer);
	});
</script>

<div class="flex flex-col h-[calc(100vh-8rem)]">
	<div class="flex items-center gap-4 mb-2 text-xs font-mono flex-wrap">
		<span class="text-[var(--color-subtext0)]">beats {total}</span>
		<span class="text-[var(--color-overlay0)]">flow.jsonl stream</span>
		<label class="ml-auto flex items-center gap-1 cursor-pointer">
			<input type="checkbox" bind:checked={autoScroll} class="accent-[var(--color-mauve)]" />
			<span class="text-[var(--color-overlay1)]">auto-scroll</span>
		</label>
		<button
			class="px-2 py-0.5 border border-[var(--color-surface1)] rounded text-[var(--color-subtext0)] hover:border-[var(--color-mauve)] cursor-pointer"
			onclick={load}
		>refresh</button>
	</div>

	{#if loading}
		<p class="text-[var(--color-overlay0)] text-sm">loading…</p>
	{:else if err}
		<p class="text-[var(--color-red)] font-mono text-xs">{err}</p>
	{:else}
		<div
			bind:this={container}
			class="flex-1 overflow-y-auto border border-[var(--color-surface0)] rounded bg-[var(--color-mantle)] p-3 font-mono text-sm space-y-1"
		>
			{#each beats as beat}
				<div class="flex gap-2 leading-relaxed">
					<span class="text-[var(--color-overlay0)] whitespace-nowrap shrink-0 text-xs mt-0.5">
						{fmtTime(beat.t)}
					</span>
					<span
						class="text-[10px] px-1.5 py-0.5 rounded whitespace-nowrap shrink-0 self-start"
						style="color:{sourceColors[beat.source] ?? 'var(--color-subtext0)'};
							   border:1px solid {sourceColors[beat.source] ?? 'var(--color-surface1)'}"
					>
						{beat.source}
					</span>
					{#if isThinking(beat)}
						<details class="min-w-0 flex-1 text-[var(--color-text)]">
							<summary class="cursor-pointer text-[var(--color-mauve)] break-all">
								{thinkingTitle(beat.text)}
							</summary>
							<div class="mt-1 whitespace-pre-wrap break-all text-[var(--color-subtext1)]">
								{beat.text}
							</div>
						</details>
					{:else}
						<span class="text-[var(--color-text)] break-all">
							{beat.text.length > 300 ? beat.text.slice(0, 297) + '…' : beat.text}
						</span>
					{/if}
				</div>
			{/each}
			{#if beats.length === 0}
				<p class="text-[var(--color-overlay0)] text-center py-8">no beats yet</p>
			{/if}
		</div>
	{/if}

	<!-- Source legend -->
	<div class="flex gap-3 mt-2 text-[10px] font-mono text-[var(--color-subtext0)]">
		{#each Object.entries(sourceColors) as [src, color]}
			<span class="flex items-center gap-1">
				<span class="inline-block w-2 h-2 rounded-full" style="background:{color}"></span>
				{src}
			</span>
		{/each}
	</div>
</div>
