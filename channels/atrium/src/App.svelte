<script lang="ts">
  import { invoke } from "@tauri-apps/api/core"
  import { listen } from "@tauri-apps/api/event"
  import { onMount } from "svelte"

  type CapabilityView = {
    name: string
    kind: "sensor" | "actuator" | string
    trust: number
    enabled: boolean
    description: string
  }

  type StatusView = {
    paused: boolean
    panic: boolean
    capabilityCount: number
    enabledCount: number
    actuatorCount: number
    sensorCount: number
    auditPath: string
    mqtt: MqttView
    mitm: MitmView
    proxy: ProxyView
    cert: CertView
    interceptCount: number
    pendingCount: number
  }

  type MqttView = {
    enabled: boolean
    connected: boolean
    status: string
    host: string
    port: number
    dispatchTopic: string
    resultTopic: string
    received: number
    published: number
    lastError?: string | null
  }

  type DispatchResult = {
    id: string
    ok: boolean
    status: string
    auditId: string
    capability?: string
    trust?: number
    kind?: string
    result?: unknown
  }

  type MitmView = {
    running: boolean
    pid?: number | null
    port: number
    ruleCount: number
    rulesPath: string
    scriptPath: string
    mitmdumpPath: string
    lastError?: string | null
  }

  type ProxyView = {
    enabled: boolean
    server?: string | null
    overrideList?: string | null
    autoConfigUrl?: string | null
    snapshotPath: string
    hasSnapshot: boolean
    lastError?: string | null
  }

  type CertView = {
    available: boolean
    path: string
    lastError?: string | null
  }

  type PendingView = {
    id: string
    capability: string
    trust: number
    kind: string
    reason?: string | null
    source: string
    createdMs: number
    payload: Record<string, unknown>
  }

  type InterceptHit = {
    id: string
    tsMs: number
    ruleId: string
    host: string
    path: string
    method: string
    url: string
    reason?: string | null
  }

  type WindowInfo = {
    hwnd: number
    pid: number
    title: string
    visible: boolean
    focused: boolean
  }

  type ProcessInfo = {
    pid: number
    parentPid: number
    exe: string
  }

  let status: StatusView | null = null
  let capabilities: CapabilityView[] = []
  let pending: PendingView[] = []
  let intercepts: InterceptHit[] = []
  let windows: WindowInfo[] = []
  let processes: ProcessInfo[] = []
  let selectedCapability = "notify"
  let dispatchId = crypto.randomUUID()
  let reason = "M1 bus check"
  let selectedInterceptId = ""
  let releaseReason = "earned release"
  let releaseMinutes = 10
  let readerText = ""
  let readerDraft = "A shared note for the reader surface."
  let browserUrl = "https://example.com"
  let browserSelector = "input, textarea"
  let browserText = "Atrium controlled input"
  let nativeHwnd = ""
  let nativeCoordinateX = 8
  let nativeCoordinateY = 8
  let nativeText = "Atrium text"
  let result: DispatchResult | null = null
  let busy = false
  let error = ""
  const surface = new URLSearchParams(location.search).get("surface")

  $: sensors = capabilities.filter((capability) => capability.kind === "sensor")
  $: actuators = capabilities.filter((capability) => capability.kind === "actuator")
  $: blocked = Boolean(status?.paused || status?.panic)
  $: enabled = capabilities.filter((capability) => capability.enabled)
  $: mqtt = status?.mqtt
  $: mitm = status?.mitm
  $: proxy = status?.proxy
  $: cert = status?.cert
  $: selectedIntercept = intercepts.find((hit) => hit.id === selectedInterceptId) ?? intercepts[0]

  function trustLabel(trust: number) {
    if (trust === 0) return "silent"
    if (trust === 1) return "toast"
    if (trust === 2) return "confirm"
    return "confirm+reason"
  }

  async function refresh() {
    error = ""
    const [nextStatus, nextCapabilities] = await Promise.all([
      invoke<StatusView>("get_status"),
      invoke<CapabilityView[]>("list_capabilities"),
    ])
    status = nextStatus
    capabilities = nextCapabilities
    ;[pending, intercepts] = await Promise.all([
      invoke<PendingView[]>("list_pending"),
      invoke<InterceptHit[]>("list_intercepts"),
    ])
    if (!capabilities.some((capability) => capability.name === selectedCapability)) {
      selectedCapability = capabilities[0]?.name ?? ""
    }
  }

  async function runAction(action: () => Promise<void>) {
    busy = true
    error = ""
    try {
      await action()
      await refresh()
    } catch (caught) {
      error = caught instanceof Error ? caught.message : String(caught)
    } finally {
      busy = false
    }
  }

  async function dryRun() {
    await runAction(async () => {
      result = await invoke<DispatchResult>("dry_run_dispatch", {
        request: {
          id: dispatchId,
          capability: selectedCapability,
          reason,
          payload: {},
        },
      })
      dispatchId = crypto.randomUUID()
    })
  }

  async function dispatchOnce() {
    await runAction(async () => {
      result = await invoke<DispatchResult>("dispatch_once", {
        request: {
          id: dispatchId,
          capability: selectedCapability,
          reason,
          payload: {},
        },
      })
      dispatchId = crypto.randomUUID()
    })
  }

  async function setMitmRunning(running: boolean) {
    await runAction(async () => {
      status = await invoke<StatusView>("set_mitm_running", { running })
    })
  }

  async function dispatchProxy(capability: "proxy.system.apply" | "proxy.system.restore") {
    await runAction(async () => {
      result = await invoke<DispatchResult>("dispatch_once", {
        request: {
          id: dispatchId,
          capability,
          reason: "ui proxy",
          payload: {},
        },
      })
      dispatchId = crypto.randomUUID()
    })
  }

  async function dispatchCertInstall() {
    await runAction(async () => {
      result = await invoke<DispatchResult>("dispatch_once", {
        request: {
          id: dispatchId,
          capability: "mitm.cert.install",
          reason: "ui cert",
          payload: {},
        },
      })
      dispatchId = crypto.randomUUID()
    })
  }

  async function releaseIntercept(ruleId: string) {
    await runAction(async () => {
      result = await invoke<DispatchResult>("dispatch_once", {
        request: {
          id: dispatchId,
          capability: "web.intercept.release",
          reason: releaseReason,
          payload: { id: ruleId, minutes: releaseMinutes },
        },
      })
      dispatchId = crypto.randomUUID()
    })
  }

  async function clearIntercepts() {
    await runAction(async () => {
      await invoke("clear_intercepts")
    })
  }

  async function loadInventory() {
    await runAction(async () => {
      ;[windows, processes] = await Promise.all([
        invoke<WindowInfo[]>("list_windows"),
        invoke<ProcessInfo[]>("list_processes"),
      ])
    })
  }

  async function openReader() {
    await runAction(async () => {
      result = await invoke<DispatchResult>("dispatch_once", {
        request: {
          id: dispatchId,
          capability: "app.spawn",
          reason: "ui surface",
          payload: { kind: "reader", title: "Atrium Reader" },
        },
      })
      dispatchId = crypto.randomUUID()
    })
  }

  async function sendReaderText(mode: "reader.set_text" | "reader.append_text") {
    await runAction(async () => {
      result = await invoke<DispatchResult>("dispatch_once", {
        request: {
          id: dispatchId,
          capability: mode,
          reason: "ui reader",
          payload: { text: readerDraft },
        },
      })
      dispatchId = crypto.randomUUID()
    })
  }

  async function openBrowser() {
    await runAction(async () => {
      result = await invoke<DispatchResult>("dispatch_once", {
        request: {
          id: dispatchId,
          capability: "web.surface.open",
          reason: "ui browser surface",
          payload: { url: browserUrl, title: "Atrium Browser" },
        },
      })
      dispatchId = crypto.randomUUID()
    })
  }

  async function navigateBrowser() {
    await runAction(async () => {
      result = await invoke<DispatchResult>("dispatch_once", {
        request: {
          id: dispatchId,
          capability: "web.surface.navigate",
          reason: "ui browser surface",
          payload: { url: browserUrl },
        },
      })
      dispatchId = crypto.randomUUID()
    })
  }

  async function runBrowserAction(capability: "web.cdp.click" | "web.cdp.type" | "web.cdp.scroll") {
    const payload = capability === "web.cdp.scroll"
      ? { y: 640 }
      : capability === "web.cdp.type"
        ? { selector: browserSelector, text: browserText }
        : { selector: browserSelector }
    await runAction(async () => {
      result = await invoke<DispatchResult>("dispatch_once", {
        request: {
          id: dispatchId,
          capability,
          reason: "ui browser action",
          payload,
        },
      })
      dispatchId = crypto.randomUUID()
    })
  }

  async function runNativeAction(capability: "window.win32.control_click" | "window.win32.set_text") {
    const hwnd = Number(nativeHwnd)
    const payload = capability === "window.win32.set_text"
      ? { hwnd, text: nativeText }
      : { hwnd, x: nativeCoordinateX, y: nativeCoordinateY }
    await runAction(async () => {
      result = await invoke<DispatchResult>("dispatch_once", {
        request: {
          id: dispatchId,
          capability,
          reason: "ui native lane",
          payload,
        },
      })
      dispatchId = crypto.randomUUID()
    })
  }

  async function resolvePending(id: string, approved: boolean) {
    await runAction(async () => {
      result = approved
        ? await invoke<DispatchResult>("approve_pending", { id })
        : await invoke<DispatchResult>("deny_pending", { id, reason: "ui" })
    })
  }

  onMount(() => {
    if (surface === "reader") {
      const setListener = listen<{ text: string }>("reader:set_text", (event) => {
        readerText = event.payload.text
      })
      const appendListener = listen<{ text: string }>("reader:append_text", (event) => {
        readerText = `${readerText}${readerText ? "\n" : ""}${event.payload.text}`
      })
      return () => {
        setListener.then((unlisten) => unlisten())
        appendListener.then((unlisten) => unlisten())
      }
    }
    refresh().catch((caught) => {
      error = caught instanceof Error ? caught.message : String(caught)
    })
  })
