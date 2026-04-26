<script lang="ts">
	import '../app.css';
	import favicon from '$lib/assets/favicon.svg';
	import { onMount } from 'svelte';

	let { children } = $props();
	let theme = $state<'dark' | 'light'>('dark');

	onMount(() => {
		try {
			const saved = localStorage.getItem('fiam-theme');
			if (saved === 'light' || saved === 'dark') theme = saved;
		} catch {}
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
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>fiam console</title>
</svelte:head>

<div class="min-h-screen flex flex-col">
	<header
		class="border-b border-[var(--color-surface0)] px-4 py-2 flex items-center gap-4 bg-[var(--color-mantle)]"
	>
		<span class="font-mono text-sm text-[var(--color-mauve)]">ℱ fiam</span>
		<nav class="flex gap-3 text-sm text-[var(--color-subtext1)]">
			<a href="/" class="hover:text-[var(--color-lavender)]">overview</a>
			<a href="/events" class="hover:text-[var(--color-lavender)]">events</a>
			<a href="/graph" class="hover:text-[var(--color-lavender)]">graph</a>
			<a href="/flow" class="hover:text-[var(--color-lavender)]">flow</a>
			<a href="/annotate" class="hover:text-[var(--color-lavender)]">annotate</a>
			<a href="/schedule" class="hover:text-[var(--color-lavender)]">schedule</a>
			<a href="/logs" class="hover:text-[var(--color-lavender)]">logs</a>
		</nav>
		<button
			onclick={toggleTheme}
			class="ml-auto text-xs font-mono px-2 py-0.5 rounded border border-[var(--color-surface1)] text-[var(--color-subtext0)] hover:bg-[var(--color-surface0)] cursor-pointer"
			title="toggle theme"
		>
			{theme === 'dark' ? '☾ dark' : '☀ light'}
		</button>
		<span class="text-xs text-[var(--color-overlay0)] font-mono" id="user-badge">—</span>
	</header>

	<main class="flex-1 p-4">
		{@render children()}
	</main>

	<footer
		class="border-t border-[var(--color-surface0)] px-4 py-1 text-xs text-[var(--color-overlay0)] font-mono flex justify-between bg-[var(--color-mantle)]"
	>
		<span>console · {theme === 'dark' ? 'catppuccin mocha' : 'claude paper'}</span>
		<span id="status-ping">·</span>
	</footer>
</div>
