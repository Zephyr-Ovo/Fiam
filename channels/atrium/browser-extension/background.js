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
const PROFILE_RULES_KEY = "fiamProfileRules";

const ext = globalThis.browser || globalThis.chrome;
const controlLocks = new Map();
const lastControlAt = new Map();

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

function getSettings() {
  return callApi(ext.storage.local, "get", DEFAULT_SETTINGS);
}

function normalizeEndpoint(endpoint) {
  return String(endpoint || DEFAULT_SETTINGS.endpoint).replace(/\/+$/, "");
}

function hostForUrl(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch (_error) {
    return "";
  }
}

function profileForHost(host, rulesByHost) {
  if (!host || !rulesByHost || typeof rulesByHost !== "object") return null;
  const direct = rulesByHost[host] || rulesByHost[`www.${host}`];
  if (direct) return direct;
  for (const [key, profile] of Object.entries(rulesByHost)) {
    if (host === key || host.endsWith(`.${key}`)) return profile;
  }
  return null;
}

async function getRulesByHost() {
  const stored = await callApi(ext.storage.local, "get", { [PROFILE_RULES_KEY]: {} });
  const rules = stored[PROFILE_RULES_KEY];
  return rules && typeof rules === "object" ? rules : {};
}

async function saveProfileRule(url, mode, rule) {
  return saveProfileRules(url, mode, [rule]);
}

async function saveProfileRules(url, mode, rules) {
  const host = hostForUrl(url);
  if (!host) throw new Error("Cannot save rule without page host");
  const rulesByHost = await getRulesByHost();
  const profile = rulesByHost[host] || {
    id: `user:${host}`,
    hosts: [host, `www.${host}`],
    maxNodes: 18,
    strictKeepContextFallback: true,
    keep: [],
    suppress: [],
    groups: { manual_hidden: "manually hidden elements" }
  };
  const bucket = mode === "suppress" ? "suppress" : "keep";
  if (bucket === "keep") profile.strictKeep = true;
  const list = Array.isArray(profile[bucket]) ? profile[bucket] : [];
  for (const rule of rules) {
    if (!rule || typeof rule !== "object") continue;
    const key = JSON.stringify(rule);
    if (!list.some((item) => JSON.stringify(item) === key)) list.push(rule);
  }
  profile[bucket] = list;
  rulesByHost[host] = profile;
  await callApi(ext.storage.local, "set", { [PROFILE_RULES_KEY]: rulesByHost });
  return {
    host,
    profileId: profile.id,
    keep: Array.isArray(profile.keep) ? profile.keep.length : 0,
    suppress: Array.isArray(profile.suppress) ? profile.suppress.length : 0
  };
}

async function profileRulesForUrl(url) {
  const profile = profileForHost(hostForUrl(url), await getRulesByHost());
  return profile || null;
}

async function activeTab() {
  const [tab] = await callApi(ext.tabs, "query", { active: true, currentWindow: true });
  if (!tab || !tab.id) throw new Error("No active tab");
  return tab;
}

async function collectSnapshot(tab) {
  const response = await callApi(ext.tabs, "sendMessage", tab.id, { type: "FIAM_COLLECT_SNAPSHOT" });
  if (!response || !response.ok) throw new Error("Snapshot collection failed");
  const profileRules = response.snapshot?.profileRules || await profileRulesForUrl(response.snapshot?.url || tab.url || "");
  return {
    ...response.snapshot,
    profileRules,
    tabId: String(tab.id),
    browser: "extension",
    windowId: tab.windowId
  };
}

async function startPicker(mode) {
  const tab = await activeTab();
  return callApi(ext.tabs, "sendMessage", tab.id, { type: "FIAM_START_PICKER", mode });
}

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function executeBrowserAction(tab, action) {
  if (action && String(action.action || "").toLowerCase() === "goto") {
    const targetUrl = String(action.url || "").trim();
    if (!/^https?:\/\//i.test(targetUrl)) throw new Error("goto requires http(s) url");
    await callApi(ext.tabs, "update", tab.id, { url: targetUrl });
    const ready = await waitForTabReady(tab.id);
    return { ok: true, action: "goto", nodeId: "", label: ready?.url || targetUrl };
  }
  const response = await callApi(ext.tabs, "sendMessage", tab.id, { type: "FIAM_EXECUTE_BROWSER_ACTION", action });
  if (!response || !response.ok) throw new Error(response?.error || "Browser action failed");
  return response.result;
}

async function recordBrowserAction(action, result, snapshot) {
  return postJson("/browser/action-result", { action, result, snapshot });
}

async function executeReturnedActions(tab, actions, maxActions) {
  const executed = [];
  for (const action of actions.slice(0, maxActions)) {
    const execution = await executeBrowserAction(tab, action);
    await wait(450);
    const afterSnapshot = await collectSnapshot(tab);
    const recorded = await recordBrowserAction(action, execution, afterSnapshot);
    executed.push({ action, result: execution, recorded });
  }
  return executed;
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
  } catch (error) {
    console.warn("Fiam screenshot capture failed", error);
    return null;
  }
}

