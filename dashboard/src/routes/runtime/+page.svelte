<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { api, type RuntimePayload, type RuntimeTurnRow } from '$lib/api';

	let payload = $state<RuntimePayload | null>(null);
	let err = $state<string | null>(null);
	let lastRefreshed = $state('—');
	let timer: ReturnType<typeof setInterval>;

	async function refresh() {
		try {
			payload = await api.debugRuntime();
			err = null;
			lastRefreshed = new Date().toLocaleTimeString();
		} catch (e) {
			err = (e as Error).message;
		}
	}

	onMount(() => {
		refresh();
		timer = setInterval(refresh, 3000);
	});
	onDestroy(() => clearInterval(timer));

	function fmtTime(value?: string | number): string {
		if (!value) return '—';
		const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
		if (Number.isNaN(date.getTime())) return String(value);
		return date.toLocaleTimeString();
	}

	function fmtDur(ms?: number | null): string {
		const v = Number(ms || 0);
		if (!v) return '—';
		return v < 1000 ? `${v} ms` : `${(v / 1000).toFixed(2)} s`;
	}

	function statusColor(status: string): string {
		if (status === 'ok') return 'var(--color-green)';
		if (status === 'error') return 'var(--color-red)';
		if (status === 'skipped') return 'var(--color-overlay1)';
		return 'var(--color-subtext0)';
	}

	function traceHref(row: RuntimeTurnRow): string {
		const q = new URLSearchParams();
		if (row.request_id) q.set('request_id', row.request_id);
		else if (row.turn_id) q.set('turn_id', row.turn_id);
		return `/trace?${q.toString()}`;
	}

	function shortId(value?: string, n = 12): string {
		if (!value) return '—';
		return value.length > n ? `${value.slice(0, n)}…` : value;
	}
</script>

