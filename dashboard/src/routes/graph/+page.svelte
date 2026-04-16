<script lang="ts">
	import { onMount } from 'svelte';
	import { api, type GraphPayload } from '$lib/api';

	let container = $state<HTMLDivElement | undefined>();
	let err = $state<string | null>(null);
	let loading = $state(true);
	let stats = $state({ nodes: 0, edges: 0 });

	const edgeColor: Record<string, string> = {
		semantic: '#89b4fa',
		temporal: '#94e2d5',
		causal: '#f38ba8',
		associative: '#cba6f7',
		reference: '#fab387',
		contrast: '#f9e2af'
	};

	onMount(async () => {
		try {
			const [{ default: cytoscape }, { default: cola }, payload] = await Promise.all([
				import('cytoscape'),
				import('cytoscape-cola'),
				api.graph()
			]);
			cytoscape.use(cola);

			stats = { nodes: payload.nodes.length, edges: payload.edges.length };

			const elements = [
				...payload.nodes.map((n) => ({
					data: {
						id: n.id,
						label: n.label,
						intensity: n.intensity ?? 0.5
					}
				})),
				...payload.edges.map((e) => ({
					data: {
						source: e.source,
						target: e.target,
						kind: e.kind,
						weight: e.weight ?? 0.5
					}
				}))
			];

			cytoscape({
				container,
				elements,
				style: [
					{
						selector: 'node',
						style: {
							'background-color': (ele: { data: (k: string) => number }) => {
								const i = ele.data('intensity') as number;
								if (i > 0.7) return '#f38ba8';
								if (i > 0.4) return '#fab387';
								return '#89b4fa';
							},
							label: 'data(label)',
							color: '#cdd6f4',
							'font-size': '10px',
							'text-valign': 'bottom',
							'text-margin-y': 4,
							'text-background-color': '#1e1e2e',
							'text-background-opacity': 0.7,
							'text-background-padding': '2px',
							width: (ele: { data: (k: string) => number }) =>
								8 + (ele.data('intensity') as number) * 20,
							height: (ele: { data: (k: string) => number }) =>
								8 + (ele.data('intensity') as number) * 20,
							'border-width': 1,
							'border-color': '#45475a'
						}
					},
					{
						selector: 'edge',
						style: {
							'line-color': (ele: { data: (k: string) => string }) =>
								edgeColor[ele.data('kind') as string] ?? '#6c7086',
							'target-arrow-color': (ele: { data: (k: string) => string }) =>
								edgeColor[ele.data('kind') as string] ?? '#6c7086',
							'target-arrow-shape': 'triangle',
							'curve-style': 'bezier',
							width: (ele: { data: (k: string) => number }) =>
								1 + (ele.data('weight') as number) * 2,
							opacity: 0.7
						}
					},
					{
						selector: 'node:selected',
						style: {
							'border-width': 3,
							'border-color': '#f5c2e7'
						}
					}
				],
				layout: {
					name: 'cola',
					animate: true,
					refresh: 1,
					maxSimulationTime: 3000,
					nodeSpacing: 12,
					edgeLength: 80,
					randomize: false,
					fit: true
				} as unknown as cytoscape.LayoutOptions,
				wheelSensitivity: 0.2
			});

			loading = false;
		} catch (e) {
			err = (e as Error).message;
			loading = false;
		}
	});
</script>

<div class="flex flex-col h-[calc(100vh-8rem)]">
	<div class="flex items-center gap-4 mb-2 text-xs font-mono">
		<span class="text-[var(--color-subtext0)]">nodes {stats.nodes}</span>
		<span class="text-[var(--color-subtext0)]">edges {stats.edges}</span>
		<span class="ml-auto flex gap-3">
			{#each Object.entries(edgeColor) as [kind, color]}
				<span class="flex items-center gap-1">
					<span class="w-3 h-[2px]" style="background: {color}"></span>
					<span class="text-[var(--color-overlay1)]">{kind}</span>
				</span>
			{/each}
		</span>
	</div>
	<div
		bind:this={container}
		class="flex-1 bg-[var(--color-mantle)] border border-[var(--color-surface0)]"
	></div>
	{#if loading}
		<p class="absolute inset-0 flex items-center justify-center text-[var(--color-overlay0)]">
			loading graph…
		</p>
	{/if}
	{#if err}
		<p class="text-[var(--color-red)] font-mono text-xs mt-2">{err}</p>
	{/if}
</div>
