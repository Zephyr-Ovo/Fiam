const FIAM_MAX_NODES = 120;
const FIAM_MAX_TEXT_BLOCKS = 60;
const FIAM_MAX_MEDIA_SAMPLES = 12;
const FIAM_PROFILE_RULES_KEY = "fiamProfileRules";
const FIAM_PAGE_PROFILE_KEY = "fiamBrowserProfileRules";
const FIAM_DEFAULT_SETTINGS = {
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
const FIAM_ACTIONABLE_SELECTOR = [
  "a[href]",
  "button",
  "input",
  "textarea",
  "select",
  "summary",
  "[contenteditable='true']",
  "[role='button']",
  "[role='link']",
  "[role='textbox']",
  "[role='checkbox']",
  "[role='combobox']",
  "[role='menuitem']",
  "[role='radio']",
  "[role='switch']",
  "[role='tab']"
].join(",");
const ext = globalThis.browser || globalThis.chrome;
let fiamPickerState = null;
let fiamPageNotifyTimer = null;
let fiamLastPageNotifyAt = 0;
let fiamDirectControlBusy = false;
let fiamLastDirectControlAt = 0;

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

function hostForUrl(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch (_error) {
    return "";
  }
}

function normalizeEndpoint(endpoint) {
  return String(endpoint || FIAM_DEFAULT_SETTINGS.endpoint).replace(/\/+$/, "");
}

function wait(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function getSettings() {
  return callApi(ext.storage.local, "get", FIAM_DEFAULT_SETTINGS);
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

async function getRulesByHost() {
  const stored = await callApi(ext.storage.local, "get", { [FIAM_PROFILE_RULES_KEY]: {} });
  const rules = stored[FIAM_PROFILE_RULES_KEY];
  return rules && typeof rules === "object" ? rules : {};
}

async function saveLocalProfileRules(url, mode, rules) {
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
  await callApi(ext.storage.local, "set", { [FIAM_PROFILE_RULES_KEY]: rulesByHost });
  return {
    host,
    profileId: profile.id,
    keep: Array.isArray(profile.keep) ? profile.keep.length : 0,
    suppress: Array.isArray(profile.suppress) ? profile.suppress.length : 0
  };
}

function baseProfileForUrl(url) {
  const host = hostForUrl(url);
  if (!host) throw new Error("Cannot save rule without page host");
  return {
    id: `user:${host}`,
    hosts: [host, `www.${host}`],
    maxNodes: 18,
    strictKeepContextFallback: true,
    keep: [],
    suppress: [],
    groups: { manual_hidden: "manually hidden elements" }
  };
}

function readPageProfile() {
  try {
    const raw = window.localStorage.getItem(FIAM_PAGE_PROFILE_KEY);
    if (!raw) return null;
    const profile = JSON.parse(raw);
    if (!profile || typeof profile !== "object") return null;
    const host = hostForUrl(location.href);
    const hosts = Array.isArray(profile.hosts) ? profile.hosts : [];
    if (hosts.length && !hosts.some((item) => host === item || host.endsWith(`.${item}`))) return null;
    return profile;
  } catch (_error) {
    return null;
  }
}

function writePageProfile(profile) {
  window.localStorage.setItem(FIAM_PAGE_PROFILE_KEY, JSON.stringify(profile));
}

function savePageProfileRules(url, mode, rules) {
  const profile = readPageProfile() || baseProfileForUrl(url);
  const bucket = mode === "suppress" ? "suppress" : "keep";
  if (bucket === "keep") profile.strictKeep = true;
  const unique = [];
  for (const rule of rules) {
    if (!rule || typeof rule !== "object") continue;
    const key = JSON.stringify(rule);
    if (!unique.some((item) => JSON.stringify(item) === key)) unique.push(rule);
  }
  profile[bucket] = unique;
  writePageProfile(profile);
  return {
    host: hostForUrl(url),
    profileId: profile.id,
    keep: Array.isArray(profile.keep) ? profile.keep.length : 0,
    suppress: Array.isArray(profile.suppress) ? profile.suppress.length : 0
  };
}

function pickFromRule(rule) {
  return {
    rule,
    info: {
      role: rule.role || "rule",
      label: rule.alias || rule.labelContains || rule.labelRegex || rule.hrefContains || "",
      selector: rule.selectorContains || rule.selectorRegex || ""
    }
  };
}

function existingPicksForMode(mode) {
  const profile = readPageProfile();
  if (!profile) return [];
  const bucket = mode === "suppress" ? "suppress" : "keep";
  return (Array.isArray(profile[bucket]) ? profile[bucket] : []).filter((rule) => rule && typeof rule === "object").map(pickFromRule);
}

function visible(element) {
  const style = window.getComputedStyle(element);
  if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") return false;
  const rect = element.getBoundingClientRect();
  return rect.width > 0 && rect.height > 0;
}

function cleanText(value, limit = 240) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit - 1).trim()}...` : text;
}

function cssPath(element) {
  if (!(element instanceof Element)) return "";
  if (element.id) return `#${CSS.escape(element.id)}`;
  const parts = [];
  let current = element;
  while (current && current.nodeType === Node.ELEMENT_NODE && parts.length < 5) {
    let part = current.localName.toLowerCase();
    if (current.classList.length) {
      part += `.${[...current.classList].slice(0, 2).map((name) => CSS.escape(name)).join(".")}`;
    }
    const parent = current.parentElement;
    if (parent) {
      const siblings = [...parent.children].filter((item) => item.localName === current.localName);
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
    }
    parts.unshift(part);
    current = parent;
  }
  return parts.join(" > ");
}

