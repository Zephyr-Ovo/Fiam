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

export interface ScheduleRow {
	wake_at: string;
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
	beats: { t: string; text: string; source: string; user: string; ai: string }[];
	offset: number;
	total: number;
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
	beats?: FlowPayload['beats'][];
	cuts?: number[];
	edges?: AnnotateEdge[];
	flow_offset?: number;
	flow_end?: number;
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
	schedule: () => j<ScheduleRow[]>('/schedule'),
	state: () => j<StateSnapshot>('/state'),
	graph: () => j<GraphPayload>('/graph'),
	pipeline: () => j<{ lines: string[] }>('/pipeline'),
	whoami: () => j<{ role: 'iris' | 'ai' | 'fiet' | 'anon' }>('/whoami'),

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

	// Annotation
	annotateProposal: () => j<AnnotateProposal>('/annotate/proposal'),
	annotateRequest: (offset?: number, limit?: number) =>
		mutate<AnnotateProposal>('POST', '/annotate/request', { offset, limit }),
	annotateEdges: (cuts?: number[]) =>
		mutate<AnnotateProposal>('POST', '/annotate/edges', { cuts }),
	annotateConfirm: (cuts: number[], edges: AnnotateEdge[]) =>
		mutate<AnnotateConfirmResult>('POST', '/annotate/confirm', { cuts, edges })
};