<div class="flex flex-col gap-4 max-w-6xl mx-auto">
	<div class="flex items-center gap-3 flex-wrap">
		<h1 class="text-lg font-mono text-[var(--color-mauve)]">runtime</h1>
		<span class="text-xs font-mono text-[var(--color-overlay0)]">
			CC channel + cold/warm — auto refresh 3s · {lastRefreshed}
		</span>
		<button
			onclick={refresh}
			class="ml-auto px-3 py-1 text-xs font-mono rounded border border-[var(--color-surface1)] hover:bg-[var(--color-surface0)] cursor-pointer"
		>
			↻ refresh
		</button>
	</div>

	{#if err}
		<div class="text-xs font-mono text-[var(--color-red)] border border-[var(--color-red)]/60 bg-[var(--color-red)]/10 rounded p-3">
			{err}
		</div>
	{/if}

	<!-- Transport + channel health -->
	<div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-3">
		<div class="bg-[var(--color-mantle)] border border-[var(--color-surface0)] p-3">
			<h2 class="text-xs uppercase tracking-wide text-[var(--color-subtext0)] mb-2">Transport</h2>
			{#if payload}
				<dl class="text-xs font-mono grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
					<dt class="text-[var(--color-subtext0)]">mode</dt>
					<dd class="text-[var(--color-peach)]">{payload.transport.mode || '—'}</dd>
					<dt class="text-[var(--color-subtext0)]">env</dt>
					<dd class="truncate">FIAM_CC_TRANSPORT={payload.transport.env || '(unset)'}</dd>
					<dt class="text-[var(--color-subtext0)]">supported</dt>
					<dd style:color={payload.transport.channel_supported ? 'var(--color-green)' : 'var(--color-red)'}>
						{payload.transport.channel_supported ? 'yes (POSIX)' : 'no'}
					</dd>
					<dt class="text-[var(--color-subtext0)]">enabled</dt>
					<dd style:color={payload.transport.channel_enabled ? 'var(--color-green)' : 'var(--color-overlay1)'}>
						{payload.transport.channel_enabled ? 'channel' : 'fallback (cold/warm)'}
					</dd>
				</dl>
			{:else}
				<p class="text-xs text-[var(--color-overlay0)]">loading…</p>
			{/if}
		</div>

		<div class="bg-[var(--color-mantle)] border border-[var(--color-surface0)] p-3">
			<h2 class="text-xs uppercase tracking-wide text-[var(--color-subtext0)] mb-2">Channel server</h2>
			{#if payload}
				<dl class="text-xs font-mono grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
					<dt class="text-[var(--color-subtext0)]">server.mjs</dt>
					<dd style:color={payload.channel_health.server_exists ? 'var(--color-green)' : 'var(--color-red)'}>
						{payload.channel_health.server_exists ? 'ok' : 'missing'}
					</dd>
					<dt class="text-[var(--color-subtext0)]">@mcp/sdk</dt>
					<dd style:color={payload.channel_health.node_modules_exists ? 'var(--color-green)' : 'var(--color-red)'}>
						{payload.channel_health.node_modules_exists ? 'installed' : 'missing — run npm install'}
					</dd>
					<dt class="text-[var(--color-subtext0)]">path</dt>
					<dd class="truncate text-[var(--color-overlay1)]">{payload.channel_health.server_path}</dd>
				</dl>
			{/if}
		</div>

		<div class="bg-[var(--color-mantle)] border border-[var(--color-surface0)] p-3">
			<h2 class="text-xs uppercase tracking-wide text-[var(--color-subtext0)] mb-2">Channel flags</h2>
			{#if payload && payload.channel_flags}
				<dl class="text-xs font-mono grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
					<dt class="text-[var(--color-subtext0)]">--exclude-dynamic…</dt>
					<dd style:color={payload.channel_flags.exclude_dynamic_system_prompt ? 'var(--color-green)' : 'var(--color-overlay1)'}>{payload.channel_flags.exclude_dynamic_system_prompt ? 'on' : 'off (NO_EXCLUDE_DYNAMIC=1)'}</dd>
					<dt class="text-[var(--color-subtext0)]">--max-turns</dt>
					<dd style:color={payload.channel_flags.max_turns ? 'var(--color-green)' : 'var(--color-overlay1)'}>{payload.channel_flags.max_turns ? 'on' : 'off (NO_MAX_TURNS=1)'}</dd>
					<dt class="text-[var(--color-subtext0)]">--effort</dt>
					<dd style:color={payload.channel_flags.effort ? 'var(--color-green)' : 'var(--color-overlay1)'}>{payload.channel_flags.effort ? 'on (from cc_effort)' : 'off (NO_EFFORT=1)'}</dd>
				</dl>
				<p class="text-[10px] font-mono text-[var(--color-overlay0)] mt-2">toggle via FIAM_CC_CHANNEL_NO_* env vars</p>
			{/if}
		</div>

		<div class="bg-[var(--color-mantle)] border border-[var(--color-surface0)] p-3">
			<h2 class="text-xs uppercase tracking-wide text-[var(--color-subtext0)] mb-2">Warm runner</h2>
			{#if payload}
				<dl class="text-xs font-mono grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
					<dt class="text-[var(--color-subtext0)]">alive</dt>
					<dd style:color={payload.warm_runner.alive ? 'var(--color-green)' : 'var(--color-overlay1)'}>
						{payload.warm_runner.alive ? 'yes' : 'no'}
					</dd>
					{#if payload.warm_runner.alive}
						<dt class="text-[var(--color-subtext0)]">session</dt>
						<dd class="truncate">{shortId(payload.warm_runner.last_session_id, 18)}</dd>
						<dt class="text-[var(--color-subtext0)]">idle</dt>
						<dd>{payload.warm_runner.last_used_ago_sec ?? 0}s</dd>
					{/if}
				</dl>
			{/if}
		</div>
	</div>

	<!-- In-flight -->
	<div class="bg-[var(--color-mantle)] border border-[var(--color-surface0)] p-3">
		<h2 class="text-xs uppercase tracking-wide text-[var(--color-subtext0)] mb-2">
			In-flight ({payload?.inflight.length ?? 0})
		</h2>
		{#if payload && payload.inflight.length}
			<div class="flex flex-col gap-3">
				{#each payload.inflight as item (item.id)}
					<div class="border border-[var(--color-surface0)] rounded p-2">
						<div class="flex flex-wrap gap-x-4 gap-y-1 text-xs font-mono items-baseline">
							<span class="text-[var(--color-sapphire)]">{fmtTime(item.started_at)}</span>
							<span style:color={item.elapsed_ms > 60000 ? 'var(--color-red)' : item.elapsed_ms > 20000 ? 'var(--color-yellow)' : 'var(--color-text)'}>
								{fmtDur(item.elapsed_ms)}
							</span>
							<span class="text-[var(--color-peach)]">{item.kind}</span>
							<span>{item.channel || '—'}{item.surface ? `/${item.surface}` : ''}</span>
							<span class="text-[var(--color-overlay1)]">{shortId(item.request_id || item.turn_id, 24)}</span>
						</div>
						{#if item.status && item.status.transcript_path}
							<dl class="mt-2 text-[10px] font-mono grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[var(--color-subtext0)]">
								<dt>session</dt><dd class="break-all">{item.status.session_id}</dd>
								<dt>transcript</dt><dd style:color={item.status.transcript_exists ? 'var(--color-green)' : 'var(--color-red)'}>{item.status.transcript_exists ? `exists (${item.status.transcript_size} B)` : 'MISSING'}</dd>
								<dt>path</dt><dd class="break-all text-[var(--color-overlay1)]">{item.status.transcript_path}</dd>
								<dt>rows read</dt><dd>{item.status.rows_read ?? 0} (parsed {item.status.parsed_rows ?? 0})</dd>
								<dt>matched channel-user</dt><dd style:color={item.status.seen_matching_user ? 'var(--color-green)' : 'var(--color-yellow)'}>{item.status.seen_matching_user ? 'yes' : 'no'}</dd>
								<dt>user origins seen</dt><dd class="break-all">{(item.status.user_origins_seen ?? []).join(', ') || '—'}</dd>
								<dt>assistant stops</dt><dd class="break-all">{(item.status.assistant_stops_seen ?? []).join(', ') || '—'}</dd>
								<dt>channel req_id</dt><dd class="break-all">{item.status.channel_request_id}</dd>
							</dl>
							{#if (item.status.user_row_previews ?? []).length}
								<details class="mt-1">
									<summary class="text-[10px] font-mono text-[var(--color-overlay0)] cursor-pointer">user row previews ({(item.status.user_row_previews ?? []).length})</summary>
									{#each item.status.user_row_previews ?? [] as p}
										<pre class="mt-1 text-[10px] font-mono bg-[var(--color-crust)] border border-[var(--color-surface0)] rounded p-1 whitespace-pre-wrap break-all text-[var(--color-subtext1)]">{p}</pre>
									{/each}
								</details>
							{/if}
						{/if}
						{#if item.pty_tail}
							<details class="mt-2" open>
								<summary class="text-[10px] font-mono text-[var(--color-overlay0)] cursor-pointer">pty_tail (live)</summary>
								<pre class="mt-1 text-[10px] font-mono bg-[var(--color-crust)] border border-[var(--color-surface0)] rounded p-2 overflow-auto max-h-48 whitespace-pre-wrap break-all text-[var(--color-subtext1)]">{item.pty_tail}</pre>
							</details>
						{:else if item.kind.startsWith('channel')}
							<p class="mt-1 text-[10px] font-mono text-[var(--color-overlay0)]">pty_tail empty — claude may not have produced output yet</p>
						{/if}
					</div>
				{/each}
			</div>
		{:else}
			<p class="text-xs text-[var(--color-overlay0)] font-mono">idle</p>
		{/if}
	</div>

	<!-- Recent failures (from in-process tracker) -->
	{#if payload && payload.recent_failures.length}
		<div class="bg-[var(--color-mantle)] border border-[var(--color-red)]/40 p-3">
			<h2 class="text-xs uppercase tracking-wide text-[var(--color-red)] mb-2">
				Recent failures ({payload.recent_failures.length})
			</h2>
			<table>
				<thead>
					<tr>
						<th class="w-24">ended</th>
						<th class="w-20">duration</th>
						<th class="w-32">kind</th>
						<th class="w-40">scene</th>
						<th>error</th>
					</tr>
				</thead>
				<tbody>
					{#each payload.recent_failures.slice().reverse() as f}
						<tr>
							<td class="font-mono text-xs text-[var(--color-sapphire)]">{fmtTime(f.ended_at)}</td>
							<td class="font-mono text-xs">{fmtDur(f.duration_ms)}</td>
							<td class="font-mono text-xs text-[var(--color-peach)]">{f.kind ?? '—'}</td>
							<td class="font-mono text-xs">{(f.channel ?? '') || '—'}{f.surface ? `/${f.surface}` : ''}</td>
							<td class="font-mono text-xs">
								<div class="text-[var(--color-red)] break-words">{f.error ?? '—'}</div>
								{#if f.status && f.status.transcript_path}
									<dl class="mt-1 text-[10px] font-mono grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[var(--color-subtext0)]">
										<dt>transcript</dt><dd style:color={f.status.transcript_exists ? 'var(--color-green)' : 'var(--color-red)'}>{f.status.transcript_exists ? `exists (${f.status.transcript_size} B)` : 'MISSING'}</dd>
										<dt>rows read</dt><dd>{f.status.rows_read ?? 0}</dd>
										<dt>matched user</dt><dd style:color={f.status.seen_matching_user ? 'var(--color-green)' : 'var(--color-red)'}>{f.status.seen_matching_user ? 'yes' : 'no'}</dd>
										<dt>origins seen</dt><dd class="break-all">{(f.status.user_origins_seen ?? []).join(', ') || '—'}</dd>
										<dt>stops seen</dt><dd class="break-all">{(f.status.assistant_stops_seen ?? []).join(', ') || '—'}</dd>
										<dt>path</dt><dd class="break-all">{f.status.transcript_path}</dd>
									</dl>
								{/if}
								{#if f.pty_tail}
									<details class="mt-1">
										<summary class="text-[10px] font-mono text-[var(--color-overlay0)] cursor-pointer">pty_tail</summary>
										<pre class="mt-1 text-[10px] font-mono bg-[var(--color-crust)] border border-[var(--color-surface0)] rounded p-2 overflow-auto max-h-32 whitespace-pre-wrap break-all text-[var(--color-subtext1)]">{f.pty_tail}</pre>
									</details>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}

	<!-- Recent CC turns from traces -->
	<div class="bg-[var(--color-mantle)] border border-[var(--color-surface0)] p-3 overflow-auto">
		<h2 class="text-xs uppercase tracking-wide text-[var(--color-subtext0)] mb-2">
			Recent runtime phases ({payload?.recent_runtime.length ?? 0}) — from {payload?.trace_file ?? 'turn_traces.jsonl'}
		</h2>
		<table class="min-w-[900px]">
			<thead>
				<tr>
					<th class="w-24">time</th>
					<th class="w-20">duration</th>
					<th class="w-20">status</th>
					<th class="w-36">phase</th>
					<th class="w-40">scene</th>
					<th class="w-32">model</th>
					<th>ids · refs</th>
				</tr>
			</thead>
			<tbody>
				{#each payload?.recent_runtime ?? [] as row}
					<tr>
						<td class="font-mono text-xs text-[var(--color-sapphire)]">{fmtTime(row.started_at)}</td>
						<td class="font-mono text-xs">{fmtDur(row.duration_ms)}</td>
						<td class="font-mono text-xs">
							<span class="px-1.5 py-0.5 rounded border" style:color={statusColor(row.status)} style:border-color={statusColor(row.status)}>
								{row.status || '—'}
							</span>
						</td>
						<td class="font-mono text-xs text-[var(--color-mauve)]">{row.phase}</td>
						<td class="font-mono text-xs">{row.channel || '—'}{row.surface ? `/${row.surface}` : ''}</td>
						<td class="font-mono text-xs text-[var(--color-overlay1)]">{row.model || '—'}</td>
						<td class="font-mono text-xs">
							<a class="text-[var(--color-lavender)] hover:underline break-all" href={traceHref(row)}>{shortId(row.request_id || row.turn_id, 22)}</a>
							{#if row.error}
								<div class="text-[var(--color-red)] break-words mt-1">{row.error}</div>
							{/if}
							{#if row.subtype}
								<span class="text-[var(--color-overlay0)] ml-2">subtype={row.subtype}</span>
							{/if}
							{#if row.action_count != null}
								<span class="text-[var(--color-overlay0)] ml-2">actions={row.action_count}</span>
							{/if}
							{#if row.returncode != null && row.returncode !== 0}
								<span class="text-[var(--color-red)] ml-2">rc={row.returncode}</span>
							{/if}
						</td>
					</tr>
				{/each}
				{#if !payload?.recent_runtime.length}
					<tr><td colspan="7" class="text-center text-xs text-[var(--color-overlay0)] py-6">no runtime phases yet</td></tr>
				{/if}
			</tbody>
		</table>
	</div>
</div>
