<script lang="ts">
	import { api, type EventDetail as Detail } from '$lib/api';

	let { id, onclose }: { id: string; onclose: () => void } = $props();
	let data = $state<Detail | null>(null);
	let err = $state<string | null>(null);

	$effect(() => {
		if (!id) return;
		data = null;
		err = null;
		api
			.event(id)
			.then((d) => (data = d))
			.catch((e) => (err = (e as Error).message));
	});

	function onKey(e: KeyboardEvent) {
		if (e.key === 'Escape') onclose();
	}
</script>

<svelte:window onkeydown={onKey} />

<div class="fixed inset-0 z-40 flex items-center justify-center bg-[var(--color-crust)]/70 backdrop-blur-sm p-4">
	<button
		type="button"
		class="absolute inset-0 h-full w-full cursor-default"
		onclick={onclose}
		aria-label="close"
	></button>
	<div
		class="relative z-10 max-w-3xl w-full max-h-[80vh] overflow-auto rounded border border-[var(--color-surface1)] bg-[var(--color-mantle)] shadow-2xl p-5 font-mono text-sm"
		role="dialog"
		tabindex="-1"
	>
		<button
			onclick={onclose}
			class="absolute top-2 right-3 text-[var(--color-overlay1)] hover:text-[var(--color-red)] text-lg"
			aria-label="close"
		>×</button>

		<div class="text-[var(--color-mauve)] mb-2 break-all">{id}</div>

		{#if err}
			<p class="text-[var(--color-red)]">{err}</p>
		{:else if !data}
			<p class="text-[var(--color-overlay0)]">loading…</p>
		{:else}
			{#if Object.keys(data.frontmatter).length}
				<table class="text-xs mb-3">
					<tbody>
						{#each Object.entries(data.frontmatter) as [k, v]}
							<tr>
								<td class="pr-3 text-[var(--color-subtext0)]">{k}</td>
								<td class="text-[var(--color-text)] break-all">{v}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{/if}
			<pre
				class="whitespace-pre-wrap text-[var(--color-text)] leading-relaxed">{data.body}</pre>
		{/if}
	</div>
</div>
