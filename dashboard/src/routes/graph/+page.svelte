<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { api, type GraphPayload } from '$lib/api';
	import NodeEditor from '$lib/NodeEditor.svelte';
	import EdgeMenu from '$lib/EdgeMenu.svelte';

	let canvas = $state<HTMLCanvasElement | undefined>();
	let err = $state<string | null>(null);
	let loading = $state(true);
	let theme = $state<'dark' | 'light'>('dark');
	let stats = $state({ nodes: 0, edges: 0, recalled: 0 });
	let hovered = $state<string | null>(null);
	let hoveredEdge = $state<Edge | null>(null);
	let autoRotate = $state(true);
	let raf = 0;

	// Edge types (loaded from server)
	let edgeTypes = $state<string[]>(['semantic', 'temporal', 'causal', 'remind', 'elaboration', 'contrast']);

	// Ctrl+click edge creation state
	let ctrlPickFirst = $state<string | null>(null);

	// Edge menu state
	let edgeMenu = $state<{
		x: number; y: number;
		source: string; target: string;
		kind: string; weight: number;
		mode: 'edit' | 'create';
	} | null>(null);

	// Node editor state
	let editNodeId = $state<string | null>(null);

	// Undo stack
	interface UndoAction {
		type: 'update_event' | 'create_edge' | 'update_edge' | 'delete_edge';
		data: Record<string, unknown>;
	}
	let undoStack: UndoAction[] = [];

	interface Edge {
		source: string;
		target: string;
		kind: string;
		weight: number;
	}

	interface DisplayNode {
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
		pinned?: boolean;
	}

	let nodes: DisplayNode[] = [];
	let edges: Edge[] = [];
	let nodeById = new Map<string, DisplayNode>();

	let rotY = 0;
	let rotX = 0.35;
	let dragging = false;
	let dragMode: 'rotate' | 'pan' | 'node' = 'rotate';
	let draggedNode: DisplayNode | null = null;
	let lastPx = 0;
	let lastPy = 0;
	let zoom = 1;
	let panX = 0;
	let panY = 0;

	// Recall glow animation tracking — "快亮慢散"
	const glowTargets = new Map<string, number>();
	const glowCurrent = new Map<string, number>();

	function prettyLabel(id: string): string {
		return id.replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
	}

	$effect(() => {
		if (typeof document === 'undefined') return;
		document.documentElement.setAttribute('data-theme', theme);
		try {
			localStorage.setItem('fiam-theme', theme);
		} catch {}
	});

	const themes = {
		dark: {
			bg: '#0a0a18',
			bgGrid: '#10101e',
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
				remind: '#fab387',
				elaboration: '#a6e3a1',
				contrast: '#f9e2af',
				reference: '#fab387'
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
				remind: '#87604A',
				elaboration: '#5A8060',
				contrast: '#B594A0',
				reference: '#87604A'
			} as Record<string, string>
		}
	};

	function hash01(s: string, salt = 0): number {
		let h = 2166136261 ^ salt;
		for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
		return ((h >>> 0) % 100000) / 100000;
	}

	function recallGlow(n: { id: string; last_accessed?: string; access_count?: number }): number {
		// Compute target glow from data
		let target = 0;
		if (n.last_accessed) {
			const t = Date.parse(n.last_accessed);
			if (!isNaN(t)) {
				const hoursSince = (Date.now() - t) / 3_600_000;
				const base = Math.exp(-hoursSince / 12);
				const boost = Math.min(1, (n.access_count ?? 0) / 10);
				target = Math.min(1, base + 0.2 * boost);
			}
		}
		glowTargets.set(n.id, target);
		const cur = glowCurrent.get(n.id) ?? 0;
		// Fast rise (快亮), slow decay (慢散)
		let next: number;
		if (target > cur) {
			next = cur + (target - cur) * 0.3;  // fast rise
		} else {
			next = cur + (target - cur) * 0.02; // slow fade
		}
		if (Math.abs(next) < 0.001) next = 0;
		glowCurrent.set(n.id, next);
		return next;
	}

	function step(dt: number) {
		if (!canvas) return;
		const W = canvas.width;
		const H = canvas.height;
		const cx = W / 2;
		const cy = H / 2;

		for (let i = 0; i < nodes.length; i++) {
			const a = nodes[i];
			if (a.pinned) { a.vx = 0; a.vy = 0; }
			for (let j = i + 1; j < nodes.length; j++) {
				const b = nodes[j];
				let dx = b.x - a.x;
				let dy = b.y - a.y;
				const d2 = Math.max(25, dx * dx + dy * dy);
				const rep = Math.min(120, 2800 / d2);
				const d = Math.sqrt(d2);
				dx /= d;
				dy /= d;
				if (!a.pinned) { a.vx -= dx * rep * dt; a.vy -= dy * rep * dt; }
				if (!b.pinned) { b.vx += dx * rep * dt; b.vy += dy * rep * dt; }
			}
			const rx = W * 0.42;
			const ry = H * 0.34;
			const nx = (a.x - cx) / rx;
			const ny = (a.y - cy) / ry;
			const r2 = nx * nx + ny * ny;
			if (r2 > 1) {
				const pull = (r2 - 1) * 0.04;
				a.vx -= nx * rx * pull;
				a.vy -= ny * ry * pull;
			} else {
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
			if (!s.pinned) { s.vx += dx * f; s.vy += dy * f; }
			if (!t.pinned) { t.vx -= dx * f; t.vy -= dy * f; }
		}

		for (const n of nodes) {
			if (n.pinned) continue;
			const v2 = n.vx * n.vx + n.vy * n.vy;
			if (v2 > 400) {
				const s = 20 / Math.sqrt(v2);
				n.vx *= s;
				n.vy *= s;
			}
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

		// Soft "brain" outline
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
		const sinY = Math.sin(rotY);
		const cosY = Math.cos(rotY);
		const sinX = Math.sin(rotX);
		const cosX = Math.cos(rotX);

		const projected = nodes.map((n) => {
			const lx = n.x - cx;
			const ly = n.y - cy;
			const lz = n.z * 140;
			const x1 = lx * cosY - lz * sinY;
			const z1 = lx * sinY + lz * cosY;
			const y2 = ly * cosX - z1 * sinX;
			const z2 = ly * sinX + z1 * cosX;
			const depth = z2 / 200;
			const scale = zoom * (0.55 + 0.45 * ((depth + 1) / 2));
			const sx = cx + x1 * zoom + panX;
			const sy = cy + y2 * zoom + panY;
			return { n, sx, sy, scale, depth };
		});
		projected.sort((a, b) => a.depth - b.depth);
		const idx = new Map(projected.map((p, i) => [p.n.id, i]));

		// Draw edges
		for (const e of edges) {
			const si = idx.get(e.source);
			const ti = idx.get(e.target);
			if (si === undefined || ti === undefined) continue;
			const s = projected[si];
			const tn = projected[ti];
			const depth = (s.depth + tn.depth) / 2;
			const isHov = hoveredEdge && hoveredEdge.source === e.source && hoveredEdge.target === e.target;
			const alpha = isHov ? 0.9 : (0.12 + 0.38 * ((depth + 1) / 2)) * (0.5 + 0.5 * e.weight);
			const col = t.kinds[e.kind] ?? t.edge;
			ctx.lineWidth = isHov ? 2.5 : 0.7;
			ctx.strokeStyle = hexAlpha(col, alpha);
			ctx.beginPath();
			ctx.moveTo(s.sx, s.sy);
			ctx.lineTo(tn.sx, tn.sy);
			ctx.stroke();
		}

		// Draw nodes
		for (const p of projected) {
			const { n, sx, sy, scale, depth } = p;
			const radius = (3 + n.intensity * 6) * scale;
			const depthAlpha = 0.45 + 0.55 * ((depth + 1) / 2);
			const glow = recallGlow(n);

			// Recall halo — "快亮慢散"
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

			// Ctrl+click highlight
			if (ctrlPickFirst === n.id) {
				ctx.strokeStyle = hexAlpha(t.glow, 0.8);
				ctx.lineWidth = 2;
				ctx.setLineDash([3, 3]);
				ctx.beginPath();
				ctx.arc(sx, sy, radius + 6, 0, Math.PI * 2);
				ctx.stroke();
				ctx.setLineDash([]);
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

			// Enlarged node label — always show
			if (depthAlpha > 0.55 && scale > 0.5) {
				const fontSize = Math.max(9, Math.min(13, 11 * scale));
				ctx.font = `${fontSize}px var(--font-sans, sans-serif)`;
				const labelText = n.label.length > 20 ? n.label.slice(0, 18) + '…' : n.label;
				const m = ctx.measureText(labelText);
				const lx = sx - m.width / 2;
				const ly = sy + radius + fontSize + 2;
				ctx.fillStyle = hexAlpha(t.text, depthAlpha * 0.85);
				ctx.fillText(labelText, lx, ly);
			}
		}

		// Hovered node tooltip — show full label + access count
		if (hovered) {
			const h = nodeById.get(hovered);
			const p = projected.find((q) => q.n.id === hovered);
			if (h && p) {
				ctx.font = '12px var(--font-mono, ui-monospace), monospace';
				const parts: string[] = [h.label];
				if (h.access_count && h.access_count > 0) parts.push(`· ${h.access_count}★`);
				const text = parts.join(' ');
				const m = ctx.measureText(text);
				const tx = p.sx + 10;
				const ty = p.sy - 10;
				ctx.fillStyle = hexAlpha(t.bgGrid, 0.92);
				ctx.fillRect(tx - 4, ty - 13, m.width + 8, 18);
				ctx.fillStyle = t.text;
				ctx.fillText(text, tx, ty);
			}
		}

		// Hovered edge tooltip — show kind + weight
		if (hoveredEdge && !hovered) {
			const sn = nodeById.get(hoveredEdge.source);
			const tn2 = nodeById.get(hoveredEdge.target);
			const sp = sn ? projected.find((q) => q.n.id === sn.id) : null;
			const tp = tn2 ? projected.find((q) => q.n.id === tn2.id) : null;
			if (sp && tp) {
				const mx2 = (sp.sx + tp.sx) / 2;
				const my2 = (sp.sy + tp.sy) / 2;
				ctx.font = '11px var(--font-mono, ui-monospace), monospace';
				const text2 = `${hoveredEdge.kind}  w=${hoveredEdge.weight.toFixed(2)}`;
				const m2 = ctx.measureText(text2);
				ctx.fillStyle = hexAlpha(t.bgGrid, 0.92);
				ctx.fillRect(mx2 - 4, my2 - 13, m2.width + 8, 18);
				const col2 = t.kinds[hoveredEdge.kind] ?? t.edge;
				ctx.fillStyle = col2;
				ctx.fillText(text2, mx2, my2);
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

	function projectHit(cx: number, cy: number) {
		const sinY = Math.sin(rotY);
		const cosY = Math.cos(rotY);
		const sinX = Math.sin(rotX);
		const cosX = Math.cos(rotX);
		return (n: DisplayNode) => {
			const lx = n.x - cx;
			const ly = n.y - cy;
			const lz = n.z * 140;
			const x1 = lx * cosY - lz * sinY;
			const z1 = lx * sinY + lz * cosY;
			const y2 = ly * cosX - z1 * sinX;
			const sx = cx + x1 * zoom + panX;
			const sy = cy + y2 * zoom + panY;
			return { sx, sy };
		};
	}

	function pickNode(ev: PointerEvent | MouseEvent): DisplayNode | null {
		if (!canvas) return null;
		const rect = canvas.getBoundingClientRect();
		const x = ev.clientX - rect.left;
		const y = ev.clientY - rect.top;
		const proj = projectHit(canvas.width / 2, canvas.height / 2);
		let best: DisplayNode | null = null;
		let bestD2 = 20 * 20;
		for (const n of nodes) {
			const { sx, sy } = proj(n);
			const dx = sx - x;
			const dy = sy - y;
			const d2 = dx * dx + dy * dy;
			if (d2 < bestD2) { bestD2 = d2; best = n; }
		}
		return best;
	}

	function pickEdge(ev: MouseEvent): Edge | null {
		if (!canvas) return null;
		const rect = canvas.getBoundingClientRect();
		const mx = ev.clientX - rect.left;
		const my = ev.clientY - rect.top;
		const proj = projectHit(canvas.width / 2, canvas.height / 2);
		let best: Edge | null = null;
		let bestD = 8; // pixel threshold
		for (const e of edges) {
			const sn = nodeById.get(e.source);
			const tn = nodeById.get(e.target);
			if (!sn || !tn) continue;
			const a = proj(sn);
			const b = proj(tn);
			// Point-to-segment distance
			const dx = b.sx - a.sx;
			const dy = b.sy - a.sy;
			const len2 = dx * dx + dy * dy;
			if (len2 < 1) continue;
			const t = Math.max(0, Math.min(1, ((mx - a.sx) * dx + (my - a.sy) * dy) / len2));
			const px = a.sx + t * dx;
			const py = a.sy + t * dy;
			const d = Math.sqrt((mx - px) ** 2 + (my - py) ** 2);
			if (d < bestD) { bestD = d; best = e; }
		}
		return best;
	}

	function onPointerDown(ev: PointerEvent) {
		if (!canvas) return;
		// Ctrl+click → edge creation
		if (ev.ctrlKey || ev.metaKey) {
			const hit = pickNode(ev);
			if (hit) {
				if (!ctrlPickFirst) {
					ctrlPickFirst = hit.id;
				} else if (ctrlPickFirst !== hit.id) {
					// Open edge create menu
					edgeMenu = {
						x: ev.clientX,
						y: ev.clientY,
						source: ctrlPickFirst,
						target: hit.id,
						kind: 'semantic',
						weight: 0.5,
						mode: 'create'
					};
					ctrlPickFirst = null;
				}
			}
			return;
		}

		if (ev.button === 2) return; // right-click handled by contextmenu
		const hit = pickNode(ev);
		if (hit && !ev.shiftKey && ev.button === 0) {
			dragMode = 'node';
			draggedNode = hit;
			hit.pinned = true;
		} else if (ev.shiftKey || ev.button === 1 || ev.buttons === 4) {
			dragMode = 'pan';
		} else {
			dragMode = 'rotate';
		}
		dragging = true;
		lastPx = ev.clientX;
		lastPy = ev.clientY;
		autoRotate = false;
		canvas.setPointerCapture(ev.pointerId);
	}
	function onPointerUp(ev: PointerEvent) {
		dragging = false;
		draggedNode = null;
		try { canvas!.releasePointerCapture(ev.pointerId); } catch {}
	}
	function onPointerMove(ev: PointerEvent) {
		if (!canvas) return;
		if (dragging) {
			const ddx = ev.clientX - lastPx;
			const ddy = ev.clientY - lastPy;
			if (dragMode === 'pan') {
				panX += ddx;
				panY += ddy;
			} else if (dragMode === 'node' && draggedNode) {
				const sinY = Math.sin(rotY);
				const cosY = Math.cos(rotY);
				const wdx = (ddx / zoom) * cosY + 0 * sinY;
				const wdy = ddy / zoom;
				draggedNode.x += wdx;
				draggedNode.y += wdy;
			} else {
				rotY += ddx * 0.01;
				rotX = Math.max(-1.3, Math.min(1.3, rotX + ddy * 0.01));
			}
			lastPx = ev.clientX;
			lastPy = ev.clientY;
			return;
		}
		const hit = pickNode(ev);
		hovered = hit ? hit.id : null;
		hoveredEdge = hit ? null : pickEdge(ev);
	}

	function onClick(ev: MouseEvent) {
		if (ev.ctrlKey || ev.metaKey) return; // handled by pointerdown
		const hit = pickNode(ev as unknown as PointerEvent);
		if (hit) {
			editNodeId = hit.id;
		}
	}

	function onContextMenu(ev: MouseEvent) {
		ev.preventDefault();
		// Try picking an edge first
		const edge = pickEdge(ev);
		if (edge) {
			edgeMenu = {
				x: ev.clientX,
				y: ev.clientY,
				source: edge.source,
				target: edge.target,
				kind: edge.kind,
				weight: edge.weight,
				mode: 'edit'
			};
			return;
		}
		// Try picking a node for edge creation start
		const node = pickNode(ev as unknown as PointerEvent);
		if (node) {
			if (!ctrlPickFirst) {
				ctrlPickFirst = node.id;
			}
		}
	}

	function onWheel(ev: WheelEvent) {
		ev.preventDefault();
		const rect = canvas!.getBoundingClientRect();
		const mx = ev.clientX - rect.left - canvas!.width / 2;
		const my = ev.clientY - rect.top - canvas!.height / 2;
		const factor = ev.deltaY < 0 ? 1.1 : 1 / 1.1;
		const newZoom = Math.max(0.2, Math.min(6, zoom * factor));
		const k = newZoom / zoom - 1;
		panX -= (mx - panX) * k;
		panY -= (my - panY) * k;
		zoom = newZoom;
	}

	function onKeyDown(ev: KeyboardEvent) {
		// Ctrl+Z undo
		if ((ev.ctrlKey || ev.metaKey) && ev.key === 'z' && !ev.shiftKey) {
			ev.preventDefault();
			performUndo();
		}
		// Escape to cancel ctrl pick
		if (ev.key === 'Escape') {
			ctrlPickFirst = null;
			edgeMenu = null;
		}
	}

	async function performUndo() {
		const action = undoStack.pop();
		if (!action) return;
		try {
			switch (action.type) {
				case 'create_edge':
					// Undo = delete the edge we just created
					await api.poolDeleteEdge(action.data.source as string, action.data.target as string);
					break;
				case 'delete_edge':
					// Undo = recreate the edge
					await api.poolCreateEdge(
						action.data.source as string,
						action.data.target as string,
						action.data.kind as string,
						action.data.weight as number
					);
					break;
				case 'update_edge':
					// Undo = restore old values
					await api.poolUpdateEdge(
						action.data.source as string,
						action.data.target as string,
						action.data.oldKind as string,
						action.data.oldWeight as number
					);
					break;
			}
			await loadGraph(false);
		} catch { /* ignore undo failures */ }
	}

	function resetView() {
		zoom = 1;
		panX = 0;
		panY = 0;
		rotY = 0;
		rotX = 0.35;
		ctrlPickFirst = null;
		for (const n of nodes) n.pinned = false;
	}

	function resize() {
		if (!canvas) return;
		const rect = canvas.getBoundingClientRect();
		canvas.width = rect.width;
		canvas.height = rect.height;
	}

	let pollTimer: ReturnType<typeof setInterval> | null = null;

	async function loadGraph(initial = false) {
		try {
			const payload: GraphPayload = await api.graph();
			const W = canvas?.clientWidth || 800;
			const H = canvas?.clientHeight || 600;
			const cx = W / 2;
			const cy = H / 2;
			const existing = nodeById;
			const next: DisplayNode[] = payload.nodes.map((n) => {
				const prior = existing.get(n.id);
				if (prior) {
					prior.intensity = n.intensity ?? prior.intensity;
					prior.last_accessed = n.last_accessed ?? prior.last_accessed;
					prior.access_count = n.access_count ?? prior.access_count;
					prior.label = n.label || prettyLabel(n.id);
					return prior;
				}
				const a = hash01(n.id, 1) * Math.PI * 2;
				const r = 60 + hash01(n.id, 2) * 140;
				return {
					id: n.id,
					label: n.label || prettyLabel(n.id),
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
			nodes = next;
			edges = payload.edges;
			nodeById = new Map(nodes.map((n) => [n.id, n]));
			const recalled = nodes.filter((n) => recallGlow(n) > 0.2).length;
			stats = { nodes: nodes.length, edges: edges.length, recalled };
			if (initial) {
				resize();
				raf = requestAnimationFrame(loop);
			}
			loading = false;
		} catch (e) {
			if (initial) err = (e as Error).message;
			loading = false;
		}
	}

	function onEdgeSaved() {
		loadGraph(false);
	}
	function onNodeSaved() {
		loadGraph(false);
	}

	onMount(async () => {
		try {
			const saved = localStorage.getItem('fiam-theme');
			if (saved === 'light' || saved === 'dark') theme = saved;
		} catch {}
		// Load edge types
		try {
			const resp = await api.poolEdgeTypes();
			if (resp.types?.length) edgeTypes = resp.types;
		} catch {}
		await loadGraph(true);
		window.addEventListener('resize', resize);
		pollTimer = setInterval(() => loadGraph(false), 30000);
	});

	onDestroy(() => {
		if (raf) cancelAnimationFrame(raf);
		if (pollTimer) clearInterval(pollTimer);
		window.removeEventListener('resize', resize);
	});
</script>

<svelte:window onkeydown={onKeyDown} />

<div class="flex flex-col h-[calc(100vh-8rem)] relative">
	<div class="flex items-center gap-4 mb-2 text-xs font-mono flex-wrap">
		<span class="text-[var(--color-subtext0)]">nodes {stats.nodes}</span>
		<span class="text-[var(--color-subtext0)]">edges {stats.edges}</span>
		<span class="text-[var(--color-pink)]">recalled {stats.recalled}</span>
		{#if ctrlPickFirst}
			<span class="text-[var(--color-yellow)]">
				pick 2nd node (Ctrl+click) · Esc cancel
			</span>
		{/if}
		<span class="text-[var(--color-overlay0)] hidden md:inline">
			click=edit · right-click edge=menu · ctrl+click 2 nodes=link · ctrl+z=undo
		</span>
		<label class="ml-auto flex items-center gap-1 cursor-pointer">
			<input type="checkbox" bind:checked={autoRotate} class="accent-[var(--color-mauve)]" />
			<span class="text-[var(--color-overlay1)]">auto-rotate</span>
		</label>
		<button
			class="px-2 py-0.5 border border-[var(--color-surface1)] rounded text-[var(--color-subtext0)] hover:border-[var(--color-mauve)] cursor-pointer"
			onclick={() => loadGraph(false)}
		>refresh</button>
		<button
			class="px-2 py-0.5 border border-[var(--color-surface1)] rounded text-[var(--color-subtext0)] hover:border-[var(--color-mauve)] cursor-pointer"
			onclick={resetView}
		>reset</button>
		<button
			class="px-2 py-0.5 border border-[var(--color-surface1)] rounded text-[var(--color-subtext0)] hover:border-[var(--color-mauve)] cursor-pointer"
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
		onclick={onClick}
		oncontextmenu={onContextMenu}
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

	{#if editNodeId}
		<NodeEditor id={editNodeId} onclose={() => (editNodeId = null)} onsaved={onNodeSaved} />
	{/if}

	{#if edgeMenu}
		<EdgeMenu
			x={edgeMenu.x}
			y={edgeMenu.y}
			source={edgeMenu.source}
			target={edgeMenu.target}
			kind={edgeMenu.kind}
			weight={edgeMenu.weight}
			mode={edgeMenu.mode}
			{edgeTypes}
			onclose={() => (edgeMenu = null)}
			onsaved={onEdgeSaved}
		/>
	{/if}
</div>
