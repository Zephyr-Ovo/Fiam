<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';

	let {
		x,
		y,
		source,
		target,
		kind,
		weight,
		mode,
		edgeTypes,
		onclose,
		onsaved
	}: {
		x: number;
		y: number;
		source: string;
		target: string;
		kind: string;
		weight: number;
		mode: 'edit' | 'create';
		edgeTypes: string[];
		onclose: () => void;
		onsaved: () => void;
	} = $props();

	let editKind = $state(kind);
	let editWeight = $state(weight);
	let saving = $state(false);
	let err = $state<string | null>(null);
	let menuEl = $state<HTMLDivElement>();

	// Close when clicking outside the menu (no backdrop — lets canvas events through)
	onMount(() => {
		function handleOutside(e: PointerEvent) {
			if (menuEl && !menuEl.contains(e.target as Node)) {
				onclose();
			}
		}
		// Delay to avoid the same click that opened the menu from closing it
		const frame = requestAnimationFrame(() => {
			window.addEventListener('pointerdown', handleOutside, true);
		});
		return () => {
			cancelAnimationFrame(frame);
			window.removeEventListener('pointerdown', handleOutside, true);
		};
	});

	async function save() {
		if (saving) return;
		saving = true;
		err = null;
		try {
			if (mode === 'create') {
				await api.poolCreateEdge(source, target, editKind, editWeight);
			} else {
				await api.poolUpdateEdge(source, target, editKind, editWeight);
			}
			onsaved();
			onclose();
		} catch (e) {
			err = (e as Error).message;
		} finally {
			saving = false;
		}
	}

	async function remove() {
		if (saving) return;
		saving = true;
		err = null;
		try {
			await api.poolDeleteEdge(source, target);
			onsaved();
			onclose();
		} catch (e) {
			err = (e as Error).message;
		} finally {
			saving = false;
		}
	}

	function onKey(e: KeyboardEvent) {
		if (e.key === 'Escape') onclose();
	}

	// Clamp position to viewport
	let left = $derived(Math.min(x, (typeof window !== 'undefined' ? window.innerWidth : 800) - 220));
	let top = $derived(Math.min(y, (typeof window !== 'undefined' ? window.innerHeight : 600) - 200));
</script>

<svelte:window onkeydown={onKey} />

<!-- Menu (no backdrop — click-outside handled by pointerdown listener) -->
<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<div
	bind:this={menuEl}
	class="fixed z-[51] w-52 rounded border border-[var(--color-surface1)] bg-[var(--color-mantle)] shadow-xl p-3 font-mono text-xs"
	style="left:{left}px;top:{top}px"
	onclick={(e) => e.stopPropagation()}
	onkeydown={(e) => e.stopPropagation()}
	role="dialog"
	tabindex="-1"
>
	<div class="text-[var(--color-subtext0)] mb-2 truncate">
		{mode === 'create' ? 'new edge' : 'edit edge'}
	</div>
	<div class="text-[10px] text-[var(--color-overlay0)] mb-2 truncate">
		{source.slice(-8)} → {target.slice(-8)}
	</div>

	{#if err}
		<p class="text-[var(--color-red)] text-[10px] mb-1">{err}</p>
	{/if}

	<label for="edge-kind" class="block text-[var(--color-overlay1)] mb-0.5">type</label>
	<select
		id="edge-kind"
		bind:value={editKind}
		class="w-full bg-[var(--color-base)] text-[var(--color-text)] border border-[var(--color-surface1)] rounded px-2 py-1 mb-2 text-xs focus:outline-none focus:border-[var(--color-mauve)]"
	>
		{#each edgeTypes as t}
			<option value={t}>{t}</option>
		{/each}
	</select>

	<label for="edge-weight" class="block text-[var(--color-overlay1)] mb-0.5">weight {editWeight.toFixed(2)}</label>
	<input
		id="edge-weight"
		type="range"
		min="0"
		max="1"
		step="0.05"
		bind:value={editWeight}
		class="w-full mb-2 accent-[var(--color-mauve)]"
	/>

	<div class="flex gap-2">
		<button
			onclick={save}
			disabled={saving}
			class="flex-1 px-2 py-1 rounded bg-[var(--color-mauve)] text-[var(--color-crust)] text-xs hover:opacity-90 disabled:opacity-50 cursor-pointer"
		>
			{mode === 'create' ? 'create' : 'save'}
		</button>
		{#if mode === 'edit'}
			<button
				onclick={remove}
				disabled={saving}
				class="px-2 py-1 rounded border border-[var(--color-red)] text-[var(--color-red)] text-xs hover:bg-[var(--color-red)] hover:text-[var(--color-crust)] disabled:opacity-50 cursor-pointer"
			>
				del
			</button>
		{/if}
	</div>
</div>