function shouldExtractVideoFrames(snapshot) {
  if (!snapshot) return false;
  const media = snapshot.media || {};
  const videoCount = Number(media.videoCount || 0);
  if (videoCount < 1) return false;
  const textCount = Array.isArray(snapshot.textBlocks) ? snapshot.textBlocks.length : 0;
  return textCount <= 12;
}

async function videoFramesForTick(tab, snapshot) {
  if (!shouldExtractVideoFrames(snapshot)) return null;
  try {
    const resp = await callApi(ext.tabs, "sendMessage", tab.id, { type: "FIAM_CAPTURE_VIDEO_FRAMES", maxFrames: 3 });
    if (resp && resp.ok && Array.isArray(resp.frames) && resp.frames.length) return resp.frames;
  } catch (error) {
    console.warn("Fiam video frame capture failed", error);
  }
  return null;
}

async function runControlLoop(tab, firstResult, settings, firstSnapshot) {
  const steps = [];
  const controlTrail = [];
  let result = firstResult;
  let currentSnapshot = firstSnapshot;
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
    const execution = await executeBrowserAction(tab, action);
    await wait(450);
    const afterSnapshot = await collectSnapshot(tab);
    const recorded = await recordBrowserAction(action, execution, afterSnapshot);
    steps.push({ action, result: execution, recorded });
    controlTrail.push({ action: action.action, nodeId: action.nodeId, name: action.name, result: execution.ok ? "ok" : "error" });
    lastActionSignature = nextActionSignature;
    lastSnapshotSignature = snapshotSignature(afterSnapshot);
    currentSnapshot = afterSnapshot;
    result = await postJson("/browser/tick", {
      reason: "after_action",
      runtime: settings.runtime || "api",
      snapshot: afterSnapshot,
      screenshot: await screenshotForTick(tab, settings, "after_action", afterSnapshot, steps.length),
      videoFrames: await videoFramesForTick(tab, afterSnapshot),
      controlTrail
    });
  }
  if (steps.length >= maxSteps) result.control_finished = "max_steps";
  result.executed_browser_actions = steps;
  result.browser_actions = [];
  result.control_steps = steps.length;
  return result;
}

async function setSessionBadge(text) {
  if (!ext.browserAction?.setBadgeText) return;
  try {
    await callApi(ext.browserAction, "setBadgeText", { text });
    if (text && ext.browserAction.setBadgeBackgroundColor) {
      await callApi(ext.browserAction, "setBadgeBackgroundColor", { color: "#4f7cff" });
    }
  } catch (_error) {}
}

async function tabFromWindow(windowInfo) {
  if (Array.isArray(windowInfo?.tabs) && windowInfo.tabs.length) return windowInfo.tabs[0];
  if (windowInfo?.id && ext.tabs?.query) {
    const tabs = await callApi(ext.tabs, "query", { windowId: windowInfo.id });
    if (tabs?.length) return tabs[0];
  }
  throw new Error("No tab in autonomous window");
}

async function waitForTabReady(tabId) {
  let tab = await callApi(ext.tabs, "get", tabId);
  for (let index = 0; index < 40; index += 1) {
    if (tab?.status === "complete" && canControlTab(tab)) {
      await wait(700);
      return tab;
    }
    await wait(250);
    tab = await callApi(ext.tabs, "get", tabId);
  }
  return tab;
}

async function startAutonomousSession(startUrl) {
  const settings = await getSettings();
  if (!settings.token) throw new Error("Set FIAM_INGEST_TOKEN in extension options first");
  const sourceTab = await activeTab().catch(() => null);
  let url = String(startUrl || sourceTab?.url || "").trim();
  if (!/^https?:\/\//i.test(url)) {
    // No usable starting page — open a neutral landing so AI can goto.
    url = "https://www.google.com/";
  }
  await setSessionBadge("AI");
  try {
    // Open AI-controlled window in background to avoid stealing user focus.
    // Note: kept as state:"normal" (not minimized) so screenshots and video frames render.
    const windowInfo = await callApi(ext.windows, "create", {
      url,
      type: "normal",
      focused: false
    });
    const createdTab = await tabFromWindow(windowInfo);
    const tab = await waitForTabReady(createdTab.id);
    const result = await controlTick(tab, "autonomous_session_start", true);
    result.autonomous_session = { windowId: tab.windowId, tabId: tab.id, url: tab.url || url };
    return result;
  } finally {
    await setSessionBadge("");
  }
}

