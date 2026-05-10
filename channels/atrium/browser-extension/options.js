const endpoint = document.getElementById("endpoint");
const token = document.getElementById("token");
const runtime = document.getElementById("runtime");
const autoExecute = document.getElementById("auto-execute");
const autonomousControl = document.getElementById("autonomous-control");
const autoScreenshot = document.getElementById("auto-screenshot");
const statusBox = document.getElementById("status");
const ext = globalThis.browser || globalThis.chrome;

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

callApi(ext.storage.local, "get", { endpoint: "http://127.0.0.1:8766", token: "", runtime: "api", autoExecuteActions: true, autonomousControl: false, controlMode: "session", autoScreenshot: true }).then((settings) => {
  endpoint.value = settings.endpoint;
  token.value = settings.token;
  runtime.value = settings.runtime;
  autoExecute.checked = settings.autoExecuteActions !== false;
  autonomousControl.checked = settings.controlMode === "autonomous";
  autoScreenshot.checked = settings.autoScreenshot !== false;
});

document.getElementById("save").addEventListener("click", async () => {
  await callApi(ext.storage.local, "set", {
    endpoint: endpoint.value.trim() || "http://127.0.0.1:8766",
    token: token.value.trim(),
    runtime: runtime.value,
    autoExecuteActions: autoExecute.checked,
    autonomousControl: autonomousControl.checked,
    controlMode: autonomousControl.checked ? "autonomous" : "session",
    autoScreenshot: autoScreenshot.checked
  });
  statusBox.textContent = "Saved";
});