<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { api, type GraphPayload } from '$lib/api';

	let canvas = $state<HTMLCanvasElement | undefined>();
	let err = $state<string | null>(null);
	let loading = $state(true);
	let theme = $state<'dark' | 'light'>('dark');
	let stats = $state({ nodes: 0, edges: 0, recalled: 0 });
	let hovered = $state<string | null>(null);
	let autoRotate = $state(true);
	let raf = 0;

	interface Node {
		id: string;
		label: string;
		intensity: number;
		last_accessed?: string;
		access_count?: number;
		x: number;
		y: number;
		z: number;
		vx: number;
		vy: number;
	}
	interface Edge {
		source: string;
		target: string;
		kind: string;
		weight: number;
	}

	let nodes: Node[] = [];
	let edges: Edge[] = [];
	let nodeById = new Map<string, Node>();

	let rotY = 0;
	let dragging = false;
	let dragMode: 'rotate' | 'pan' = 'rotate';
	let lastPx = 0;
	let lastPy = 0;
	let zoom = 1;
	let panX = 0;
	let panY = 0;

	function prettyLabel(id: string): string {
		const s = id.replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
		return s.length > 22 ? s.slice(0, 21) + '\u2026' : s;
	}

	// Sync theme to html[data-theme] for local toggles on this page.
	// (Layout restores from localStorage on first paint — this keeps parity when user toggles the graph's own button.)
	$effect(() => {
		if (typeof document === 'undefined') return;
		document.documentElement.setAttribute('data-theme', theme);
		try {
			localStorage.setItem('fiam-theme', theme);
		} catch {}
	});

	// Dark: catppuccin mocha. Light: Claude-palette from user css.
	const themes = {
		dark: {
			bg: '#11111b',
			bgGrid: '#181825',
			text: '#cdd6f4',
			edge: 'rgba(166,173,200,0.18)',
			nodeBase: '#89b4fa',
			nodeWarm: '#f9e2af',
			nodeHot: '#f38ba8',
			glow: '#f5c2e7',
			kinds: {
				semantic: '#89b4fa',
				temporal: '#94e2d5',
				causal: '#f38ba8',
				associative: '#cba6f7',
				reference: '#fab387',
				contrast: '#f9e2af'
			} as Record<string, string>
		},
		light: {
			bg: '#F8F8F3',
			bgGrid: '#F2F0EB',
			text: '#141413',
			edge: 'rgba(132,132,128,0.22)',
			nodeBase: '#849EB8',
			nodeWarm: '#b86548',
			nodeHot: '#96452A',
			glow: '#725E85',
			kinds: {
				semantic: '#849EB8',
				temporal: '#5A8060',
				causal: '#b86548',
				associative: '#725E85',
				reference: '#87604A',
				contrast: '#B594A0'
			} as Record<string, string>
		}
	};

	function hash01(s: string, salt = 0): number {
		let h = 2166136261 ^ salt;
		for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
		return ((h >>> 0) % 100000) / 100000;
	}

	function recallGlow(n: { last_accessed?: string; access_count?: number }): number {
		if (!n.last_accessed) return 0;
		const t = Date.parse(n.last_accessed);
		if (isNaN(t)) return 0;
		const hoursSince = (Date.now() - t) / 3_600_000;
		const base = Math.exp(-hoursSince / 12);
		const boost = Math.min(1, (n.access_count ?? 0) / 10);
		return Math.min(1, base + 0.2 * boost);
	}

	function step(dt: number) {
		if (!canvas) return;
		const W = canvas.width;
		const H = canvas.height;
		const cx = W / 2;
		const cy = H / 2;

		for (let i = 0; i < nodes.length; i++) {
			const a = nodes[i];
			for (let j = i + 1; j < nodes.length; j++) {
				const b = nodes[j];
				let dx = b.x - a.x;
				let dy = b.y - a.y;
				const d2 = dx * dx + dy * dy + 0.01;
				const rep = 2800 / d2;
				const d = Math.sqrt(d2);
				dx /= d;
				dy /= d;
				a.vx -= dx * rep * dt;
				a.vy -= dy * rep * dt;
				b.vx += dx * rep * dt;
				b.vy += dy * rep * dt;
			}
			// Soft brain-shaped containment: ellipse wider than tall, gentle inside, firm outside.
			const rx = W * 0.42;
			const ry = H * 0.34;
			const nx = (a.x - cx) / rx;
			const ny = (a.y - cy) / ry;
			const r2 = nx * nx + ny * ny;
			if (r2 > 1) {
				// outside the shell — pull back proportionally to how far out
				const pull = (r2 - 1) * 0.04;
				a.vx -= nx * rx * pull;
				a.vy -= ny * ry * pull;
			} else {
				// inside — very weak centering, so nodes spread to fill the shape
				a.vx += (cx - a.x) * 0.00008;
				a.vy += (cy - a.y) * 0.00008;
			}
		}

		for (const e of edges) {
			const s = nodeById.get(e.source);
			const t = nodeById.get(e.target);
			if (!s || !t) continue;
			const dx = t.x - s.x;
			const dy = t.y - s.y;
			const d = Math.sqrt(dx * dx + dy * dy) + 0.01;
			const target = 110;
			const f = ((d - target) * 0.015 * e.weight) / d;
			s.vx += dx * f;
			s.vy += dy * f;
			t.vx -= dx * f;
			t.vy -= dy * f;
		}

		for (const n of nodes) {
			n.vx *= 0.82;
			n.vy *= 0.82;
			n.x += n.vx;
			n.y += n.vy;
		}
	}

	function hexAlpha(color: string, a: number): string {
		if (color.startsWith('rgba')) return color;
		if (color.startsWith('#') && color.length === 7) {
			const r = parseInt(color.slice(1, 3), 16);
			const g = parseInt(color.slice(3, 5), 16);
			const b = parseInt(color.slice(5, 7), 16);
			return `rgba(${r},${g},${b},${a})`;
		}
		return color;
	}

	function mix(a: string, b: string, t: number): string {
		const pa = parseInt(a.slice(1), 16);
		const pb = parseInt(b.slice(1), 16);
		const ar = (pa >> 16) & 255;
		const ag = (pa >> 8) & 255;
		const ab = pa & 255;
		const br = (pb >> 16) & 255;
		const bg = (pb >> 8) & 255;
		const bb = pb & 255;
		const r = Math.round(ar + (br - ar) * t);
		const g = Math.round(ag + (bg - ag) * t);
		const bl = Math.round(ab + (bb - ab) * t);
		return `#${((r << 16) | (g << 8) | bl).toString(16).padStart(6, '0')}`;
	}

	function render() {
		if (!canvas) return;
		const ctx = canvas.getContext('2d')!;
		const W = canvas.width;
		const H = canvas.height;
		const t = themes[theme];
		const cx = W / 2;
		const cy = H / 2;

		ctx.fillStyle = t.bg;
		ctx.fillRect(0, 0, W, H);
		const g = ctx.createRadialGradient(cx, cy, 10, cx, cy, Math.max(W, H) / 1.4);
		g.addColorStop(0, t.bgGrid);
		g.addColorStop(1, t.bg);
		ctx.fillStyle = g;
		ctx.fillRect(0, 0, W, H);

		// Soft "brain" outline — matches the containment ellipse.
		{
			const rx = W * 0.42 * zoom;
			const ry = H * 0.34 * zoom;
			ctx.save();
			ctx.strokeStyle = hexAlpha(t.text, 0.07);
			ctx.lineWidth = 1;
			ctx.setLineDash([4, 6]);
			ctx.beginPath();
			ctx.ellipse(cx + panX, cy + panY, rx, ry, 0, 0, Math.PI * 2);
			ctx.stroke();
			ctx.restore();
		}

		if (autoRotate) rotY += 0.0035;
		const sinR = Math.sin(rotY);
		const cosR = Math.cos(rotY);

		// project pseudo-3D: Y-axis rotation of (x-cx, z) plane
		const projected = nodes.map((n) => {
			const localX = n.x - cx;
			const xr = localX * cosR - n.z * 140 * sinR;
			const zr = localX * sinR + n.z * 140 * cosR;
			const depth = zr / 140; // normalized roughly [-1, 1]
			const scale = zoom * (0.55 + 0.45 * ((depth + 1) / 2));
			const sx = cx + xr * zoom + panX;
			const sy = cy + (n.y - cy) * zoom + panY;
			return { n, sx, sy, scale, depth };
		});
		projected.sort((a, b) => a.depth - b.depth);
		const idx = new Map(projected.map((p, i) => [p.n.id, i]));

		ctx.lineWidth = 0.7;
		for (const e of edges) {
			const si = idx.get(e.source);
			const ti = idx.get(e.target);
			if (si === undefined || ti === undefined) continue;
			const s = projected[si];
			const tn = projected[ti];
			const depth = (s.depth + tn.depth) / 2;
			const alpha = (0.12 + 0.38 * ((depth + 1) / 2)) * (0.5 + 0.5 * e.weight);
			const col = t.kinds[e.kind] ?? t.edge;
			ctx.strokeStyle = hexAlpha(col, alpha);
			ctx.beginPath();
			ctx.moveTo(s.sx, s.sy);
			ctx.lineTo(tn.sx, tn.sy);
			ctx.stroke();
		}

		for (const p of projected) {
			const { n, sx, sy, scale, depth } = p;
			const radius = (3 + n.intensity * 6) * scale;
			const depthAlpha = 0.45 + 0.55 * ((depth + 1) / 2);
			const glow = recallGlow(n);

			if (glow > 0.05) {
				const haloR = radius + 10 + glow * 18;
				const halo = ctx.createRadialGradient(sx, sy, radius, sx, sy, haloR);
				halo.addColorStop(0, hexAlpha(t.glow, 0.55 * glow * depthAlpha));
				halo.addColorStop(1, hexAlpha(t.glow, 0));
				ctx.fillStyle = halo;
				ctx.beginPath();
				ctx.arc(sx, sy, haloR, 0, Math.PI * 2);
				ctx.fill();
			}

			let body: string;
			if (n.intensity > 0.7) body = t.nodeHot;
			else if (n.intensity > 0.4) body = t.nodeWarm;
			else body = t.nodeBase;
			if (glow > 0.3) body = mix(body, t.glow, glow * 0.6);

			ctx.fillStyle = hexAlpha(body, depthAlpha);
			ctx.beginPath();
			ctx.arc(sx, sy, radius, 0, Math.PI * 2);
			ctx.fill();

			if (hovered === n.id) {
				ctx.strokeStyle = t.text;
				ctx.lineWidth = 1.2;
				ctx.beginPath();
				ctx.arc(sx, sy, radius + 3, 0, Math.PI * 2);
				ctx.stroke();
			}
		}

		if (hovered) {
			const h = nodeById.get(hovered);
			const p = projected.find((q) => q.n.id === hovered);
			if (h && p) {
				ctx.font = '11px ui-monospace, monospace';
				const parts: string[] = [h.label];
				if (h.intensity > 0.01) parts.push(`i=${h.intensity.toFixed(2)}`);
				if (h.access_count && h.access_count > 0) parts.push(`recalls=${h.access_count}`);
				const text = parts.join('  ');
				const m = ctx.measureText(text);
				const tx = p.sx + 10;
				const ty = p.sy - 10;
				ctx.fillStyle = hexAlpha(t.bgGrid, 0.92);
				ctx.fillRect(tx - 4, ty - 11, m.width + 8, 16);
				ctx.fillStyle = t.text;
				ctx.fillText(text, tx, ty);
			}
		}
	}

	let lastT = 0;
	function loop(ts: number) {
		const dt = Math.min(0.05, (ts - lastT) / 16.67 || 1);
		lastT = ts;
		step(dt);
		render();
		raf = requestAnimationFrame(loop);
	}

	function onPointerDown(ev: PointerEvent) {
		dragging = true;
		dragMode = ev.shiftKey || ev.button === 1 || ev.buttons === 4 ? 'pan' : 'rotate';
		lastPx = ev.clientX;
		lastPy = ev.clientY;
		autoRotate = false;
		canvas!.setPointerCapture(ev.pointerId);
	}
	function onPointerUp(ev: PointerEvent) {
		dragging = false;
		try {
			canvas!.releasePointerCapture(ev.pointerId);
		} catch {}
	}
	function onPointerMove(ev: PointerEvent) {
		if (!canvas) return;
		if (dragging) {
			if (dragMode === 'pan') {
				panX += ev.clientX - lastPx;
				panY += ev.clientY - lastPy;
			} else {
				rotY += (ev.clientX - lastPx) * 0.01;
			}
			lastPx = ev.clientX;
			lastPy = ev.clientY;
			return;
		}
		const rect = canvas.getBoundingClientRect();
		const x = ev.clientX - rect.left;
		const y = ev.clientY - rect.top;
		let best: string | null = null;
		let bestD2 = 18 * 18;
		const cx = canvas.width / 2;
		const cy = canvas.height / 2;
		const sinR = Math.sin(rotY);
		const cosR = Math.cos(rotY);
		for (const n of nodes) {
			const xr = (n.x - cx) * cosR - n.z * 140 * sinR;
			const sx = cx + xr * zoom + panX;
			const sy = cy + (n.y - cy) * zoom + panY;
			const dx = sx - x;
			const dy = sy - y;
			const d2 = dx * dx + dy * dy;
			if (d2 < bestD2) {
				bestD2 = d2;
				best = n.id;
			}
		}
		hovered = best;
	}
	function onWheel(ev: WheelEvent) {
		ev.preventDefault();
		const rect = canvas!.getBoundingClientRect();
		const mx = ev.clientX - rect.left - canvas!.width / 2;
		const my = ev.clientY - rect.top - canvas!.height / 2;
		const factor = ev.deltaY < 0 ? 1.1 : 1 / 1.1;
		const newZoom = Math.max(0.2, Math.min(6, zoom * factor));
		// keep point under cursor stationary
		const k = newZoom / zoom - 1;
		panX -= (mx - panX) * k;
		panY -= (my - panY) * k;
		zoom = newZoom;
	}
	function resetView() {
		zoom = 1;
		panX = 0;
		panY = 0;
		rotY = 0;
	}

	function resize() {
		if (!canvas) return;
		const rect = canvas.getBoundingClientRect();
		canvas.width = rect.width;
		canvas.height = rect.height;
	}

	onMount(async () => {
		try {
			const saved = localStorage.getItem('fiam-theme');
			if (saved === 'light' || saved === 'dark') theme = saved;
		} catch {}
		try {
			const payload: GraphPayload = await api.graph();
			const W = canvas!.clientWidth || 800;
			const H = canvas!.clientHeight || 600;
			const cx = W / 2;
			const cy = H / 2;
			nodes = payload.nodes.map((n) => {
				const a = hash01(n.id, 1) * Math.PI * 2;
				const r = 60 + hash01(n.id, 2) * 140;
				return {
					id: n.id,
					label: prettyLabel(n.label ?? n.id),
					intensity: n.intensity ?? 0.5,
					last_accessed: n.last_accessed,
					access_count: n.access_count,
					x: cx + Math.cos(a) * r,
					y: cy + Math.sin(a) * r,
					z: hash01(n.id, 3) * 2 - 1,
					vx: 0,
					vy: 0
				};
			});
			edges = payload.edges;
			nodeById = new Map(nodes.map((n) => [n.id, n]));
			const recalled = nodes.filter((n) => recallGlow(n) > 0.2).length;
			stats = { nodes: nodes.length, edges: edges.length, recalled };
			resize();
			window.addEventListener('resize', resize);
			raf = requestAnimationFrame(loop);
			loading = false;
		} catch (e) {
			err = (e as Error).message;
			loading = false;
		}
	});

	onDestroy(() => {
		if (raf) cancelAnimationFrame(raf);
		window.removeEventListener('resize', resize);
	});
