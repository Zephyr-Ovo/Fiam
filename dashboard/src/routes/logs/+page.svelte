<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { api } from '$lib/api';

	let lines = $state<string[]>([]);
	let err = $state<string | null>(null);
	let timer: ReturnType<typeof setInterval>;
	let pre = $state<HTMLPreElement | undefined>();
	let stick = $state(true);

	async function refresh() {
		try {
			const data = await api.pipeline();
			lines = data.lines;
			if (stick && pre) pre.scrollTop = pre.scrollHeight;
		} catch (e) {
			err = (e as Error).message;
		}
	}

	onMount(() => {
		refresh();
		timer = setInterval(refresh, 3000);
	});
	onDestroy(() => clearInterval(timer));

	function onScroll() {
		if (!pre) return;
		stick = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 20;
	}
</script>

<div class="flex flex-col h-[calc(100vh-8rem)]">
	<div class="flex items-center gap-3 mb-2 text-xs font-mono">
		<span class="text-[var(--color-subtext0)]">pipeline.log (last 200 lines)</span>
		<label class="flex items-center gap-1 text-[var(--color-overlay1)]">
			<input type="checkbox" bind:checked={stick} /> autoscroll
		</label>
	</div>

	{#if err}
		<p class="text-[var(--color-red)] text-xs font-mono mb-2">{err}</p>
	{/if}

	<pre
		bind:this={pre}
		onscroll={onScroll}
		class="flex-1 bg-[var(--color-crust)] border border-[var(--color-surface0)] p-3 text-xs overflow-auto whitespace-pre-wrap"
	>{lines.join('\n')}</pre>
</div>
