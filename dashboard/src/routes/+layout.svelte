<script lang="ts">
	import '../app.css';
	import favicon from '$lib/assets/favicon.svg';

	let { children } = $props();
</script>

<svelte:head>
	<link rel="icon" href={favicon} />
	<title>fiam console</title>
</svelte:head>

<div class="min-h-screen flex flex-col">
	<header
		class="border-b border-[var(--color-surface0)] px-4 py-2 flex items-center gap-4 bg-[var(--color-mantle)]"
	>
		<span class="font-mono text-sm text-[var(--color-mauve)]">fiam</span>
		<nav class="flex gap-3 text-sm">
			<a href="/" class="hover:text-[var(--color-lavender)]">overview</a>
			<a href="/events" class="hover:text-[var(--color-lavender)]">events</a>
			<a href="/graph" class="hover:text-[var(--color-lavender)]">graph</a>
			<a href="/schedule" class="hover:text-[var(--color-lavender)]">schedule</a>
			<a href="/logs" class="hover:text-[var(--color-lavender)]">logs</a>
		</nav>
		<span class="ml-auto text-xs text-[var(--color-overlay0)] font-mono" id="user-badge">—</span>
	</header>

	<main class="flex-1 p-4">
		{@render children()}
	</main>

	<footer
		class="border-t border-[var(--color-surface0)] px-4 py-1 text-xs text-[var(--color-overlay0)] font-mono flex justify-between bg-[var(--color-mantle)]"
	>
		<span>console · catppuccin mocha</span>
		<span id="status-ping">·</span>
	</footer>
</div>