</script>

{#if surface === "reader"}
  <main class="reader-shell">
    <header class="reader-toolbar">
      <strong>Atrium Reader</strong>
      <span>local</span>
    </header>
    <section class="reader-page">
      <textarea bind:value={readerText} aria-label="Reader text"></textarea>
    </section>
  </main>
{:else}
<main class="shell">
  <section class="topbar">
    <div>
      <p class="eyebrow">Atrium</p>
      <h1>Desktop Host</h1>
    </div>
    <div class="status-strip" aria-label="Atrium status">
      <span class:bad={blocked}>{status?.panic ? "panic" : status?.paused ? "paused" : "ready"}</span>
      <span class:bad={mqtt && !mqtt.connected}>mqtt {mqtt?.status ?? "idle"}</span>
      <span class:bad={mitm?.lastError}>{mitm?.running ? "mitm on" : "mitm off"}</span>
      <span class:bad={proxy?.lastError}>{proxy?.enabled ? "proxy on" : "proxy off"}</span>
      <span class:bad={!cert?.available}>cert {cert?.available ? "ready" : "missing"}</span>
      <span class:bad={intercepts.length > 0}>{status?.interceptCount ?? 0} hits</span>
      <span class:bad={pending.length > 0}>{status?.pendingCount ?? 0} pending</span>
      <span>{status?.enabledCount ?? 0}/{status?.capabilityCount ?? 0} enabled</span>
      <span>{status?.actuatorCount ?? 0} actuators</span>
      <span>{status?.sensorCount ?? 0} sensors</span>
    </div>
  </section>

  <section class="control-band">
    <div class="switches">
      <button disabled={busy || !status} class:active={status?.paused} on:click={() => runAction(() => invoke("set_paused", { paused: !status?.paused, reason: "ui" }))}>
        {status?.paused ? "Resume" : "Pause"}
      </button>
      <button disabled={busy || status?.panic} class="danger" on:click={() => runAction(() => invoke("panic_stop", { reason: "ui" }))}>
        Panic
      </button>
      <button disabled={busy || !status?.panic} on:click={() => runAction(() => invoke("clear_panic", { reason: "ui" }))}>
        Clear
      </button>
      <button disabled={busy} on:click={() => runAction(() => invoke("reload_capabilities"))}>
        Reload
      </button>
    </div>
    <div class="audit-path">{status?.auditPath ?? "audit.jsonl"}</div>
  </section>

  {#if error}
    <section class="banner">{error}</section>
  {/if}

  <section class="grid">
    <div class="panel wide">
      <div class="panel-head">
        <h2>Capabilities</h2>
        <span>{enabled.length} active</span>
      </div>
      <div class="cap-table">
        <div class="cap-row cap-row-head">
          <span>Name</span>
          <span>Kind</span>
          <span>Trust</span>
          <span>State</span>
        </div>
        {#each capabilities as capability}
          <button class="cap-row" class:selected={capability.name === selectedCapability} on:click={() => (selectedCapability = capability.name)}>
            <span>{capability.name}</span>
            <span>{capability.kind}</span>
            <span>{capability.trust} · {trustLabel(capability.trust)}</span>
            <span class:off={!capability.enabled}>{capability.enabled ? "enabled" : "disabled"}</span>
          </button>
        {/each}
      </div>
    </div>

    <div class="panel">
      <div class="panel-head">
        <h2>Dispatch</h2>
        <span>{blocked ? "blocked" : "armed"}</span>
      </div>
      <label>
        Capability
        <select bind:value={selectedCapability}>
          {#each capabilities as capability}
            <option value={capability.name}>{capability.name}</option>
          {/each}
        </select>
      </label>
      <label>
        Reason
        <input bind:value={reason} />
      </label>
      <button class="primary" disabled={busy || !selectedCapability} on:click={dryRun}>Dry Run</button>
      <button disabled={busy || !selectedCapability} on:click={dispatchOnce}>Dispatch</button>
      {#if result}
        <div class="result" class:ok={result.ok}>
          <strong>{result.status}</strong>
          <span>{result.auditId}</span>
        </div>
      {/if}
    </div>

    <div class="panel">
      <div class="panel-head">
        <h2>MQTT Bus</h2>
        <span class:bad={mqtt && !mqtt.connected}>{mqtt?.connected ? "online" : mqtt?.status ?? "idle"}</span>
      </div>
      <div class="bus-grid">
        <span>Broker</span>
        <strong>{mqtt ? `${mqtt.host}:${mqtt.port}` : "127.0.0.1:1883"}</strong>
        <span>Dispatch</span>
        <strong>{mqtt?.dispatchTopic ?? "fiam/dispatch/desktop"}</strong>
        <span>Result</span>
        <strong>{mqtt?.resultTopic ?? "fiam/receive/desktop/result"}</strong>
        <span>Traffic</span>
        <strong>{mqtt?.received ?? 0} in · {mqtt?.published ?? 0} out</strong>
      </div>
      {#if mqtt?.lastError}
        <div class="result">{mqtt.lastError}</div>
      {/if}
    </div>

    <div class="panel">
      <div class="panel-head">
        <h2>Trust Gate</h2>
        <span class:bad={pending.length > 0}>{pending.length}</span>
      </div>
      {#if pending.length === 0}
        <p class="mini">No pending requests</p>
      {:else}
        <div class="pending-list">
          {#each pending as item}
            <div class="pending-item">
              <strong>{item.capability}</strong>
              <span>trust {item.trust} · {item.reason ?? item.source}</span>
              <code>{JSON.stringify(item.payload)}</code>
              <div class="pending-actions">
                <button disabled={busy} on:click={() => resolvePending(item.id, false)}>Deny</button>
                <button class="primary" disabled={busy} on:click={() => resolvePending(item.id, true)}>Approve</button>
              </div>
            </div>
          {/each}
        </div>
      {/if}
    </div>

    <div class="panel">
      <div class="panel-head">
        <h2>Intercepts</h2>
        <span class:bad={intercepts.length > 0}>{intercepts.length}</span>
      </div>
      {#if intercepts.length === 0}
        <p class="mini">No intercepted requests</p>
      {:else}
        <div class="pending-list">
          {#each intercepts as hit}
            <button class="pending-item clickable" class:selected={selectedIntercept?.id === hit.id} on:click={() => (selectedInterceptId = hit.id)}>
              <strong>{hit.host}</strong>
              <span>{hit.method} · {hit.path}</span>
              <code>{hit.ruleId || hit.id}</code>
            </button>
          {/each}
        </div>
      {/if}
    </div>

    <div class="panel">
      <div class="panel-head">
        <h2>Release Dialog</h2>
        <span>{selectedIntercept?.ruleId ?? "none"}</span>
      </div>
      {#if selectedIntercept}
        <div class="dialog-target">
          <strong>{selectedIntercept.host}</strong>
          <span>{selectedIntercept.path}</span>
        </div>
        <label>
          Reply
          <input bind:value={releaseReason} />
        </label>
        <label>
          Minutes
          <input type="number" min="1" max="240" bind:value={releaseMinutes} />
        </label>
        <div class="pending-actions">
          <button disabled={busy} on:click={clearIntercepts}>Clear Hits</button>
          <button class="primary" disabled={busy || !selectedIntercept.ruleId} on:click={() => releaseIntercept(selectedIntercept.ruleId)}>Release</button>
        </div>
      {:else}
        <p class="mini">No intercepted request selected</p>
      {/if}
    </div>

    <div class="panel">
      <div class="panel-head">
        <h2>mitmproxy</h2>
        <span class:bad={mitm?.lastError}>{mitm?.running ? `pid ${mitm.pid}` : "stopped"}</span>
      </div>
      <div class="bus-grid">
        <span>Port</span>
        <strong>{mitm?.port ?? 8088}</strong>
        <span>Rules</span>
        <strong>{mitm?.ruleCount ?? 0}</strong>
        <span>Script</span>
        <strong>{mitm?.scriptPath ?? "tools/mitmproxy/rules.py"}</strong>
        <span>Binary</span>
        <strong>{mitm?.mitmdumpPath ?? "mitmdump"}</strong>
        <span>File</span>
        <strong>{mitm?.rulesPath ?? "tools/mitmproxy/rules.json"}</strong>
      </div>
      <button class="primary spaced" disabled={busy} on:click={() => setMitmRunning(!mitm?.running)}>
        {mitm?.running ? "Stop" : "Start"}
      </button>
      {#if mitm?.lastError}
        <div class="result">{mitm.lastError}</div>
      {/if}
    </div>

    <div class="panel">
      <div class="panel-head">
        <h2>Proxy</h2>
        <span class:bad={proxy?.lastError}>{proxy?.enabled ? "enabled" : "disabled"}</span>
      </div>
      <div class="bus-grid">
        <span>Server</span>
        <strong>{proxy?.server ?? "none"}</strong>
        <span>Override</span>
        <strong>{proxy?.overrideList ?? "none"}</strong>
        <span>Snapshot</span>
        <strong>{proxy?.hasSnapshot ? proxy.snapshotPath : "none"}</strong>
      </div>
      <div class="pending-actions spaced">
        <button disabled={busy} on:click={() => dispatchProxy("proxy.system.restore")}>Restore</button>
        <button class="primary" disabled={busy} on:click={() => dispatchProxy("proxy.system.apply")}>Apply</button>
      </div>
      {#if proxy?.lastError}
        <div class="result">{proxy.lastError}</div>
      {/if}
    </div>

    <div class="panel">
      <div class="panel-head">
        <h2>Certificate</h2>
        <span class:bad={!cert?.available}>{cert?.available ? "available" : "missing"}</span>
      </div>
      <div class="bus-grid">
        <span>Path</span>
        <strong>{cert?.path ?? "~/.mitmproxy/mitmproxy-ca-cert.cer"}</strong>
      </div>
      <button class="primary spaced" disabled={busy || !cert?.available} on:click={dispatchCertInstall}>Open</button>
      {#if cert?.lastError}
        <div class="result">{cert.lastError}</div>
      {/if}
    </div>

    <div class="panel">
      <div class="panel-head">
        <h2>Surfaces</h2>
        <span>owned</span>
      </div>
      <button class="primary" disabled={busy} on:click={openReader}>Open Reader</button>
      <label class="spaced">
        Text
        <input bind:value={readerDraft} />
      </label>
      <div class="pending-actions">
        <button disabled={busy} on:click={() => sendReaderText("reader.append_text")}>Append</button>
        <button class="primary" disabled={busy} on:click={() => sendReaderText("reader.set_text")}>Set</button>
      </div>
      <label class="spaced">
        URL
        <input bind:value={browserUrl} />
      </label>
      <div class="pending-actions">
        <button disabled={busy} on:click={navigateBrowser}>Navigate</button>
        <button class="primary" disabled={busy} on:click={openBrowser}>Open Browser</button>
      </div>
      <label class="spaced">
        Selector
        <input bind:value={browserSelector} />
      </label>
      <label>
        Text
        <input bind:value={browserText} />
      </label>
      <div class="pending-actions">
        <button disabled={busy} on:click={() => runBrowserAction("web.cdp.scroll")}>Scroll</button>
        <button disabled={busy} on:click={() => runBrowserAction("web.cdp.click")}>Click</button>
        <button class="primary" disabled={busy} on:click={() => runBrowserAction("web.cdp.type")}>Type</button>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head">
        <h2>Native Lane</h2>
        <span>trust 2</span>
      </div>
      <label>
        HWND
        <input bind:value={nativeHwnd} inputmode="numeric" />
      </label>
      <div class="split tight">
        <label>
          X
          <input type="number" bind:value={nativeCoordinateX} />
        </label>
        <label>
          Y
          <input type="number" bind:value={nativeCoordinateY} />
        </label>
      </div>
      <label>
        Text
        <input bind:value={nativeText} />
      </label>
      <div class="pending-actions">
        <button disabled={busy || !nativeHwnd} on:click={() => runNativeAction("window.win32.control_click")}>Click</button>
        <button class="primary" disabled={busy || !nativeHwnd} on:click={() => runNativeAction("window.win32.set_text")}>Set Text</button>
      </div>
    </div>

    <div class="panel split inventory-panel">
      <div>
        <div class="panel-head">
          <h2>Windows</h2>
          <span>{windows.length}</span>
        </div>
        {#each windows.slice(0, 8) as item}
          <p class="mini" class:active-text={item.focused}>{item.hwnd} · {item.title}</p>
        {/each}
      </div>
      <div>
        <div class="panel-head">
          <h2>Processes</h2>
          <span>{processes.length}</span>
        </div>
        {#each processes.slice(0, 8) as item}
          <p class="mini">{item.exe} · {item.pid}</p>
        {/each}
      </div>
      <button class="primary inventory-refresh" disabled={busy} on:click={loadInventory}>Refresh</button>
    </div>

    <div class="panel split">
      <div>
        <div class="panel-head">
          <h2>Sensors</h2>
          <span>{sensors.length}</span>
        </div>
        {#each sensors as capability}
          <p class="mini">{capability.name}</p>
        {/each}
      </div>
      <div>
        <div class="panel-head">
          <h2>Actuators</h2>
          <span>{actuators.length}</span>
        </div>
        {#each actuators as capability}
          <p class="mini" class:off={!capability.enabled}>{capability.name}</p>
        {/each}
      </div>
    </div>
  </section>
</main>
{/if}