function viewportFor(rect) {
  if (rect.bottom < 0 || rect.top > window.innerHeight || rect.right < 0 || rect.left > window.innerWidth) {
    return "nearby";
  }
  return "visible";
}

function roleFor(element) {
  const ariaRole = element.getAttribute("role");
  if (ariaRole) return ariaRole.toLowerCase();
  const tag = element.localName.toLowerCase();
  if (tag === "a") return "link";
  if (tag === "button" || tag === "summary") return "button";
  if (tag === "select") return "combobox";
  if (tag === "textarea") return "textbox";
  if (tag === "input") {
    const type = (element.getAttribute("type") || "text").toLowerCase();
    if (["button", "submit", "reset"].includes(type)) return "button";
    if (["checkbox", "radio"].includes(type)) return type;
    if (type === "search") return "searchbox";
    return "textbox";
  }
  if (element.isContentEditable) return "textbox";
  return "unknown";
}

function labelFor(element) {
  const aria = element.getAttribute("aria-label") || element.getAttribute("title") || element.getAttribute("placeholder");
  if (aria) return cleanText(aria);
  if (element.id) {
    const label = document.querySelector(`label[for="${CSS.escape(element.id)}"]`);
    if (label) return cleanText(label.textContent);
  }
  return cleanText(element.innerText || element.value || element.textContent);
}

function actionsFor(role) {
  if (["textbox", "searchbox", "combobox"].includes(role)) return ["focus", "set_text"];
  if (["button", "checkbox", "link", "menuitem", "radio", "switch", "tab"].includes(role)) return ["click"];
  return [];
}

function actionableTargetFor(element) {
  if (!(element instanceof Element)) return element;
  if (actionsFor(roleFor(element)).length) return element;
  const closest = element.closest(FIAM_ACTIONABLE_SELECTOR);
  if (closest instanceof Element && visible(closest) && actionsFor(roleFor(closest)).length) return closest;
  const child = element.querySelector(FIAM_ACTIONABLE_SELECTOR);
  if (child instanceof Element && visible(child) && actionsFor(roleFor(child)).length) return child;
  return element;
}

