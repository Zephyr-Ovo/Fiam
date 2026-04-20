<script lang="ts">
	import { onMount } from 'svelte';
	import {
		api,
		type AnnotateProposal,
		type AnnotateEdge,
		type FlowPayload
	} from '$lib/api';

	// --- State ---
	let phase = $state<'idle' | 'loading' | 'cuts' | 'edges_loading' | 'edges' | 'confirming' | 'done'>('idle');
	let err = $state<string | null>(null);
	let beats = $state<FlowPayload['beats']>([]);
	let cuts = $state<number[]>([]);
	let edges = $state<AnnotateEdge[]>([]);
	let result = $state<{ events_created: string[]; edges_created: number } | null>(null);

	const sourceColors: Record<string, string> = {
		cc: 'var(--color-blue)',
		action: 'var(--color-peach)',
		tg: 'var(--color-green)',
		email: 'var(--color-yellow)',
		favilla: 'var(--color-pink)',
		schedule: 'var(--color-teal)'
	};

	const edgeTypeColors: Record<string, string> = {
		temporal: 'var(--color-blue)',
		semantic: 'var(--color-green)',
		cause: 'var(--color-red)',
		remind: 'var(--color-yellow)',
		contrast: 'var(--color-peach)',
		elaboration: 'var(--color-teal)'
	};

	function fmtTime(iso: string): string {
		try {
			const d = new Date(iso);
			return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
		} catch {
			return iso?.slice(11, 16) ?? '??:??';
		}
	}

	/** How many segments the current cuts produce */
	function segmentCount(): number {
		return cuts.filter((c) => c === 1).length + 1;
	}

	// --- Actions ---
	async function requestAnnotation() {
		phase = 'loading';
		err = null;
		try {
			const p = await api.annotateRequest(undefined, 200);
			if (p.beats) beats = p.beats as any;
			if (p.cuts) cuts = p.cuts;
			phase = 'cuts';
		} catch (e) {
			err = (e as Error).message;
			phase = 'idle';
		}
	}

	async function requestEdges() {
		phase = 'edges_loading';
		err = null;
		try {
			const p = await api.annotateEdges(cuts);
			if (p.edges) edges = p.edges;
			phase = 'edges';
		} catch (e) {
			err = (e as Error).message;
			phase = 'cuts';
		}
	}

	async function confirm() {
		phase = 'confirming';
		err = null;
		try {
			const r = await api.annotateConfirm(cuts, edges);
			result = { events_created: r.events_created, edges_created: r.edges_created };
			phase = 'done';
		} catch (e) {
			err = (e as Error).message;
			phase = 'edges';
		}
	}

	function toggleCut(i: number) {
		cuts[i] = cuts[i] === 1 ? 0 : 1;
		cuts = [...cuts]; // trigger reactivity
	}

	function removeEdge(i: number) {
		edges = edges.filter((_, idx) => idx !== i);
	}

	function updateEdgeWeight(i: number, w: number) {
		edges[i] = { ...edges[i], weight: Math.round(w * 100) / 100 };
		edges = [...edges];
	}

	onMount(async () => {
		try {
			const p = await api.annotateProposal();
			if (p.status === 'cuts_proposed' || p.status === 'edges_proposed') {
				if (p.beats) beats = p.beats as any;
				if (p.cuts) cuts = p.cuts;
				if (p.edges) edges = p.edges;
				phase = p.status === 'edges_proposed' ? 'edges' : 'cuts';
			}
		} catch {
			// no pending proposal
		}
	});
</script>

