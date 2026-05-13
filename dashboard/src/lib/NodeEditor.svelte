<script lang="ts">
	import { api, type PoolEventDetail } from '$lib/api';

	let { id, onclose, onsaved }: { id: string; onclose: () => void; onsaved: () => void } =
		$props();
	let data = $state<PoolEventDetail | null>(null);
	let err = $state<string | null>(null);
	let saving = $state(false);
	let editBody = $state('');

	$effect(() => {
		if (!id) return;
		data = null;
		err = null;
		api
			.poolEvent(id)
			.then((d) => {
				data = d;
				editBody = d.body;
			})
			.catch((e) => (err = (e as Error).message));
	});

	let confirmDelete = $state(false);

	async function save() {
		if (!data || saving) return;
		saving = true;
		err = null;
		try {
			await api.poolUpdateEvent(data.id, editBody);
			onsaved();
			onclose();
		} catch (e) {
			err = (e as Error).message;
		} finally {
			saving = false;
		}
	}

	async function deleteEvent() {
		if (!data || saving) return;
		saving = true;
		err = null;
		try {
			await api.poolDeleteEvent(data.id);
			onsaved();
			onclose();
		} catch (e) {
			err = (e as Error).message;
		} finally {
			saving = false;
			confirmDelete = false;
		}
	}

	function onKey(e: KeyboardEvent) {
		if (e.key === 'Escape') onclose();
		if ((e.ctrlKey || e.metaKey) && e.key === 's') {
			e.preventDefault();
			save();
		}
	}
</script>

<svelte:window onkeydown={onKey} />

<div class="fixed inset-0 z-50 flex items-center justify-center bg-[var(--color-crust)]/70 backdrop-blur-sm p-4">
	<button
		type="button"
		class="absolute inset-0 h-full w-full cursor-default"
		onclick={onclose}
		aria-label="close"
	></button>
	<div
		class="relative z-10 max-w-3xl w-full max-h-[85vh] overflow-auto rounded border border-[var(--color-surface1)] bg-[var(--color-mantle)] shadow-2xl p-5 font-mono text-sm"
		role="dialog"
		tabindex="-1"
	>
		<button
			onclick={onclose}
			class="absolute top-2 right-3 text-[var(--color-overlay1)] hover:text-[var(--color-red)] text-lg cursor-pointer"
			aria-label="close">×</button
		>

		<div class="text-[var(--color-mauve)] mb-3 break-all text-xs">{id}</div>

		{#if err}
			<p class="text-[var(--color-red)] text-xs mb-2">{err}</p>
		{/if}

		{#if !data}
			<p class="text-[var(--color-overlay0)]">loading…</p>
		{:else}
			<table class="text-xs mb-3 w-auto">
				<tbody>
					<tr>
						<td class="pr-4 text-[var(--color-subtext0)]">time</td>
						<td class="text-[var(--color-overlay1)]">{data.time}</td>
					</tr>
					<tr>
						<td class="pr-4 text-[var(--color-subtext0)]">access</td>
						<td class="text-[var(--color-overlay1)]">{data.access_count}</td>
					</tr>
					<tr>
						<td class="pr-4 text-[var(--color-subtext0)]">idx</td>
						<td class="text-[var(--color-overlay1)]">{data.fingerprint_idx}</td>
					</tr>
				</tbody>
			</table>

			<label for="node-body" class="block text-[var(--color-subtext0)] text-xs mb-1">body</label>
			<textarea
				id="node-body"
				bind:value={editBody}
				rows="12"
				class="w-full bg-[var(--color-base)] text-[var(--color-text)] border border-[var(--color-surface1)] rounded px-3 py-2 font-mono text-sm leading-relaxed resize-y focus:outline-none focus:border-[var(--color-mauve)]"
			></textarea>

			<div class="flex items-center gap-3 mt-3">
				<button
					onclick={save}
					disabled={saving}
					class="px-4 py-1.5 rounded bg-[var(--color-mauve)] text-[var(--color-crust)] text-xs font-medium hover:opacity-90 disabled:opacity-50 cursor-pointer"
				>
					{saving ? 'saving…' : 'save (Ctrl+S)'}
				</button>
				<button
					onclick={onclose}
					class="px-3 py-1.5 rounded border border-[var(--color-surface1)] text-[var(--color-subtext0)] text-xs hover:border-[var(--color-overlay0)] cursor-pointer"
				>
					cancel
				</button>
				<span class="text-[10px] text-[var(--color-overlay0)] ml-auto mr-2">
					re-embed on save
				</span>
				{#if !confirmDelete}
					<button
						onclick={() => (confirmDelete = true)}
						disabled={saving}
						class="px-3 py-1.5 rounded border border-[var(--color-red)]/40 text-[var(--color-red)] text-xs hover:border-[var(--color-red)] disabled:opacity-50 cursor-pointer"
					>
						del
					</button>
				{:else}
					<button
						onclick={deleteEvent}
						disabled={saving}
						class="px-3 py-1.5 rounded bg-[var(--color-red)] text-[var(--color-crust)] text-xs font-medium hover:opacity-90 disabled:opacity-50 cursor-pointer"
					>
						{saving ? 'deleting…' : 'confirm delete'}
					</button>
				{/if}
			</div>
		{/if}
	</div>
</div>