function ruleTextFor(label, selector, mode) {
  const cleanLabel = cleanText(label, 100);
  if (mode === "suppress" && cleanLabel.toLowerCase().includes("pin page")) return { labelContains: "pin page" };
  if (cleanLabel) return { labelContains: cleanLabel };
  return { selectorContains: cleanText(selector, 160) };
}

function pickerRuleFor(element, mode) {
  const target = actionableTargetFor(element);
  const role = roleFor(target);
  const label = labelFor(target);
  const selector = cssPath(target);
  const textRule = ruleTextFor(label, selector, mode);
  const rule = { ...textRule };
  if (role && role !== "unknown") rule.role = role;
  if (mode === "keep") rule.alias = cleanText(label || role || "control", 80);
  if (mode === "suppress") rule.group = "manual_hidden";
  return {
    rule,
    info: {
      role,
      label: cleanText(label, 120),
      selector: cleanText(selector, 220)
    }
  };
}

function sendRuntimeMessage(message) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (response) => resolve(response || { ok: false, error: "No response" }));
  });
}

function ensurePickerBox() {
  let box = document.getElementById("fiam-picker-box");
  if (!box) {
    box = document.createElement("div");
    box.id = "fiam-picker-box";
    box.style.cssText = [
      "position:fixed",
      "z-index:2147483647",
      "pointer-events:none",
      "border:2px solid #0a84ff",
      "background:rgba(10,132,255,0.10)",
      "box-shadow:0 0 0 99999px rgba(0,0,0,0.04)",
      "display:none"
    ].join(";");
    document.documentElement.appendChild(box);
  }
  return box;
}

function pickerToast(text) {
  let toast = document.getElementById("fiam-picker-toast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "fiam-picker-toast";
    toast.style.cssText = [
      "position:fixed",
      "z-index:2147483647",
      "left:16px",
      "bottom:16px",
      "max-width:360px",
      "padding:10px 12px",
      "border-radius:6px",
      "background:#201a17",
      "color:#fffaf3",
      "font:13px/1.35 system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif",
      "box-shadow:0 8px 24px rgba(0,0,0,0.24)"
    ].join(";");
    document.documentElement.appendChild(toast);
  }
  toast.textContent = text;
  window.setTimeout(() => toast.remove(), 2200);
}

function panelHtml(mode) {
  const title = "选择保留控件";
  return `
    <div class="fiam-picker-title">${title}</div>
    <div class="fiam-picker-hint">点击页面上需要让 ai 看见/操作的控件。点错了用 ❌ 删除。</div>
    <div class="fiam-picker-list"></div>
    <div class="fiam-picker-actions">
      <button data-fiam-action="save">保存</button>
      <button data-fiam-action="cancel">取消</button>
    </div>
  `;
}

function ensurePickerPanel(mode) {
  let panel = document.getElementById("fiam-picker-panel");
  if (!panel) {
    panel = document.createElement("div");
    panel.id = "fiam-picker-panel";
    panel.style.cssText = [
      "position:fixed",
      "z-index:2147483647",
      "right:16px",
      "top:16px",
      "width:320px",
      "max-height:70vh",
      "overflow:auto",
      "box-sizing:border-box",
      "padding:12px",
      "border:1px solid rgba(32,26,23,0.16)",
      "border-radius:8px",
      "background:#fffaf3",
      "color:#201a17",
      "font:13px/1.35 system-ui,-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif",
      "box-shadow:0 14px 40px rgba(0,0,0,0.24)"
    ].join(";");
    document.documentElement.appendChild(panel);
  }
  panel.innerHTML = panelHtml(mode);
  panel.querySelector("[data-fiam-action='save']").addEventListener("click", async (event) => {
    event.preventDefault();
    event.stopPropagation();
    await savePickerRules();
  });
  panel.querySelector("[data-fiam-action='cancel']").addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    stopPicker();
    pickerToast("已取消选择");
  });
  return panel;
}