<div class="flex flex-col gap-4 max-w-5xl mx-auto">
	<!-- Header -->
	<div class="flex items-center justify-between">
		<h1 class="text-lg font-mono text-[var(--color-text)]">annotate</h1>
		<div class="flex items-center gap-2 text-xs font-mono text-[var(--color-subtext0)]">
			{#if phase === 'cuts'}
				<span class="text-[var(--color-green)]">● phase 1: cuts</span>
			{:else if phase === 'edges' || phase === 'edges_loading'}
				<span class="text-[var(--color-blue)]">● phase 2: edges</span>
			{:else if phase === 'done'}
				<span class="text-[var(--color-green)]">✓ done</span>
			{:else}
				<span>idle</span>
			{/if}
		</div>
	</div>

	{#if err}
		<div class="bg-[var(--color-surface0)] border border-[var(--color-red)] rounded p-3 text-sm font-mono text-[var(--color-red)]">
			{err}
		</div>
	{/if}

	<!-- Idle -->
	{#if phase === 'idle'}
		<div class="flex flex-col items-center gap-4 py-12 text-center">
			<p class="text-[var(--color-subtext0)] text-sm font-mono">
				send recent flow beats to DS for cut annotation
			</p>
			<button
				class="px-6 py-2 bg-[var(--color-mauve)] text-[var(--color-base)] rounded font-mono text-sm hover:opacity-90 cursor-pointer"
				onclick={requestAnnotation}
			>
				annotate flow
			</button>
		</div>
	{/if}

	<!-- Loading -->
	{#if phase === 'loading' || phase === 'edges_loading' || phase === 'confirming'}
		<div class="flex items-center gap-3 py-12 justify-center">
			<div class="w-4 h-4 border-2 border-[var(--color-mauve)] border-t-transparent rounded-full animate-spin"></div>
			<span class="text-sm font-mono text-[var(--color-subtext0)]">
				{phase === 'loading' ? 'DS is reading beats...' : phase === 'edges_loading' ? 'DS is proposing edges...' : 'saving...'}
			</span>
		</div>
	{/if}

	<!-- Phase 1: Binary cuts review -->
	{#if phase === 'cuts'}
		<div class="text-xs font-mono text-[var(--color-subtext0)] flex items-center gap-3">
			<span>{beats.length} beats → {segmentCount()} segments</span>
			<span class="text-[var(--color-overlay0)]">click ✂ between beats to toggle cuts</span>
		</div>

		<div class="border border-[var(--color-surface0)] rounded bg-[var(--color-mantle)] overflow-y-auto max-h-[65vh]">
			{#each beats as beat, i}
				<!-- Beat row -->
				<div
					class="flex gap-2 px-3 py-1 text-sm font-mono leading-relaxed hover:bg-[var(--color-surface0)] transition-colors"
				>
					<span class="text-[var(--color-overlay0)] text-xs whitespace-nowrap shrink-0 mt-0.5 w-10">
						{fmtTime(beat.t)}
					</span>
					<span
						class="text-[10px] px-1 py-0.5 rounded whitespace-nowrap shrink-0 self-start"
						style="color: {sourceColors[beat.source] ?? 'var(--color-subtext0)'}; border: 1px solid {sourceColors[beat.source] ?? 'var(--color-surface1)'}"
					>
						{beat.source}
					</span>
					<span class="text-[var(--color-text)] break-all flex-1">
						{beat.text.length > 400 ? beat.text.slice(0, 397) + '…' : beat.text}
					</span>
					<span class="text-[var(--color-overlay0)] text-[10px] shrink-0 self-start w-4 text-right">
						{i}
					</span>
				</div>

				<!-- Cut toggle (between beat i and i+1) -->
				{#if i < beats.length - 1}
					<button
						class="w-full h-4 relative group cursor-pointer flex items-center justify-center"
						onclick={() => toggleCut(i)}
						title={cuts[i] === 1 ? 'remove cut' : 'add cut here'}
					>
						{#if cuts[i] === 1}
							<div class="absolute inset-x-3 top-1/2 border-t-2 border-dashed border-[var(--color-red)] opacity-80"></div>
							<span class="relative z-10 text-[10px] font-mono text-[var(--color-red)] bg-[var(--color-mantle)] px-1">✂ cut</span>
						{:else}
							<div class="absolute inset-x-3 top-1/2 border-t border-transparent group-hover:border-[var(--color-overlay0)] group-hover:border-dashed transition-colors"></div>
							<span class="relative z-10 text-[10px] font-mono text-transparent group-hover:text-[var(--color-overlay0)] transition-colors">✂</span>
						{/if}
					</button>
				{/if}
			{/each}
		</div>

		<div class="flex gap-3 justify-end">
			<button
				class="px-4 py-1.5 border border-[var(--color-surface1)] rounded text-[var(--color-subtext0)] text-sm font-mono hover:border-[var(--color-mauve)] cursor-pointer"
				onclick={requestAnnotation}
			>re-run DS</button>
			<button
				class="px-4 py-1.5 bg-[var(--color-blue)] text-[var(--color-base)] rounded text-sm font-mono hover:opacity-90 cursor-pointer"
				onclick={requestEdges}
			>confirm cuts → propose edges</button>
		</div>
	{/if}

	<!-- Phase 2: Edge review -->
	{#if phase === 'edges'}
		<div class="text-xs font-mono text-[var(--color-subtext0)]">
			{edges.length} edges proposed between {segmentCount()} new + existing events
		</div>

		<div class="border border-[var(--color-surface0)] rounded bg-[var(--color-mantle)] overflow-y-auto max-h-[50vh] divide-y divide-[var(--color-surface0)]">
			{#each edges as edge, i}
				<div class="flex items-center gap-3 px-3 py-2 text-sm font-mono">
					<span class="text-[var(--color-text)] shrink-0">{edge.src}</span>
					<span
						class="text-[10px] px-1.5 py-0.5 rounded shrink-0"
						style="color: {edgeTypeColors[edge.type] ?? 'var(--color-subtext0)'}; border: 1px solid {edgeTypeColors[edge.type] ?? 'var(--color-surface1)'}"
					>
						{edge.type}
					</span>
					<span class="text-[var(--color-text)] shrink-0">{edge.dst}</span>

					<input
						type="range"
						min="0"
						max="1"
						step="0.05"
						value={edge.weight}
						oninput={(e) => updateEdgeWeight(i, parseFloat((e.target as HTMLInputElement).value))}
						class="flex-1 accent-[var(--color-mauve)] min-w-16"
					/>
					<span class="text-[var(--color-overlay0)] w-8 text-right shrink-0">{edge.weight}</span>

					<button
						class="text-[var(--color-red)] hover:text-[var(--color-text)] text-xs cursor-pointer shrink-0"
						onclick={() => removeEdge(i)}
						title="remove edge"
					>✕</button>
				</div>
				{#if edge.reason}
					<div class="px-3 pb-2 text-[11px] text-[var(--color-overlay0)] font-mono -mt-1">
						{edge.reason}
					</div>
				{/if}
			{/each}
			{#if edges.length === 0}
				<p class="text-center py-6 text-[var(--color-overlay0)] text-sm font-mono">no edges proposed</p>
			{/if}
		</div>

		<div class="flex gap-3 justify-end">
			<button
				class="px-4 py-1.5 border border-[var(--color-surface1)] rounded text-[var(--color-subtext0)] text-sm font-mono hover:border-[var(--color-mauve)] cursor-pointer"
				onclick={() => { phase = 'cuts'; }}
			>← back to cuts</button>
			<button
				class="px-4 py-1.5 bg-[var(--color-green)] text-[var(--color-base)] rounded text-sm font-mono hover:opacity-90 cursor-pointer"
				onclick={confirm}
			>confirm all → save</button>
		</div>
	{/if}

	<!-- Done -->
	{#if phase === 'done' && result}
		<div class="bg-[var(--color-surface0)] border border-[var(--color-green)] rounded p-4 text-sm font-mono space-y-2">
			<p class="text-[var(--color-green)] font-medium">✓ annotation saved</p>
			<p class="text-[var(--color-text)]">
				events: {result.events_created.join(', ')}
			</p>
			<p class="text-[var(--color-subtext0)]">
				{result.edges_created} edges created
			</p>
		</div>
		<button
			class="px-4 py-1.5 bg-[var(--color-mauve)] text-[var(--color-base)] rounded text-sm font-mono hover:opacity-90 cursor-pointer self-start"
			onclick={() => { phase = 'idle'; result = null; beats = []; cuts = []; edges = []; }}
		>annotate more</button>
	{/if}
</div>
