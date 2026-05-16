/**
 * Tiny API client for the fiam dashboard backend.
 * Backend is scripts/dashboard_server.py — exposes JSON endpoints under /api/*.
 */

const BASE = '/api';

async function j<T>(path: string): Promise<T> {
	const res = await fetch(`${BASE}${path}`, { credentials: 'same-origin', cache: 'no-store' });
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
		actor: string;
		channel: string;
		surface?: string;
		kind: string;
		content: string;
		runtime?: string | null;
		meta?: Record<string, unknown>;
	}[];
	offset: number;
	total: number;
}

export interface RuntimeConfig {
	memory_mode: 'manual' | 'auto';
	app?: {
		default_runtime: 'auto' | 'api' | 'cc';
		recall_include_recent: boolean;
	};
	cc?: {
		model: string;
		effort: string;
		disallowed_tools: string;
		transport: string;
		warm_alive: boolean;
	};
	route_state?: {
		family?: string;
		reason?: string;
		remaining_turns?: number;
		updated_at?: string;
	};
	annotation: { processed_until: number };
	catalog?: Record<string, CatalogEntry>;
}

export interface CatalogEntry {
	provider: string;
	model: string;
	fallbacks: string[];
	extended_thinking: boolean;
	budget_tokens: number;
}

export interface CatalogPayload {
	catalog: Record<string, CatalogEntry>;
	cache: Record<string, { models: string[]; refreshed_at?: string }>;
	providers: string[];
	families: string[];
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

export interface ObjectRecord {
	object_hash: string;
	token: string;
	name?: string;
	mime?: string;
	size?: number;
	t?: string;
	channel?: string;
	surface?: string;
	kind?: string;
	actor?: string;
	event_id?: string;
	turn_id?: string;
	dispatch_id?: string;
	direction?: string;
	visibility?: string;
	provenance?: string;
	summary?: string;
	tags?: string[];
	source?: string;
}

export interface ObjectsPayload {
	records: ObjectRecord[];
	returned: number;
	query: string;
	token?: string;
	object_hash?: string;
	error?: string;
}

export interface TraceRow {
	turn_id: string;
	request_id?: string;
	session_id?: string;
	channel?: string;
	surface?: string;
	phase: string;
	status: 'ok' | 'error' | 'skipped' | string;
	started_at?: string;
	ended_at?: string;
	duration_ms?: number;
	runtime?: string;
	model?: string;
	attempt?: number;
	error?: string;
	refs?: Record<string, unknown>;
}

export interface TracePayload {
	rows: TraceRow[];
	total: number;
	returned: number;
	path?: string;
	filters?: Record<string, string>;
	summary?: {
		by_status?: Record<string, number>;
		by_phase?: Record<string, number>;
		failures?: Array<{ phase: string; turn_id: string; request_id?: string; error?: string; refs?: Record<string, unknown> }>;
		retry_phases?: number;
		total_duration_ms?: number;
		slowest?: Array<{ phase: string; duration_ms: number; turn_id: string }>;
	};
	error?: string;
}

export interface RuntimeChannelStatus {
	transcript_path?: string;
	transcript_dir?: string;
	session_id?: string;
	channel_request_id?: string;
	resume_attempted?: boolean;
	start_offset?: number;
	transcript_exists?: boolean;
	transcript_size?: number;
	rows_read?: number;
	safe_offset?: number;
	seen_matching_user?: boolean;
	user_origins_seen?: string[];
	user_row_previews?: string[];
	assistant_stops_seen?: string[];
	parsed_rows?: number;
	last_updated?: number;
}

export interface RuntimeInflightEntry {
	id: number;
	kind: string;
	channel: string;
	surface: string;
	request_id: string;
	turn_id: string;
	started_at: number;
	elapsed_ms: number;
	pty_tail?: string;
	status?: RuntimeChannelStatus;
}

export interface RuntimeFailureEntry {
	kind?: string;
	channel?: string;
	surface?: string;
	request_id?: string;
	turn_id?: string;
	started_at?: number;
	ended_at?: number;
	duration_ms?: number;
	error?: string;
	pty_tail?: string;
	status?: RuntimeChannelStatus;
}

export interface RuntimeTurnRow {
	started_at: string;
	ended_at: string;
	duration_ms: number;
	phase: string;
	status: string;
	channel: string;
	surface: string;
	turn_id: string;
	request_id: string;
	model?: string;
	subtype?: string;
	returncode?: number | null;
	action_count?: number | null;
	error?: string;
}

export interface RuntimePayload {
	transport: {
		env: string;
		mode: string;
		channel_supported: boolean;
		channel_enabled: boolean;
	};
	channel_health: {
		server_path: string;
		server_exists: boolean;
		node_modules_path: string;
		node_modules_exists: boolean;
	};
	channel_flags?: {
		exclude_dynamic_system_prompt: boolean;
		max_turns: boolean;
		effort: boolean;
	};
	warm_runner: {
		alive: boolean;
		fingerprint?: string;
		last_session_id?: string;
		last_used_ago_sec?: number;
	};
	inflight: RuntimeInflightEntry[];
	recent_runtime: RuntimeTurnRow[];
	recent_failures: RuntimeFailureEntry[];
	trace_file: string;
	error?: string;
}

export interface TimelineRecord {
	path: string;
	heading: string;
	text: string;
	refs: string[];
}

export interface TimelinePayload {
	records: TimelineRecord[];
	returned: number;
	query: string;
	path: string;
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
	catalog: () => j<CatalogPayload>('/catalog/list'),
	refreshCatalog: (provider: string) =>
		mutate<{ ok: boolean; provider: string; models: string[] }>('POST', '/catalog/refresh', { provider }),
	saveCatalog: (payload: {
		family: string;
		provider: string;
		model: string;
		fallbacks?: string[];
		extended_thinking?: boolean;
		budget_tokens?: number;
	}) => mutate<{ ok: boolean; family: string; catalog: CatalogEntry }>('POST', '/config/catalog', payload),
	saveRuntimeConfig: (payload: {
		default_runtime?: 'auto' | 'api' | 'cc';
		recall_include_recent?: boolean;
		cc_model?: string;
		cc_effort?: string;
		cc_disallowed_tools?: string;
		clear_route_state?: boolean;
	}) => mutate<{ ok: boolean; config: RuntimeConfig }>('POST', '/config/runtime', payload),
	setMemoryMode: (memory_mode: 'manual' | 'auto') =>
		mutate<{ ok: boolean; memory_mode: 'manual' | 'auto' }>('POST', '/config/memory-mode', { memory_mode }),
	plugins: () => j<{ plugins: PluginManifest[] }>('/plugins'),
	setPluginEnabled: (id: string, enabled: boolean) =>
		mutate<{ ok: boolean; id: string; enabled: boolean }>('POST', '/config/plugin', { id, enabled }),
	graph: () => j<GraphPayload>('/graph'),
	pipeline: () => j<{ lines: string[] }>('/pipeline'),
	whoami: () => j<{ role: 'Zephyr' | 'ai' | 'live' | 'anon' }>('/whoami'),

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
	objects: (params: { query?: string; token?: string; limit?: number } = {}) => {
		const q = new URLSearchParams();
		if (params.query) q.set('q', params.query);
		if (params.token) q.set('token', params.token);
		q.set('limit', String(params.limit ?? 20));
		return j<ObjectsPayload>(`/objects?${q.toString()}`);
	},
	timeline: (params: { query?: string; limit?: number } = {}) => {
		const q = new URLSearchParams();
		if (params.query) q.set('q', params.query);
		q.set('limit', String(params.limit ?? 20));
		return j<TimelinePayload>(`/timeline?${q.toString()}`);
	},