function renderPickerList() {
  if (!fiamPickerState?.panel) return;
  const list = fiamPickerState.panel.querySelector(".fiam-picker-list");
  if (!list) return;
  const items = fiamPickerState.picks;
  if (!items.length) {
    list.innerHTML = `<div style="color:#6e5a4d;padding:8px 0;">还没有选择元素</div>`;
    return;
  }
  list.innerHTML = "";
  items.forEach((pick, index) => {
    const row = document.createElement("div");
    row.style.cssText = "display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center;border-top:1px solid rgba(32,26,23,0.12);padding:8px 0;";
    const label = document.createElement("div");
    label.textContent = `${pick.info.role || "element"}: ${pick.info.label || pick.info.selector || "(unlabelled)"}`;
    label.style.cssText = "overflow:hidden;text-overflow:ellipsis;white-space:nowrap;";
    const remove = document.createElement("button");
    remove.textContent = "❌";
    remove.style.cssText = "min-height:26px;border:1px solid #bfae9f;border-radius:6px;background:#fff;color:#201a17;";
    remove.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      fiamPickerState.picks.splice(index, 1);
      renderPickerList();
    });
    row.append(label, remove);
    list.appendChild(row);
  });
}

function sameRule(a, b) {
  return JSON.stringify(a.rule) === JSON.stringify(b.rule);
}

function addPickerElement(element) {
  if (!fiamPickerState) return;
  const picked = pickerRuleFor(element, fiamPickerState.mode);
  if (picked.info.role === "unknown") {
    pickerToast("这个元素没有可操作角色，请点按钮、链接、输入框或标签页");
    return;
  }
  if (!fiamPickerState.picks.some((item) => sameRule(item, picked))) {
    fiamPickerState.picks.push(picked);
  }
  renderPickerList();
}

async function savePickerRules() {
  if (!fiamPickerState) return;
  const { mode, picks } = fiamPickerState;
  if (!picks.length) {
    pickerToast("没有可保存的规则");
    return;
  }
  const rules = picks.map((item) => item.rule);
  try {
    const result = savePageProfileRules(location.href, mode, rules);
    saveLocalProfileRules(location.href, mode, rules).catch(() => {});
    const count = mode === "suppress" ? result.suppress : result.keep;
    stopPicker();
    pickerToast(`规则已保存，共 ${count} 条`);
  } catch (error) {
    const response = await sendRuntimeMessage({ type: "FIAM_PROFILE_RULES_PICKED", mode, url: location.href, rules });
    if (response && response.ok) {
      const count = response.result?.keep ?? response.result?.suppress ?? rules.length;
      stopPicker();
      pickerToast(`规则已保存，共 ${count} 条`);
    } else {
      pickerToast(`保存失败：${response?.error || (error instanceof Error ? error.message : String(error))}`);
    }
  }
}

function blockPickerEvent(event) {
  if (!fiamPickerState) return;
  if (event.target instanceof Element && event.target.closest("#fiam-picker-panel")) return;
  event.preventDefault();
  event.stopPropagation();
}

function stopPicker() {
  if (!fiamPickerState) return;
  document.removeEventListener("mousemove", fiamPickerState.move, true);
  document.removeEventListener("pointerdown", fiamPickerState.block, true);
  document.removeEventListener("mousedown", fiamPickerState.block, true);
  document.removeEventListener("mouseup", fiamPickerState.block, true);
  document.removeEventListener("click", fiamPickerState.click, true);
  document.removeEventListener("keydown", fiamPickerState.keydown, true);
  fiamPickerState.box.remove();
  fiamPickerState.panel.remove();
  fiamPickerState = null;
}