async function postJson(path, body) {
  const settings = await getSettings();
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

async function getJson(path) {
  const settings = await getSettings();
  if (!settings.token) throw new Error("Set FIAM_INGEST_TOKEN in extension options first");
  const response = await fetch(`${normalizeEndpoint(settings.endpoint)}${path}`, {
    method: "GET",
    headers: { "X-Fiam-Token": settings.token }
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

let wakeupPollTimer = null;
let wakeupInflight = false;
async function pollWakeupOnce() {
  if (wakeupInflight) return;
  wakeupInflight = true;
  try {
    const settings = await getSettings();
    if (!settings.token) return;
    const data = await getJson("/browser/wakeup");
    const items = Array.isArray(data?.items) ? data.items : [];
    for (const item of items) {
      try {
        await startAutonomousSession(String(item.url || ""));
      } catch (error) {
        console.warn("Fiam wakeup failed", item, error);
      }
    }
  } catch (error) {
    // Silent — server may be down. Don't spam.
  } finally {
    wakeupInflight = false;
  }
}
function startWakeupPolling() {
  if (wakeupPollTimer) return;
  wakeupPollTimer = setInterval(pollWakeupOnce, 5000);
  pollWakeupOnce();
}
startWakeupPolling();

async function capturePage() {
  const tab = await activeTab();
  const snapshot = await collectSnapshot(tab);
  return postJson("/browser/snapshot", { snapshot });
}

async function askPage(question, runtime) {
  const tab = await activeTab();
  const snapshot = await collectSnapshot(tab);
  const settings = await getSettings();
  const result = await postJson("/browser/ask", {
    question,
    runtime: runtime || settings.runtime || "api",
    snapshot
  });
  const actions = Array.isArray(result.browser_actions) ? result.browser_actions.slice(0, 3) : [];
  if (!settings.autoExecuteActions || !actions.length) return result;
  const executed = await executeReturnedActions(tab, actions, 3);
  result.executed_browser_actions = executed;
  result.browser_actions = [];
  return result;
}

function tabKey(tab) {
  return String(tab?.id || "active");
}

function canControlTab(tab) {
  return Boolean(tab?.id && /^https?:\/\//i.test(String(tab.url || "")));
}

async function controlTick(tab, reason, force = false) {
  if (!canControlTab(tab)) return { skipped: true, reason: "unsupported_tab" };
  const settings = await getSettings();
  if (!force && settings.controlMode !== "autonomous") return { skipped: true, reason: "ask_do_mode" };
  if (!force && !settings.autonomousControl) return { skipped: true, reason: "disabled" };
  if (!settings.token) return { skipped: true, reason: "missing_token" };
  const key = tabKey(tab);
  const now = Date.now();
  const cooldown = Number(settings.controlCooldownMs || DEFAULT_SETTINGS.controlCooldownMs);
  if (!force && now - Number(lastControlAt.get(key) || 0) < cooldown) return { skipped: true, reason: "cooldown" };
  if (controlLocks.get(key)) return { skipped: true, reason: "busy" };
  controlLocks.set(key, true);
  lastControlAt.set(key, now);
  try {
    const snapshot = await collectSnapshot(tab);
    const result = await postJson("/browser/tick", {
      reason: reason || "page_changed",
      runtime: settings.runtime || "api",
      snapshot,
      screenshot: await screenshotForTick(tab, settings, reason || "page_changed", snapshot, 0),
      videoFrames: await videoFramesForTick(tab, snapshot)
    });
    return runControlLoop(tab, result, settings, snapshot);
  } finally {
    controlLocks.delete(key);
  }
}

function queueControlTick(tab, reason, force = false) {
  controlTick(tab, reason, force).catch((error) => {
    console.warn("Fiam browser control tick failed", error);
  });
}

if (ext.tabs?.onUpdated) {
  ext.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
    if (changeInfo.status === "complete") queueControlTick({ ...tab, id: tabId }, "tab_complete");
  });
}

if (ext.tabs?.onActivated) {
  ext.tabs.onActivated.addListener((activeInfo) => {
    callApi(ext.tabs, "get", activeInfo.tabId).then((tab) => queueControlTick(tab, "tab_activated")).catch(() => {});
  });
}

ext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const run = async () => {
    if (message.type === "FIAM_CAPTURE_PAGE") return capturePage();
    if (message.type === "FIAM_ASK_PAGE") return askPage(message.question, message.runtime);
    if (message.type === "FIAM_START_AUTONOMOUS_SESSION") return startAutonomousSession(message.url);
    if (message.type === "FIAM_RUN_CONTROL_TICK") return controlTick(await activeTab(), message.reason || "manual", true);
    if (message.type === "FIAM_PAGE_EVENT") return controlTick(_sender.tab || await activeTab(), message.reason || "page_event");
    if (message.type === "FIAM_START_PICKER") return startPicker(message.mode);
    if (message.type === "FIAM_PROFILE_RULE_PICKED") return saveProfileRule(message.url || _sender.tab?.url || "", message.mode, message.rule || {});
    if (message.type === "FIAM_PROFILE_RULES_PICKED") return saveProfileRules(message.url || _sender.tab?.url || "", message.mode, message.rules || []);
    throw new Error("Unknown message");
  };
  run().then((result) => sendResponse({ ok: true, result })).catch((error) => {
    sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) });
  });
  return true;
});