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
	nodes: { id: string; label: string; intensity: number; time?: string }[];
	edges: { source: string; target: string; kind: string; weight: number }[];
}

export const api = {
	status: () => j<Status>('/status'),
	events: (limit = 50) => j<EventRow[]>(`/events?limit=${limit}`),
	schedule: () => j<ScheduleRow[]>('/schedule'),
	state: () => j<StateSnapshot>('/state'),
	graph: () => j<GraphPayload>('/graph'),
	pipeline: () => j<{ lines: string[] }>('/pipeline'),
	whoami: () => j<{ role: 'iris' | 'ai' | 'fiet' | 'anon' }>('/whoami')
};