function startPicker(mode) {
  stopPicker();
  const box = ensurePickerBox();
  const panel = ensurePickerPanel(mode);
  const state = {
    mode,
    box,
    panel,
    picks: existingPicksForMode(mode),
    target: null,
    block: blockPickerEvent,
    move(event) {
      if (event.target instanceof Element && event.target.closest("#fiam-picker-panel")) return;
      const element = event.target instanceof Element ? event.target : null;
      if (!element || element.id === "fiam-picker-box" || element.id === "fiam-picker-toast") return;
      state.target = actionableTargetFor(element);
      const rect = state.target.getBoundingClientRect();
      box.style.display = "block";
      box.style.left = `${Math.round(rect.left)}px`;
      box.style.top = `${Math.round(rect.top)}px`;
      box.style.width = `${Math.round(rect.width)}px`;
      box.style.height = `${Math.round(rect.height)}px`;
      box.style.borderColor = mode === "suppress" ? "#d93025" : "#0a84ff";
      box.style.background = mode === "suppress" ? "rgba(217,48,37,0.10)" : "rgba(10,132,255,0.10)";
    },
    click(event) {
      if (event.target instanceof Element && event.target.closest("#fiam-picker-panel")) return;
      const element = state.target || (event.target instanceof Element ? event.target : null);
      if (!element) return;
      event.preventDefault();
      event.stopPropagation();
      addPickerElement(element);
    },
    keydown(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        stopPicker();
        pickerToast("已取消选择");
      }
    }
  };
  fiamPickerState = state;
  document.addEventListener("mousemove", state.move, true);
  document.addEventListener("pointerdown", state.block, true);
  document.addEventListener("mousedown", state.block, true);
  document.addEventListener("mouseup", state.block, true);
  document.addEventListener("click", state.click, true);
  document.addEventListener("keydown", state.keydown, true);
  renderPickerList();
  pickerToast("点击要保留的页面控件，Esc 取消");
}

function setElementText(element, value) {
  element.focus();
  if ("value" in element) {
    element.value = value;
    element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
    element.dispatchEvent(new Event("change", { bubbles: true }));
    return;
  }
  if (element.isContentEditable) {
    element.textContent = value;
    element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
  }
}

function executeBrowserAction(action) {
  const selector = String(action?.selector || "");
  if (!selector) throw new Error("Missing action selector");
  const element = document.querySelector(selector);
  if (!(element instanceof HTMLElement)) throw new Error("Action target not found");
  element.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  const kind = String(action.action || "click");
  if (kind === "focus") {
    element.focus();
  } else if (kind === "set_text") {
    setElementText(element, String(action.text || ""));
  } else if (kind === "click") {
    element.click();
  } else {
    throw new Error(`Unsupported browser action: ${kind}`);
  }
  return {
    ok: true,
    action: kind,
    nodeId: action.nodeId || "",
    label: action.name || labelFor(element) || selector
  };
}

function collectNodes() {
  const nodes = [];
  for (const element of document.querySelectorAll(FIAM_ACTIONABLE_SELECTOR)) {
    if (!visible(element)) continue;
    const role = roleFor(element);
    const label = labelFor(element);
    const rect = element.getBoundingClientRect();
    nodes.push({
      id: `node_${nodes.length + 1}`,
      role,
      name: label,
      text: cleanText(element.innerText || element.value || element.textContent),
      selector: cssPath(element),
      href: cleanText(element.href || "", 320),
      rect: [Math.round(rect.left), Math.round(rect.top), Math.round(rect.width), Math.round(rect.height)],
      viewport: viewportFor(rect),
      actions: actionsFor(role)
    });
    if (nodes.length >= FIAM_MAX_NODES) break;
  }
  return nodes;
}

