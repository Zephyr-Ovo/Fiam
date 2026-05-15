<script lang="ts">
	import '../app.css';
	import favicon from '$lib/assets/favicon.svg';
	import { api, type Status } from '$lib/api';
	import { onMount } from 'svelte';

	let { children } = $props();
	let theme = $state<'dark' | 'light'>('dark');
	let userRole = $state<'Zephyr' | 'ai' | 'live' | 'anon'>('anon');
	let statusText = $state('—');

	onMount(() => {
		try {
			const saved = localStorage.getItem('fiam-theme');
			if (saved === 'light' || saved === 'dark') theme = saved;
		} catch {}
		void refreshHeader();
	});

	$effect(() => {
		if (typeof document === 'undefined') return;
		document.documentElement.setAttribute('data-theme', theme);
		try {
			localStorage.setItem('fiam-theme', theme);
		} catch {}
	});

	function toggleTheme() {
		theme = theme === 'dark' ? 'light' : 'dark';
	}

	async function refreshHeader() {
		try {
			const [whoami, status] = await Promise.all([
				api.whoami().catch(() => null),
				api.status().catch(() => null)
			]);
			if (whoami) userRole = whoami.role;
			if (status) statusText = `${status.daemon} · ${status.events} events`;
		} catch {}
	}
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>fiam console</title>
</svelte:head>

<div class="min-h-screen flex flex-col">
	<header
		class="border-b border-[var(--color-surface0)] px-4 py-2 flex items-center gap-4 bg-[var(--color-mantle)] flex-wrap"
	>
		<span class="font-mono text-sm text-[var(--color-mauve)]">ℱ fiam</span>
		<nav class="flex gap-x-3 gap-y-1 text-sm text-[var(--color-subtext1)] flex-wrap min-w-0">
			<a href="/" class="hover:text-[var(--color-lavender)]">overview</a>
			<a href="/events" class="hover:text-[var(--color-lavender)]">events</a>
			<a href="/objects" class="hover:text-[var(--color-lavender)]">objects</a>
			<a href="/panel" class="hover:text-[var(--color-lavender)]">panel</a>
			<a href="/runtime" class="hover:text-[var(--color-lavender)]">runtime</a>
			<a href="/trace" class="hover:text-[var(--color-lavender)]">trace</a>
			<a href="/graph" class="hover:text-[var(--color-lavender)]">graph</a>
			<a href="/annotate" class="hover:text-[var(--color-lavender)]">annotate</a>
			<a href="/todo" class="hover:text-[var(--color-lavender)]">todo</a>
			<a href="/logs" class="hover:text-[var(--color-lavender)]">logs</a>
		</nav>
		<button
			onclick={toggleTheme}
			class="ml-auto text-xs font-mono px-2 py-0.5 rounded border border-[var(--color-surface1)] text-[var(--color-subtext0)] hover:bg-[var(--color-surface0)] cursor-pointer"
			title="toggle theme"
		>
			{theme === 'dark' ? '☾ dark' : '☀ light'}
		</button>
		<span class="text-xs text-[var(--color-overlay0)] font-mono" id="user-badge">{userRole}</span>
	</header>

	<main class="flex-1 p-4">
		{@render children()}
	</main>

	<footer
		class="border-t border-[var(--color-surface0)] px-4 py-1 text-xs text-[var(--color-overlay0)] font-mono flex justify-between bg-[var(--color-mantle)]"
	>
		<span>console · {theme === 'dark' ? 'catppuccin mocha' : 'claude paper'}</span>
		<span id="status-ping">{statusText}</span>
	</footer>
</div>
