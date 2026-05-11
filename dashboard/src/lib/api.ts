/**
 * Tiny API client for the fiam dashboard backend.
 * Backend is scripts/dashboard_server.py — exposes JSON endpoints under /api/*.
 */

const BASE = '/api';

async function j<T>(path: string): Promise<T> {
	const res = await fetch(`${BASE}${path}`, { credentials: 'same-origin' });
	if (!res.ok) throw new Error(`${path}: ${res.status}`);
	return res.json();
}

async function mutate<T>(method: string, path: string, body?: unknown): Promise<T> {
	const res = await fetch(`${BASE}${path}`, {
		method,
		credentials: 'same-origin',
		headers: body ? { 'Content-Type': 'application/json' } : {},
		body: body ? JSON.stringify(body) : undefined
	});
	if (!res.ok) {
		const text = await res.text().catch(() => '');
		throw new Error(`${path}: ${res.status} ${text}`);
	}
	return res.json();
}

export interface Status {
	daemon: 'running' | 'stopped';
	pid: number | null;
	events: number;
	embeddings: number;
	last_processed: string | null;
	home: string;
	uptime_sec: number | null;
}

export interface EventRow {
	id: string;
	time: string;
	intensity: number;
	preview: string;
}

export interface TodoRow {
	at: string;
	type: string;
	reason: string;
}

export interface StateSnapshot {
	mood: string;
	tension: number;
	reflection: string;
	updated_at: string;
}

export interface GraphPayload {
	nodes: {
		id: string;
		label: string;
		intensity: number;
		time?: string;
		last_accessed?: string;
		access_count?: number;
	}[];
	edges: { source: string; target: string; kind: string; weight: number }[];
}

export interface EventDetail {
	id: string;
	frontmatter: Record<string, string>;
	body: string;
}

export interface PoolEventDetail {
	id: string;
	body: string;
	time: string;
	access_count: number;
	fingerprint_idx: number;
}

export interface FlowPayload {
	beats: {
		t: string;
		text: string;
		actor?: string;
		channel?: string;
		runtime?: string | null;
		meta?: Record<string, unknown>;
		user: string;
		ai: string;
	}[];
	offset: number;
	total: number;
}

export interface RuntimeConfig {
	memory_mode: 'manual' | 'auto';
	annotation: { processed_until: number };
}

export interface DebugContextPart {
	role: string;
	label: string;
	cache: boolean;
	length: number;
	text: string;
}

export interface DebugContextMetrics {
	runtime?: string;
	model?: string;
	latency_ms?: number;
	cost_usd?: number;
	tokens_in?: number;
	tokens_out?: number;
	tokens_cache_read?: number;
	tokens_cache_creation?: number;
	raw_usage?: Record<string, unknown>;
}

export interface DebugContextPayload {
	timestamp?: number;
	runtime?: string;
	channel?: string;
	session_id?: string;
	parts?: DebugContextPart[];
	metrics?: DebugContextMetrics;
	empty?: boolean;
	error?: string;
}

export interface DebugFlowPayload {
	rows: Record<string, unknown>[];
	total: number;
	returned: number;
	error?: string;
}

export interface PluginManifest {
	id: string;
	name: string;
	enabled: boolean;
	status: string;
	kind: string;
	description: string;
	transports: string[];
	capabilities: string[];
	receive_channels: string[];
	dispatch_targets: string[];
	entrypoint: string;
	auth: string;
	latency: string;
	env: string[];
	replaces: string[];
	notes: string[];
}

export interface AnnotateEdge {
	src: string;
	dst: string;
	type: string;
	weight: number;
	reason: string;
}

export interface AnnotateProposal {
	status: 'none' | 'cuts_proposed' | 'edges_proposed';
	beats?: FlowPayload['beats'];
	cuts?: number[];
	drift_cuts?: number[];
	names?: Record<string, string>;
	edges?: AnnotateEdge[];
	flow_offset?: number;
	flow_end?: number;
	processed_until?: number;
}

export interface AnnotateConfirmResult {
	ok: boolean;
	events_created: string[];
	edges_created: number;
	saved_boundaries: number;
	saved_pairs: number;
}

export const api = {
	status: () => j<Status>('/status'),
	events: (limit = 50) => j<EventRow[]>(`/events?limit=${limit}`),
	event: (id: string) => j<EventDetail>(`/event/${encodeURIComponent(id)}`),
	todo: () => j<TodoRow[]>('/todo'),
	state: () => j<StateSnapshot>('/state'),
	config: () => j<RuntimeConfig>('/config'),
	setMemoryMode: (memory_mode: 'manual' | 'auto') =>
		mutate<{ ok: boolean; memory_mode: 'manual' | 'auto' }>('POST', '/config/memory-mode', { memory_mode }),
	plugins: () => j<{ plugins: PluginManifest[] }>('/plugins'),
	setPluginEnabled: (id: string, enabled: boolean) =>
		mutate<{ ok: boolean; id: string; enabled: boolean }>('POST', '/config/plugin', { id, enabled }),
	graph: () => j<GraphPayload>('/graph'),
	pipeline: () => j<{ lines: string[] }>('/pipeline'),
	whoami: () => j<{ role: 'iris' | 'ai' | 'live' | 'anon' }>('/whoami'),

	// Pool APIs
	poolGraph: () => j<GraphPayload>('/pool/graph'),
	poolEvent: (id: string) => j<PoolEventDetail>(`/pool/event/${encodeURIComponent(id)}`),
	poolUpdateEvent: (id: string, body: string) =>
		mutate<{ ok: boolean; re_embedded: boolean }>('POST', `/pool/event/${encodeURIComponent(id)}`, { body }),
	poolDeleteEvent: (id: string) =>
		mutate<{ ok: boolean }>('POST', `/pool/event/delete/${encodeURIComponent(id)}`, {}),
	poolCreateEdge: (source: string, target: string, kind: string, weight = 0.5) =>
		mutate<{ ok: boolean }>('POST', '/pool/edge', { source, target, kind, weight }),
	poolUpdateEdge: (source: string, target: string, kind?: string, weight?: number) =>
		mutate<{ ok: boolean }>('PUT', '/pool/edge', { source, target, kind, weight }),
	poolDeleteEdge: (source: string, target: string) =>
		mutate<{ ok: boolean }>('POST', '/pool/edge/delete', { source, target }),
	poolEdgeTypes: () => j<{ types: string[] }>('/pool/edge-types'),

	// Flow
	flow: (offset = 0, limit = 50) => j<FlowPayload>(`/flow?offset=${offset}&limit=${limit}`),

	// Debug — last assembled context per runtime + raw flow tail
	debugContext: (runtime: 'api' | 'cc') => j<DebugContextPayload>(`/debug/context?runtime=${runtime}`),
	debugFlow: (limit = 200) => j<DebugFlowPayload>(`/debug/flow?limit=${limit}`),

	// Annotation
	annotateProposal: () => j<AnnotateProposal>('/annotate/proposal'),
	annotateRequest: (offset?: number, limit?: number) =>
		mutate<AnnotateProposal>('POST', '/annotate/request', { offset, limit }),
	annotateEdges: (cuts?: number[], drift_cuts?: number[]) =>
		mutate<AnnotateProposal>('POST', '/annotate/edges', { cuts, drift_cuts }),
	annotateConfirm: (cuts: number[], drift_cuts: number[], edges: AnnotateEdge[]) =>
		mutate<AnnotateConfirmResult>('POST', '/annotate/confirm', { cuts, drift_cuts, edges })
};