function collectMedia() {
  const samples = [];
  const images = [...document.querySelectorAll("img,picture")].filter((element) => visible(element));
  const videos = [...document.querySelectorAll("video")].filter((element) => visible(element));
  const iframes = [...document.querySelectorAll("iframe")].filter((element) => visible(element));
  const canvases = [...document.querySelectorAll("canvas")].filter((element) => visible(element));
  const backgrounds = [];
  for (const element of document.querySelectorAll("main *, [role='main'] *, article *, [style], [class]")) {
    if (backgrounds.length >= 200) break;
    if (!(element instanceof Element) || !visible(element)) continue;
    const rect = element.getBoundingClientRect();
    if (rect.width < 48 || rect.height < 48) continue;
    const style = window.getComputedStyle(element);
    if (!style.backgroundImage || style.backgroundImage === "none") continue;
    backgrounds.push(element);
  }
  for (const element of [...images, ...videos, ...iframes]) {
    const rect = element.getBoundingClientRect();
    const tag = element.localName.toLowerCase();
    const kind = tag === "picture" ? "image" : tag;
    const label = cleanText(
      element.getAttribute("alt") ||
      element.getAttribute("aria-label") ||
      element.getAttribute("title") ||
      element.closest("figure")?.innerText ||
      "",
      180
    );
    samples.push({
      kind,
      label,
      rect: [Math.round(rect.left), Math.round(rect.top), Math.round(rect.width), Math.round(rect.height)],
      viewport: viewportFor(rect)
    });
    if (samples.length >= FIAM_MAX_MEDIA_SAMPLES) break;
  }
  for (const element of [...canvases, ...backgrounds]) {
    if (samples.length >= FIAM_MAX_MEDIA_SAMPLES) break;
    const rect = element.getBoundingClientRect();
    const kind = element.localName.toLowerCase() === "canvas" ? "canvas" : "background-image";
    const label = cleanText(
      element.getAttribute("aria-label") ||
      element.getAttribute("title") ||
      element.closest("figure")?.innerText ||
      element.closest("a")?.innerText ||
      "",
      180
    );
    samples.push({
      kind,
      label,
      rect: [Math.round(rect.left), Math.round(rect.top), Math.round(rect.width), Math.round(rect.height)],
      viewport: viewportFor(rect)
    });
  }
  return {
    imageCount: images.length + backgrounds.length + canvases.length,
    imageElementCount: images.length,
    backgroundImageCount: backgrounds.length,
    canvasCount: canvases.length,
    videoCount: videos.length,
    iframeCount: iframes.length,
    samples
  };
}

function collectTextBlocks() {
  const roots = document.querySelectorAll("main, article, [role='main'], p, li, td, th, label");
  const blocks = [];
  for (const element of roots) {
    if (!(element instanceof Element) || !visible(element)) continue;
    const text = cleanText(element.innerText || element.textContent, 360);
    if (text.length < 12) continue;
    if (blocks.includes(text)) continue;
    blocks.push(text);
    if (blocks.length >= FIAM_MAX_TEXT_BLOCKS) break;
  }
  return blocks;
}

function snapshot() {
  return {
    schema: "fiam.browser.snapshot.v1",
    url: location.href,
    title: document.title,
    capturedAt: new Date().toISOString(),
    selection: cleanText(String(window.getSelection() || ""), 1600),
    headings: [...document.querySelectorAll("h1,h2,h3,[role='heading']")]
      .filter((element) => visible(element))
      .map((element) => cleanText(element.innerText || element.textContent))
      .filter(Boolean)
      .slice(0, 20),
    textBlocks: collectTextBlocks(),
    nodes: collectNodes(),
    media: collectMedia(),
    profileRules: readPageProfile(),
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
      scrollX: window.scrollX,
      scrollY: window.scrollY
    }
  };
}

function actionSignature(action) {
  return [action?.action || "click", action?.nodeId || "", action?.name || "", action?.text || ""].join("|");
}

function snapshotSignature(pageSnapshot) {
  return [
    pageSnapshot?.url || "",
    pageSnapshot?.title || "",
    (pageSnapshot?.headings || []).slice(0, 8).join("|"),
    (pageSnapshot?.textBlocks || []).slice(0, 3).join("|")
  ].join("\n");
}