	// Debug — last assembled context per runtime + raw flow tail
	debugContext: (runtime: 'latest' | 'api' | 'cc' = 'latest') => j<DebugContextPayload>(`/debug/context?runtime=${runtime}`),
	debugFlow: (limit = 200) => j<DebugFlowPayload>(`/debug/flow?limit=${limit}`),
	debugRuntime: () => j<RuntimePayload>('/debug/runtime'),
	debugTrace: (params: {
		turn_id?: string;
		request_id?: string;
		session_id?: string;
		phase?: string;
		status?: string;
		limit?: number;
	} = {}) => {
		const q = new URLSearchParams();
		for (const key of ['turn_id', 'request_id', 'session_id', 'phase', 'status'] as const) {
			const value = params[key];
			if (value) q.set(key, value);
		}
		q.set('limit', String(params.limit ?? 200));
		return j<TracePayload>(`/debug/trace?${q.toString()}`);
	},

	// Annotation
	annotateProposal: () => j<AnnotateProposal>('/annotate/proposal'),
	annotateRequest: (offset?: number, limit?: number) =>
		mutate<AnnotateProposal>('POST', '/annotate/request', { offset, limit }),
	annotateEdges: (cuts?: number[], drift_cuts?: number[]) =>
		mutate<AnnotateProposal>('POST', '/annotate/edges', { cuts, drift_cuts }),
	annotateConfirm: (cuts: number[], drift_cuts: number[], edges: AnnotateEdge[]) =>
		mutate<AnnotateConfirmResult>('POST', '/annotate/confirm', { cuts, drift_cuts, edges })
};
