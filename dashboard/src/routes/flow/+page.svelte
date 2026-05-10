<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { api, type FlowPayload } from '$lib/api';
	type Beat = FlowPayload['beats'][number];

	let beats = $state<FlowPayload['beats']>([]);
	let total = $state(0);
	let loading = $state(true);
	let err = $state<string | null>(null);
	let autoScroll = $state(true);
	let container = $state<HTMLDivElement | undefined>();
	let pollTimer: ReturnType<typeof setInterval> | null = null;

	// Color by actor (left of @) — secondary tint by channel (right of @).
	const actorColors: Record<string, string> = {
		user: 'var(--color-pink)',
		ai: 'var(--color-blue)',
		external: 'var(--color-yellow)',
		system: 'var(--color-lavender)'
	};
	const channelColors: Record<string, string> = {
		think: 'var(--color-mauve)',
		action: 'var(--color-peach)',
		favilla: 'var(--color-pink)',
		stroll: 'var(--color-sapphire)',
		email: 'var(--color-yellow)',
		schedule: 'var(--color-lavender)',
		studio: 'var(--color-maroon)',
		console: 'var(--color-mauve)' // legacy old rows
	};
	const runtimeColors: Record<string, string> = {
		cc: 'var(--color-blue)',
		api: 'var(--color-green)',
		claude: 'var(--color-blue)',
		gemini: 'var(--color-teal)'
	};

	function getActor(beat: Beat): string {
		return (beat.actor ?? '').toString();
	}
	function getChannel(beat: Beat): string {
		return (beat.channel ?? '').toString();
	}
	function colorForScene(beat: Beat): string {
		const ch = getChannel(beat);
		if (ch && channelColors[ch]) return channelColors[ch];
		const actor = getActor(beat);
		if (actor && actorColors[actor]) return actorColors[actor];
		return 'var(--color-subtext0)';
	}
	function colorForRuntime(rt: string): string {
		return runtimeColors[rt] ?? 'var(--color-overlay1)';
	}

	function sceneOf(beat: Beat): string {
		const a = getActor(beat);
		const c = getChannel(beat);
		if (a && c) return `${a}@${c}`;
		return a || c;
	}

	function isThinking(beat: Beat): boolean {
		return sceneOf(beat) === 'ai@think' || (beat.meta as any)?.kind === 'thinking';
	}

	function thinkingTitle(text: string): string {
		const idx = text.indexOf('我想：');
		const body = idx >= 0 ? text.slice(0, idx + 3) : 'thinking';
		return body.length > 80 ? body.slice(0, 77) + '…' : body;
	}

	const TIME_FMT = new Intl.DateTimeFormat('zh-CN', {
		timeZone: 'Asia/Shanghai',
		hour: '2-digit',
		minute: '2-digit',
		hour12: false
	});
	function fmtTime(iso: string): string {
		try {
			return TIME_FMT.format(new Date(iso));
		} catch {
			return iso.slice(11, 16);
		}
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

	function legendItems(): { label: string; color: string }[] {
		const seen = new Map<string, string>();
		for (const b of beats) {
			const s = sceneOf(b);
			if (s) seen.set(s, colorForScene(b));
			const rt = b.runtime;
			if (rt) seen.set(`runtime:${rt}`, colorForRuntime(rt));
		}
		return [...seen].map(([label, color]) => ({ label, color }));
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
				{@const sceneStr = sceneOf(beat)}
				{@const sceneColor = colorForScene(beat)}
				<div class="leading-relaxed border border-[var(--color-surface0)] rounded p-2 bg-[var(--color-base)]/30">
					<div class="flex items-start gap-2 flex-wrap text-[10px]">
						<span class="text-[var(--color-overlay0)] whitespace-nowrap shrink-0 text-xs mt-0.5">
							{fmtTime(beat.t)}
						</span>
						<span
							class="px-1.5 py-0.5 rounded border break-all"
							style="color:{sceneColor}; border-color:{sceneColor}"
						>{sceneStr || '?'}</span>
						{#if beat.runtime}
							<span
								class="px-1.5 py-0.5 rounded border break-all"
								style="color:{colorForRuntime(beat.runtime)}; border-color:{colorForRuntime(beat.runtime)}"
							>{beat.runtime}</span>
						{/if}
						<span class="px-1.5 py-0.5 rounded border border-[var(--color-surface1)] text-[var(--color-overlay1)]">ai={beat.ai}</span>
					</div>
					{#if isThinking(beat)}
						<details class="mt-2 min-w-0 text-[var(--color-text)]">
							<summary class="cursor-pointer text-[var(--color-mauve)] break-all">
								{thinkingTitle(beat.text)}
							</summary>
							<div class="mt-1 whitespace-pre-wrap break-all text-[var(--color-subtext1)]">
								{beat.text}
							</div>
						</details>
					{:else}
						<div class="mt-2 text-[var(--color-text)] whitespace-pre-wrap break-words min-w-0">
							{beat.text}
						</div>
					{/if}
				</div>
			{/each}
			{#if beats.length === 0}
				<p class="text-[var(--color-overlay0)] text-center py-8">no beats yet</p>
			{/if}
		</div>
	{/if}

	<!-- Scene legend -->
	<div class="flex gap-3 mt-2 text-[10px] font-mono text-[var(--color-subtext0)] flex-wrap">
		{#each legendItems() as item}
			<span class="flex items-center gap-1">
				<span class="inline-block w-2 h-2 rounded-full" style="background:{item.color}"></span>
				{item.label}
			</span>
		{/each}
	</div>
</div>