</script>

<div class="flex flex-col h-[calc(100vh-8rem)] relative">
	<div class="flex items-center gap-4 mb-2 text-xs font-mono flex-wrap">
		<span class="text-[var(--color-subtext0)]">nodes {stats.nodes}</span>
		<span class="text-[var(--color-subtext0)]">edges {stats.edges}</span>
		<span class="text-[var(--color-pink)]">recalled {stats.recalled}</span>
		<span class="text-[var(--color-overlay0)] hidden md:inline">
			drag = rotate · shift+drag = pan · wheel = zoom
		</span>
		<label class="ml-auto flex items-center gap-1 cursor-pointer">
			<input type="checkbox" bind:checked={autoRotate} class="accent-[var(--color-mauve)]" />
			<span class="text-[var(--color-overlay1)]">auto-rotate</span>
		</label>
		<button
			class="px-2 py-0.5 border border-[var(--color-surface1)] rounded text-[var(--color-subtext0)] hover:border-[var(--color-mauve)]"
			onclick={resetView}
		>reset</button>
		<button
			class="px-2 py-0.5 border border-[var(--color-surface1)] rounded text-[var(--color-subtext0)] hover:border-[var(--color-mauve)]"
			onclick={() => (theme = theme === 'dark' ? 'light' : 'dark')}
		>
			{theme === 'dark' ? '☾ dark' : '☀ light'}
		</button>
	</div>
	<canvas
		bind:this={canvas}
		class="flex-1 border border-[var(--color-surface0)] rounded cursor-grab active:cursor-grabbing touch-none"
		onpointerdown={onPointerDown}
		onpointerup={onPointerUp}
		onpointermove={onPointerMove}
		onwheel={onWheel}
	></canvas>
	<!-- edge-type legend -->
	<div
		class="absolute bottom-10 left-3 flex flex-col gap-1 px-2 py-1.5 rounded text-[10px] font-mono bg-[var(--color-mantle)]/80 border border-[var(--color-surface0)] backdrop-blur-sm pointer-events-none"
	>
		{#each Object.entries(themes[theme].kinds) as [k, c]}
			<div class="flex items-center gap-1.5">
				<span class="inline-block w-3 h-[2px]" style="background:{c}"></span>
				<span class="text-[var(--color-subtext0)]">{k}</span>
			</div>
		{/each}
	</div>
	{#if loading}
		<p class="absolute inset-0 flex items-center justify-center text-[var(--color-overlay0)]">
			loading graph…
		</p>
	{/if}
	{#if err}
		<p class="text-[var(--color-red)] font-mono text-xs mt-2">{err}</p>
	{/if}
</div>