async function runDirectControlTick(reason) {
  if (fiamDirectControlBusy) return { skipped: true, reason: "busy" };
  const settings = await getSettings();
  if (settings.controlMode !== "autonomous" && reason !== "manual" && reason !== "popup_debug") return { skipped: true, reason: "ask_do_mode" };
  if (!settings.autonomousControl && reason !== "manual" && reason !== "popup_debug") return { skipped: true, reason: "disabled" };
  if (!settings.token) return { skipped: true, reason: "missing_token" };
  const now = Date.now();
  const cooldown = Number(settings.controlCooldownMs || FIAM_DEFAULT_SETTINGS.controlCooldownMs);
  if (now - fiamLastDirectControlAt < cooldown) return { skipped: true, reason: "cooldown" };
  fiamDirectControlBusy = true;
  fiamLastDirectControlAt = now;
  try {
    let currentSnapshot = snapshot();
    let result = await postJson("/browser/tick", {
      reason: reason || "page_event",
      runtime: settings.runtime || "api",
      snapshot: currentSnapshot
    }, settings);
    const executed = [];
    const controlTrail = [];
    let lastActionSignature = "";
    let lastSnapshotSignature = "";
    const maxSteps = Math.max(1, Math.min(10, Number(settings.controlMaxSteps || FIAM_DEFAULT_SETTINGS.controlMaxSteps)));
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
      const execution = executeBrowserAction(action);
      await wait(450);
      const afterSnapshot = snapshot();
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
        controlTrail
      }, settings);
    }
    if (executed.length >= maxSteps) result.control_finished = "max_steps";
    result.executed_browser_actions = executed;
    result.browser_actions = [];
    result.control_steps = executed.length;
    return result;
  } finally {
    fiamDirectControlBusy = false;
  }
}

function shouldFallbackToDirect(response) {
  if (!response) return true;
  if (response.ok) return false;
  const error = String(response.error || "").toLowerCase();
  return error.includes("unknown message") || error.includes("receiving end") || error.includes("could not establish connection");
}

async function sendPageEvent(reason) {
  try {
    const response = await callApi(ext.runtime, "sendMessage", { type: "FIAM_PAGE_EVENT", reason, url: location.href });
    if (shouldFallbackToDirect(response)) await runDirectControlTick(reason);
  } catch (_error) {
    try {
      await runDirectControlTick(reason);
    } catch (_directError) {
      // Ignore extension lifecycle races while pages are loading or unloading.
    }
  }
}

function schedulePageEvent(reason, delay = 1200) {
  if (fiamPickerState) return;
  const now = Date.now();
  if (now - fiamLastPageNotifyAt < 7000) return;
  if (fiamPageNotifyTimer) window.clearTimeout(fiamPageNotifyTimer);
  fiamPageNotifyTimer = window.setTimeout(() => {
    fiamPageNotifyTimer = null;
    fiamLastPageNotifyAt = Date.now();
    sendPageEvent(reason);
  }, delay);
}

ext.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message && message.type === "FIAM_COLLECT_SNAPSHOT") {
    sendResponse({ ok: true, snapshot: snapshot() });
    return true;
  }
  if (message && message.type === "FIAM_START_PICKER") {
    startPicker(message.mode === "suppress" ? "suppress" : "keep");
    sendResponse({ ok: true });
    return true;
  }
  if (message && message.type === "FIAM_EXECUTE_BROWSER_ACTION") {
    try {
      sendResponse({ ok: true, result: executeBrowserAction(message.action || {}) });
    } catch (error) {
      sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) });
    }
    return true;
  }
  if (message && message.type === "FIAM_DIRECT_CONTROL_TICK") {
    runDirectControlTick(message.reason || "manual").then((result) => {
      sendResponse({ ok: true, result });
    }).catch((error) => {
      sendResponse({ ok: false, error: error instanceof Error ? error.message : String(error) });
    });
    return true;
  }
  return false;
});

window.addEventListener("load", () => schedulePageEvent("page_load", 900), { once: true });
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") schedulePageEvent("page_visible", 500);
});

new MutationObserver(() => schedulePageEvent("dom_changed", 1800)).observe(document.documentElement, {
  childList: true,
  subtree: true,
  attributes: true,
  attributeFilter: ["aria-label", "aria-expanded", "class", "hidden", "style"]
});

schedulePageEvent("content_ready", 900);