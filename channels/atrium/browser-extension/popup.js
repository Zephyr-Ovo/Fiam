const statusBox = document.getElementById("status");
const runtimeSelect = document.getElementById("runtime");
const executeActionButton = document.getElementById("execute-action");
const ext = globalThis.browser || globalThis.chrome;
const DEFAULT_SETTINGS = {
  endpoint: "http://127.0.0.1:8766",
  token: "",
  runtime: "api",
  autoExecuteActions: true,
  autonomousControl: false,
  controlMode: "session",
  autoScreenshot: true,
  controlCooldownMs: 8000,
  controlMaxSteps: 6
};
let pendingBrowserAction = null;

function callApi(target, method, ...args) {
  const fn = target[method].bind(target);
  if (globalThis.browser) return fn(...args);
  return new Promise((resolve, reject) => {
    fn(...args, (result) => {
      const error = ext.runtime.lastError;
      if (error) reject(new Error(error.message));
      else resolve(result);
    });
  });
}

function show(value) {
  statusBox.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function normalizeEndpoint(endpoint) {
  return String(endpoint || DEFAULT_SETTINGS.endpoint).replace(/\/+$/, "");
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function getSettings() {
  return callApi(ext.storage.local, "get", DEFAULT_SETTINGS);
}

async function postJson(path, body, settings) {
  if (!settings.token) throw new Error("Set FIAM_INGEST_TOKEN in extension options first");
  const response = await fetch(`${normalizeEndpoint(settings.endpoint)}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Fiam-Token": settings.token
    },
    body: JSON.stringify(body)
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

async function send(message) {
  const response = await callApi(ext.runtime, "sendMessage", message);
  if (!response || !response.ok) throw new Error(response?.error || "Extension action failed");
  return response.result;
}

async function activeTab() {
  const [tab] = await callApi(ext.tabs, "query", { active: true, currentWindow: true });
  if (!tab || !tab.id) throw new Error("No active tab");
  return tab;
}

async function startPicker(mode) {
  const tab = await activeTab();
  const response = await callApi(ext.tabs, "sendMessage", tab.id, { type: "FIAM_START_PICKER", mode });
  if (!response || !response.ok) throw new Error(response?.error || "Picker did not start");
  return response;
}

async function executeBrowserAction(action) {
  const tab = await activeTab();
  const response = await callApi(ext.tabs, "sendMessage", tab.id, { type: "FIAM_EXECUTE_BROWSER_ACTION", action });
  if (!response || !response.ok) throw new Error(response?.error || "Browser action failed");
  return response.result;
}

async function collectSnapshot(tab) {
  const response = await callApi(ext.tabs, "sendMessage", tab.id, { type: "FIAM_COLLECT_SNAPSHOT" });
  if (!response || !response.ok) throw new Error(response?.error || "Snapshot collection failed");
  return {
    ...response.snapshot,
    tabId: String(tab.id),
    browser: "extension",
    windowId: tab.windowId
  };
}

function actionSignature(action) {
  return [action?.action || "click", action?.nodeId || "", action?.name || "", action?.text || ""].join("|");
}

function snapshotSignature(snapshot) {
  return [
    snapshot?.url || "",
    snapshot?.title || "",
    (snapshot?.headings || []).slice(0, 8).join("|"),
    (snapshot?.textBlocks || []).slice(0, 3).join("|")
  ].join("\n");
}

function shouldAttachScreenshot(snapshot, reason, steps) {
  if (!snapshot) return false;
  const media = snapshot.media || {};
  const imageCount = Number(media.imageCount || 0);
  const textCount = Array.isArray(snapshot.textBlocks) ? snapshot.textBlocks.length : 0;
  const nodeCount = Array.isArray(snapshot.nodes) ? snapshot.nodes.length : 0;
  return imageCount >= 8 && (textCount <= 4 || nodeCount <= 12 || reason === "after_action" || steps > 0);
}

async function captureScreenshot(tab, reason, snapshot) {
  const dataUrl = await callApi(ext.tabs, "captureVisibleTab", tab.windowId, { format: "jpeg", quality: 42 });
  if (!dataUrl || dataUrl.length > 1800000) return null;
  return {
    dataUrl,
    reason,
    viewport: snapshot?.viewport || null
  };
}

async function screenshotForTick(tab, settings, reason, snapshot, steps = 0) {
  if (!settings.autoScreenshot || !shouldAttachScreenshot(snapshot, reason, steps)) return null;
  try {
    return await captureScreenshot(tab, reason, snapshot);
  } catch (_error) {
    return null;
  }
}

function isUnknownMessage(error) {
  const text = String(error instanceof Error ? error.message : error).toLowerCase();
  return text.includes("unknown message") || text.includes("receiving end") || text.includes("could not establish connection");
}

async function runControlTick() {
  try {
    return await send({ type: "FIAM_RUN_CONTROL_TICK", reason: "popup_debug" });
  } catch (error) {
    if (!isUnknownMessage(error)) throw error;
    const tab = await activeTab();
    const settings = await getSettings();
    let currentSnapshot = await collectSnapshot(tab);
    let result = await postJson("/browser/tick", {
      reason: "popup_debug",
      runtime: settings.runtime || "api",
      snapshot: currentSnapshot,
      screenshot: await screenshotForTick(tab, settings, "popup_debug", currentSnapshot, 0)
    }, settings);
    const executed = [];
    const controlTrail = [];
    let lastActionSignature = "";
    let lastSnapshotSignature = "";
    const maxSteps = Math.max(1, Math.min(10, Number(settings.controlMaxSteps || DEFAULT_SETTINGS.controlMaxSteps)));
    for (let index = 0; index < maxSteps; index += 1) {
      if (result.browser_done) {
        result.control_finished = "browser_done";
        break;
      }
      const actions = Array.isArray(result.browser_actions) ? result.browser_actions.slice(0, 1) : [];
      if (!settings.autoExecuteActions || !actions.length) {
        result.control_finished = actions.length ? "awaiting_manual_execution" : "no_action";
        break;
      }
      const action = actions[0];
      const nextActionSignature = actionSignature(action);
      const currentSnapshotSignature = snapshotSignature(currentSnapshot);
      if (nextActionSignature === lastActionSignature && currentSnapshotSignature === lastSnapshotSignature) {
        result.control_finished = "repeat_no_change";
        break;
      }
      const execution = await executeBrowserAction(action);
      await wait(450);
      const afterSnapshot = await collectSnapshot(tab);
      const recorded = await postJson("/browser/action-result", { action, result: execution, snapshot: afterSnapshot }, settings);
      executed.push({ action, result: execution, recorded });
      controlTrail.push({ action: action.action, nodeId: action.nodeId, name: action.name, result: execution.ok ? "ok" : "error" });
      lastActionSignature = nextActionSignature;
      lastSnapshotSignature = snapshotSignature(afterSnapshot);
      currentSnapshot = afterSnapshot;
      result = await postJson("/browser/tick", {
        reason: "after_action",
        runtime: settings.runtime || "api",
        snapshot: afterSnapshot,
        screenshot: await screenshotForTick(tab, settings, "after_action", afterSnapshot, executed.length),
        controlTrail
      }, settings);
    }
    if (executed.length >= maxSteps) result.control_finished = "max_steps";
    result.executed_browser_actions = executed;
    result.browser_actions = [];
    result.control_steps = executed.length;
    return result;
  }
}

function updatePendingAction(result) {
  pendingBrowserAction = result?.executed_browser_actions?.length ? null : (result?.browser_actions || [])[0] || null;
  executeActionButton.hidden = !pendingBrowserAction;
  if (pendingBrowserAction) {
    const label = pendingBrowserAction.name || pendingBrowserAction.nodeId || "目标控件";
    executeActionButton.textContent = `执行建议动作：${pendingBrowserAction.action} ${label}`;
  }
}

callApi(ext.storage.local, "get", { runtime: "api" }).then((settings) => {
  runtimeSelect.value = settings.runtime || "api";
});

document.getElementById("options").addEventListener("click", () => ext.runtime.openOptionsPage());

document.getElementById("pick-keep").addEventListener("click", async () => {
  show("回到页面，点击要保留的控件；点错了在列表里删除。");
  try {
    await startPicker("keep");
    window.close();
  } catch (error) {
    show(error instanceof Error ? error.message : String(error));
  }
});

document.getElementById("capture").addEventListener("click", async () => {
  updatePendingAction(null);
  show("Capturing...");
  try {
    show(await send({ type: "FIAM_CAPTURE_PAGE" }));
  } catch (error) {
    show(error instanceof Error ? error.message : String(error));
  }
});

document.getElementById("autonomous-session").addEventListener("click", async () => {
  updatePendingAction(null);
  show("Starting autonomous session...");
  try {
    const result = await send({ type: "FIAM_START_AUTONOMOUS_SESSION" });
    updatePendingAction(result?.result || result);
    show(result);
  } catch (error) {
    show(error instanceof Error ? error.message : String(error));
  }
});

document.getElementById("control-tick").addEventListener("click", async () => {
  updatePendingAction(null);
  show("Running AI control tick...");
  try {
    const result = await runControlTick();
    updatePendingAction(result);
    show(result);
  } catch (error) {
    show(error instanceof Error ? error.message : String(error));
  }
});

executeActionButton.addEventListener("click", async () => {
  if (!pendingBrowserAction) return;
  show("Executing action...");
  try {
    show(await executeBrowserAction(pendingBrowserAction));
    updatePendingAction(null);
  } catch (error) {
    show(error instanceof Error ? error.message : String(error));
  }
});

document.getElementById("ask").addEventListener("click", async () => {
  const question = document.getElementById("question").value.trim();
  updatePendingAction(null);
  show("Asking...");
  try {
    const runtime = runtimeSelect.value;
    await callApi(ext.storage.local, "set", { runtime });
    const result = await send({ type: "FIAM_ASK_PAGE", question, runtime });
    updatePendingAction(result);
    show(result);
  } catch (error) {
    show(error instanceof Error ? error.message : String(error));
  }
});