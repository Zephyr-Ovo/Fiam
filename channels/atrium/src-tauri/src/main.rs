#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use rumqttc::{Client, Event, Incoming, MqttOptions, Publish, QoS};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::BTreeMap,
    fs::{self, OpenOptions},
    io::Write,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, Manager, State, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
};
#[cfg(windows)]
use windows_sys::Win32::Foundation::{
    CloseHandle, HWND, INVALID_HANDLE_VALUE, LPARAM, TRUE, WPARAM,
};
#[cfg(windows)]
use windows_sys::Win32::Networking::WinInet::{
    InternetSetOptionW, INTERNET_OPTION_REFRESH, INTERNET_OPTION_SETTINGS_CHANGED,
};
#[cfg(windows)]
use windows_sys::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, Process32FirstW, Process32NextW, PROCESSENTRY32W, TH32CS_SNAPPROCESS,
};
#[cfg(windows)]
use windows_sys::Win32::UI::WindowsAndMessaging::{
    EnumWindows, GetForegroundWindow, GetWindowTextLengthW, GetWindowTextW,
    GetWindowThreadProcessId, IsWindowVisible, PostMessageW, SendMessageW, WM_LBUTTONDOWN,
    WM_LBUTTONUP, WM_MOUSEMOVE, WM_SETTEXT,
};
#[cfg(windows)]
use winreg::{enums::HKEY_CURRENT_USER, RegKey};

#[derive(Debug, Deserialize, Clone)]
struct Registry {
    capabilities: BTreeMap<String, Capability>,
}

#[derive(Debug, Deserialize, Clone)]
struct Capability {
    kind: String,
    trust: u8,
    enabled: bool,
    description: Option<String>,
}

#[derive(Debug, Serialize)]
struct CapabilityView {
    name: String,
    kind: String,
    trust: u8,
    enabled: bool,
    description: String,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct StatusView {
    paused: bool,
    panic: bool,
    capability_count: usize,
    enabled_count: usize,
    actuator_count: usize,
    sensor_count: usize,
    audit_path: String,
    mqtt: MqttView,
    mitm: MitmView,
    proxy: ProxyView,
    cert: CertView,
    intercept_count: usize,
    pending_count: usize,
}

#[derive(Debug, Deserialize, Clone)]
struct DispatchRequest {
    #[serde(default = "dispatch_id")]
    id: String,
    capability: String,
    reason: Option<String>,
    #[serde(default = "empty_payload")]
    payload: Value,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct DispatchResult {
    id: String,
    ok: bool,
    status: String,
    audit_id: String,
    capability: Option<String>,
    trust: Option<u8>,
    kind: Option<String>,
    result: Value,
}

#[derive(Debug, Serialize)]
struct AuditEntry {
    id: String,
    ts_ms: u128,
    event: String,
    ok: bool,
    capability: Option<String>,
    reason: Option<String>,
    detail: Value,
}

#[derive(Debug, Deserialize)]
struct FiamToml {
    mqtt: Option<MqttToml>,
}

#[derive(Debug, Deserialize)]
struct MqttToml {
    host: Option<String>,
    port: Option<u16>,
    keepalive: Option<u64>,
}

#[derive(Debug, Clone)]
struct MqttSettings {
    enabled: bool,
    host: String,
    port: u16,
    keepalive: u64,
    client_id: String,
    dispatch_topic: String,
    result_topic: String,
}

#[derive(Clone)]
struct MqttRuntime {
    settings: MqttSettings,
    client: Option<Client>,
    connected: bool,
    status: String,
    received: u64,
    published: u64,
    last_error: Option<String>,
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct MqttView {
    enabled: bool,
    connected: bool,
    status: String,
    host: String,
    port: u16,
    dispatch_topic: String,
    result_topic: String,
    received: u64,
    published: u64,
    last_error: Option<String>,
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct MitmView {
    running: bool,
    pid: Option<u32>,
    port: u16,
    rule_count: usize,
    rules_path: String,
    script_path: String,
    mitmdump_path: String,
    last_error: Option<String>,
}

#[derive(Debug)]
struct MitmRuntime {
    running: bool,
    pid: Option<u32>,
    port: u16,
    rule_count: usize,
    rules_path: PathBuf,
    script_path: PathBuf,
    mitmdump_path: PathBuf,
    child: Option<Child>,
    last_error: Option<String>,
}

#[derive(Debug)]
struct ProxyRuntime {
    snapshot_path: PathBuf,
    last_error: Option<String>,
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct ProxyView {
    enabled: bool,
    server: Option<String>,
    override_list: Option<String>,
    auto_config_url: Option<String>,
    snapshot_path: String,
    has_snapshot: bool,
    last_error: Option<String>,
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct CertView {
    available: bool,
    path: String,
    last_error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct InterceptHit {
    id: String,
    ts_ms: u128,
    rule_id: String,
    host: String,
    path: String,
    method: String,
    url: String,
    reason: Option<String>,
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct WindowInfo {
    hwnd: usize,
    pid: u32,
    title: String,
    visible: bool,
    focused: bool,
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
struct ProcessInfo {
    pid: u32,
    parent_pid: u32,
    exe: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct ProxySnapshot {
    proxy_enable: Option<u32>,
    proxy_server: Option<String>,
    proxy_override: Option<String>,
    auto_config_url: Option<String>,
}

#[derive(Debug, Clone)]
struct PendingDispatch {
    request: DispatchRequest,
    trust: u8,
    kind: String,
    reason: Option<String>,
    source: String,
    created_ms: u128,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct PendingView {
    id: String,
    capability: String,
    trust: u8,
    kind: String,
    reason: Option<String>,
    source: String,
    created_ms: u128,
    payload: Value,
}

#[derive(Debug, Serialize, Deserialize)]
struct InterceptRulesFile {
    version: u8,
    rules: Vec<InterceptRule>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
#[serde(rename_all = "camelCase")]
struct InterceptRule {
    id: String,
    host: String,
    path: Option<String>,
    reason: Option<String>,
    #[serde(default = "default_true")]
    enabled: bool,
    created_ms: u128,
    release_until_ms: Option<u128>,
}

impl Default for MqttSettings {
    fn default() -> Self {
        Self {
            enabled: true,
            host: "127.0.0.1".into(),
            port: 1883,
            keepalive: 60,
            client_id: "fiam-atrium".into(),
            dispatch_topic: "fiam/dispatch/desktop".into(),
            result_topic: "fiam/receive/desktop/result".into(),
        }
    }
}

impl MqttRuntime {
    fn new(settings: MqttSettings) -> Self {
        Self {
            settings,
            client: None,
            connected: false,
            status: "idle".into(),
            received: 0,
            published: 0,
            last_error: None,
        }
    }
}

impl MitmRuntime {
    fn new(root: &Path) -> Self {
        let rules_path = mitm_rules_path(root);
        let script_path = mitm_script_path(root);
        let mitmdump_path = resolve_mitmdump_path(root);
        let rule_count = load_rules(&rules_path)
            .map(|rules| rules.rules.len())
            .unwrap_or_default();

        Self {
            running: false,
            pid: None,
            port: 8088,
            rule_count,
            rules_path,
            script_path,
            mitmdump_path,
            child: None,
            last_error: None,
        }
    }
}

impl ProxyRuntime {
    fn new(root: &Path) -> Self {
        Self {
            snapshot_path: root.join("proxy-snapshot.json"),
            last_error: None,
        }
    }
}

struct CoreState {
    root: PathBuf,
    app: Option<AppHandle>,
    reader_label: Option<String>,
    browser_label: Option<String>,
    registry: Registry,
    paused: bool,
    panic: bool,
    mqtt: MqttRuntime,
    mitm: MitmRuntime,
    proxy: ProxyRuntime,
    pending: BTreeMap<String, PendingDispatch>,
}

type SharedState = Arc<Mutex<CoreState>>;

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

fn audit_id() -> String {
    format!("audit-{}", now_ms())
}

fn dispatch_id() -> String {
    format!("dispatch-{}", now_ms())
}

fn empty_payload() -> Value {
    json!({})
}

fn default_true() -> bool {
    true
}

fn project_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri has a parent directory")
        .to_path_buf()
}

fn capability_path(root: &Path) -> PathBuf {
    root.join("capabilities.toml")
}

fn audit_path(root: &Path) -> PathBuf {
    root.join("audit.jsonl")
}

fn mitm_cert_path() -> PathBuf {
    std::env::var("USERPROFILE")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("."))
        .join(".mitmproxy")
        .join("mitmproxy-ca-cert.cer")
}

fn proxy_target(port: u16) -> String {
    format!("http=127.0.0.1:{port};https=127.0.0.1:{port}")
}

fn mitm_dir(root: &Path) -> PathBuf {
    root.join("tools").join("mitmproxy")
}

fn mitm_rules_path(root: &Path) -> PathBuf {
    mitm_dir(root).join("rules.json")
}

fn mitm_script_path(root: &Path) -> PathBuf {
    mitm_dir(root).join("rules.py")
}

fn intercepts_path(root: &Path) -> PathBuf {
    mitm_dir(root).join("intercepts.jsonl")
}

fn workspace_root(root: &Path) -> Option<PathBuf> {
    root.parent().and_then(Path::parent).map(Path::to_path_buf)
}

fn resolve_mitmdump_path(root: &Path) -> PathBuf {
    if let Ok(path) = std::env::var("ATRIUM_MITMDUMP") {
        let trimmed = path.trim();
        if !trimmed.is_empty() {
            return PathBuf::from(trimmed);
        }
    }
    if let Some(workspace) = workspace_root(root) {
        let candidate = workspace.join(".venv").join("Scripts").join("mitmdump.exe");
        if candidate.is_file() {
            return candidate;
        }
    }
    PathBuf::from("mitmdump")
}

fn load_registry(root: &Path) -> Result<Registry, String> {
    let source = fs::read_to_string(capability_path(root)).map_err(|err| err.to_string())?;
    toml::from_str(&source).map_err(|err| err.to_string())
}

fn load_mqtt_settings(root: &Path) -> MqttSettings {
    let mut settings = MqttSettings::default();
    let Some(workspace) = workspace_root(root) else {
        return settings;
    };

    let path = workspace.join("fiam.toml");
    let Ok(source) = fs::read_to_string(path) else {
        return settings;
    };
    let Ok(config) = toml::from_str::<FiamToml>(&source) else {
        return settings;
    };
    let Some(mqtt) = config.mqtt else {
        return settings;
    };

    if let Some(host) = mqtt.host {
        settings.host = host;
    }
    if let Some(port) = mqtt.port {
        settings.port = port;
    }
    if let Some(keepalive) = mqtt.keepalive {
        settings.keepalive = keepalive;
    }
    settings
}

fn empty_rules() -> InterceptRulesFile {
    InterceptRulesFile {
        version: 1,
        rules: Vec::new(),
    }
}

fn load_rules(path: &Path) -> Result<InterceptRulesFile, String> {
    if !path.is_file() {
        return Ok(empty_rules());
    }
    let source = fs::read_to_string(path).map_err(|err| err.to_string())?;
    if source.trim().is_empty() {
        return Ok(empty_rules());
    }
    serde_json::from_str(&source).map_err(|err| err.to_string())
}

fn load_intercepts(path: &Path) -> Result<Vec<InterceptHit>, String> {
    if !path.is_file() {
        return Ok(Vec::new());
    }
    let source = fs::read_to_string(path).map_err(|err| err.to_string())?;
    let mut hits = Vec::new();
    for line in source.lines().filter(|line| !line.trim().is_empty()) {
        if let Ok(hit) = serde_json::from_str::<InterceptHit>(line) {
            hits.push(hit);
        }
    }
    hits.sort_by(|left, right| right.ts_ms.cmp(&left.ts_ms));
    hits.truncate(25);
    Ok(hits)
}

fn save_rules(path: &Path, rules: &InterceptRulesFile) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    let body = serde_json::to_string_pretty(rules).map_err(|err| err.to_string())?;
    fs::write(path, format!("{body}\n")).map_err(|err| err.to_string())
}

fn write_audit(root: &Path, entry: AuditEntry) -> Result<String, String> {
    let id = entry.id.clone();
    let path = audit_path(root);
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|err| err.to_string())?;
    let line = serde_json::to_string(&entry).map_err(|err| err.to_string())?;
    writeln!(file, "{line}").map_err(|err| err.to_string())?;
    Ok(id)
}

fn registry_views(registry: &Registry) -> Vec<CapabilityView> {
    registry
        .capabilities
        .iter()
        .map(|(name, capability)| CapabilityView {
            name: name.clone(),
            kind: capability.kind.clone(),
            trust: capability.trust,
            enabled: capability.enabled,
            description: capability.description.clone().unwrap_or_default(),
        })
        .collect()
}

fn mqtt_view(mqtt: &MqttRuntime) -> MqttView {
    MqttView {
        enabled: mqtt.settings.enabled,
        connected: mqtt.connected,
        status: mqtt.status.clone(),
        host: mqtt.settings.host.clone(),
        port: mqtt.settings.port,
        dispatch_topic: mqtt.settings.dispatch_topic.clone(),
        result_topic: mqtt.settings.result_topic.clone(),
        received: mqtt.received,
        published: mqtt.published,
        last_error: mqtt.last_error.clone(),
    }
}

fn mitm_view(mitm: &MitmRuntime) -> MitmView {
    MitmView {
        running: mitm.running,
        pid: mitm.pid,
        port: mitm.port,
        rule_count: mitm.rule_count,
        rules_path: mitm.rules_path.display().to_string(),
        script_path: mitm.script_path.display().to_string(),
        mitmdump_path: mitm.mitmdump_path.display().to_string(),
        last_error: mitm.last_error.clone(),
    }
}

fn proxy_view(proxy: &ProxyRuntime) -> ProxyView {
    let (snapshot, error) = read_proxy_snapshot()
        .map(|snapshot| (snapshot, None))
        .unwrap_or_else(|error| {
            (
                ProxySnapshot {
                    proxy_enable: None,
                    proxy_server: None,
                    proxy_override: None,
                    auto_config_url: None,
                },
                Some(error),
            )
        });

    ProxyView {
        enabled: snapshot.proxy_enable.unwrap_or_default() != 0,
        server: snapshot.proxy_server,
        override_list: snapshot.proxy_override,
        auto_config_url: snapshot.auto_config_url,
        snapshot_path: proxy.snapshot_path.display().to_string(),
        has_snapshot: proxy.snapshot_path.is_file(),
        last_error: proxy.last_error.clone().or(error),
    }
}

fn cert_view() -> CertView {
    let path = mitm_cert_path();
    CertView {
        available: path.is_file(),
        path: path.display().to_string(),
        last_error: None,
    }
}

fn status_view(state: &CoreState) -> StatusView {
    let capability_count = state.registry.capabilities.len();
    let enabled_count = state
        .registry
        .capabilities
        .values()
        .filter(|capability| capability.enabled)
        .count();
    let actuator_count = state
        .registry
        .capabilities
        .values()
        .filter(|capability| capability.kind == "actuator")
        .count();
    let sensor_count = state
        .registry
        .capabilities
        .values()
        .filter(|capability| capability.kind == "sensor")
        .count();

    StatusView {
        paused: state.paused,
        panic: state.panic,
        capability_count,
        enabled_count,
        actuator_count,
        sensor_count,
        audit_path: audit_path(&state.root).display().to_string(),
        mqtt: mqtt_view(&state.mqtt),
        mitm: mitm_view(&state.mitm),
        proxy: proxy_view(&state.proxy),
        cert: cert_view(),
        intercept_count: load_intercepts(&intercepts_path(&state.root))
            .map(|hits| hits.len())
            .unwrap_or_default(),
        pending_count: state.pending.len(),
    }
}

fn pending_views(core: &CoreState) -> Vec<PendingView> {
    core.pending
        .iter()
        .map(|(id, pending)| PendingView {
            id: id.clone(),
            capability: pending.request.capability.clone(),
            trust: pending.trust,
            kind: pending.kind.clone(),
            reason: pending.reason.clone(),
            source: pending.source.clone(),
            created_ms: pending.created_ms,
            payload: pending.request.payload.clone(),
        })
        .collect()
}

fn refresh_mitm_status(core: &mut CoreState) {
    let exited = if let Some(child) = core.mitm.child.as_mut() {
        match child.try_wait() {
            Ok(Some(status)) => Some(format!("mitmdump exited: {status}")),
            Ok(None) => None,
            Err(error) => Some(error.to_string()),
        }
    } else {
        None
    };

    if let Some(message) = exited {
        core.mitm.child = None;
        core.mitm.running = false;
        core.mitm.pid = None;
        core.mitm.last_error = Some(message);
    }
}

fn update_mqtt_state<F>(state: &SharedState, update: F)
where
    F: FnOnce(&mut MqttRuntime),
{
    if let Ok(mut core) = state.lock() {
        update(&mut core.mqtt);
    }
}

fn write_runtime_audit(
    state: &SharedState,
    event: &str,
    ok: bool,
    reason: Option<String>,
    detail: Value,
) {
    let root = state.lock().map(|core| core.root.clone());
    if let Ok(root) = root {
        let _ = write_audit(
            &root,
            AuditEntry {
                id: audit_id(),
                ts_ms: now_ms(),
                event: event.into(),
                ok,
                capability: None,
                reason,
                detail,
            },
        );
    }
}

#[derive(Debug, Clone, Copy)]
enum DispatchMode {
    DryRun,
    Execute,
    Confirmed,
}

struct ExecutionOutcome {
    status: String,
    result: Value,
}

#[cfg(windows)]
const MK_LBUTTON_WPARAM: WPARAM = 0x0001;

fn payload_string(payload: &Value, key: &str) -> Option<String> {
    payload
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
}

#[cfg(windows)]
fn proxy_key() -> Result<RegKey, String> {
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    hkcu.open_subkey_with_flags(
        "Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings",
        winreg::enums::KEY_READ | winreg::enums::KEY_WRITE,
    )
    .map_err(|err| err.to_string())
}

#[cfg(windows)]
fn optional_reg_value<T: winreg::types::FromRegValue>(key: &RegKey, name: &str) -> Option<T> {
    key.get_value(name).ok()
}

#[cfg(windows)]
fn read_proxy_snapshot() -> Result<ProxySnapshot, String> {
    let key = proxy_key()?;
    Ok(ProxySnapshot {
        proxy_enable: optional_reg_value(&key, "ProxyEnable"),
        proxy_server: optional_reg_value(&key, "ProxyServer"),
        proxy_override: optional_reg_value(&key, "ProxyOverride"),
        auto_config_url: optional_reg_value(&key, "AutoConfigURL"),
    })
}

#[cfg(not(windows))]
fn read_proxy_snapshot() -> Result<ProxySnapshot, String> {
    Err("proxy management is only implemented on Windows".into())
}

#[cfg(windows)]
fn set_optional_string(key: &RegKey, name: &str, value: &Option<String>) -> Result<(), String> {
    if let Some(value) = value {
        key.set_value(name, value).map_err(|err| err.to_string())
    } else {
        let _ = key.delete_value(name);
        Ok(())
    }
}

#[cfg(windows)]
fn notify_proxy_changed() {
    unsafe {
        let _ = InternetSetOptionW(
            std::ptr::null_mut(),
            INTERNET_OPTION_SETTINGS_CHANGED,
            std::ptr::null_mut(),
            0,
        );
        let _ = InternetSetOptionW(
            std::ptr::null_mut(),
            INTERNET_OPTION_REFRESH,
            std::ptr::null_mut(),
            0,
        );
    }
}

#[cfg(not(windows))]
fn notify_proxy_changed() {}

#[cfg(windows)]
fn write_proxy_snapshot(snapshot: &ProxySnapshot) -> Result<(), String> {
    let key = proxy_key()?;
    key.set_value("ProxyEnable", &snapshot.proxy_enable.unwrap_or_default())
        .map_err(|err| err.to_string())?;
    set_optional_string(&key, "ProxyServer", &snapshot.proxy_server)?;
    set_optional_string(&key, "ProxyOverride", &snapshot.proxy_override)?;
    set_optional_string(&key, "AutoConfigURL", &snapshot.auto_config_url)?;
    notify_proxy_changed();
    Ok(())
}

#[cfg(not(windows))]
fn write_proxy_snapshot(_snapshot: &ProxySnapshot) -> Result<(), String> {
    Err("proxy management is only implemented on Windows".into())
}

fn save_proxy_snapshot_file(path: &Path, snapshot: &ProxySnapshot) -> Result<(), String> {
    let body = serde_json::to_string_pretty(snapshot).map_err(|err| err.to_string())?;
    fs::write(path, format!("{body}\n")).map_err(|err| err.to_string())
}

fn load_proxy_snapshot_file(path: &Path) -> Result<ProxySnapshot, String> {
    let source = fs::read_to_string(path).map_err(|err| err.to_string())?;
    serde_json::from_str(&source).map_err(|err| err.to_string())
}

fn apply_system_proxy(core: &mut CoreState) -> Result<Value, String> {
    let previous = read_proxy_snapshot()?;
    save_proxy_snapshot_file(&core.proxy.snapshot_path, &previous)?;
    let next = ProxySnapshot {
        proxy_enable: Some(1),
        proxy_server: Some(proxy_target(core.mitm.port)),
        proxy_override: previous.proxy_override.clone().or(Some("<local>".into())),
        auto_config_url: previous.auto_config_url.clone(),
    };
    write_proxy_snapshot(&next)?;
    core.proxy.last_error = None;
    Ok(json!({
        "applied": true,
        "proxyServer": next.proxy_server,
        "snapshotPath": core.proxy.snapshot_path,
    }))
}

fn restore_system_proxy(core: &mut CoreState) -> Result<Value, String> {
    let snapshot = load_proxy_snapshot_file(&core.proxy.snapshot_path)?;
    write_proxy_snapshot(&snapshot)?;
    core.proxy.last_error = None;
    Ok(json!({
        "restored": true,
        "snapshotPath": core.proxy.snapshot_path,
    }))
}

fn spawn_reader_surface(core: &mut CoreState, payload: &Value) -> Result<Value, String> {
    let Some(app) = core.app.clone() else {
        return Err("app handle not ready".into());
    };
    let kind = payload_string(payload, "kind").unwrap_or_else(|| "reader".into());
    if !matches!(kind.as_str(), "reader" | "co-reader" | "doc-viewer") {
        return Err(format!("unknown surface kind: {kind}"));
    }
    let title = payload_string(payload, "title").unwrap_or_else(|| "Atrium Reader".into());
    let label = format!("reader-{}", now_ms());
    let window = WebviewWindowBuilder::new(
        &app,
        label.clone(),
        WebviewUrl::App("index.html?surface=reader".into()),
    )
    .title(&title)
    .inner_size(860.0, 720.0)
    .min_inner_size(520.0, 420.0)
    .build()
    .map_err(|err| err.to_string())?;
    let _ = window.show();
    core.reader_label = Some(label.clone());
    Ok(json!({ "label": label, "kind": kind, "title": title }))
}

fn target_reader_label(core: &CoreState, payload: &Value) -> Result<String, String> {
    payload_string(payload, "label")
        .or_else(|| core.reader_label.clone())
        .ok_or_else(|| "no reader surface is open".to_string())
}

fn reader_set_text(core: &mut CoreState, payload: &Value) -> Result<Value, String> {
    let Some(app) = core.app.clone() else {
        return Err("app handle not ready".into());
    };
    let label = target_reader_label(core, payload)?;
    let text = payload_string(payload, "text").unwrap_or_default();
    app.emit_to(&label, "reader:set_text", json!({ "text": text }))
        .map_err(|err| err.to_string())?;
    Ok(json!({ "label": label, "chars": text.chars().count() }))
}

fn reader_append_text(core: &mut CoreState, payload: &Value) -> Result<Value, String> {
    let Some(app) = core.app.clone() else {
        return Err("app handle not ready".into());
    };
    let label = target_reader_label(core, payload)?;
    let text = payload_string(payload, "text").unwrap_or_default();
    app.emit_to(&label, "reader:append_text", json!({ "text": text }))
        .map_err(|err| err.to_string())?;
    Ok(json!({ "label": label, "chars": text.chars().count() }))
}

fn parse_browser_url(raw: &str, allow_blank: bool) -> Result<Url, String> {
    let trimmed = raw.trim();
    if allow_blank && trimmed == "about:blank" {
        return Url::parse(trimmed).map_err(|err| err.to_string());
    }
    let candidate = if trimmed.starts_with("http://") || trimmed.starts_with("https://") {
        trimmed.to_string()
    } else {
        format!("https://{trimmed}")
    };
    let url = Url::parse(&candidate).map_err(|err| err.to_string())?;
    if matches!(url.scheme(), "http" | "https") {
        Ok(url)
    } else {
        Err("only http/https browser surface URLs are allowed".into())
    }
}

fn spawn_browser_surface(core: &mut CoreState, payload: &Value) -> Result<Value, String> {
    let Some(app) = core.app.clone() else {
        return Err("app handle not ready".into());
    };
    let title = payload_string(payload, "title").unwrap_or_else(|| "Atrium Browser".into());
    let raw_url = payload_string(payload, "url").unwrap_or_else(|| "about:blank".into());
    let url = parse_browser_url(&raw_url, true)?;
    let label = format!("browser-{}", now_ms());
    let window = WebviewWindowBuilder::new(&app, label.clone(), WebviewUrl::External(url.clone()))
        .title(&title)
        .inner_size(1120.0, 780.0)
        .min_inner_size(640.0, 480.0)
        .build()
        .map_err(|err| err.to_string())?;
    let _ = window.show();
    core.browser_label = Some(label.clone());
    Ok(json!({ "label": label, "kind": "browser", "title": title, "url": url.as_str() }))
}

fn spawn_app_surface(core: &mut CoreState, payload: &Value) -> Result<Value, String> {
    let kind = payload_string(payload, "kind").unwrap_or_else(|| "reader".into());
    if matches!(kind.as_str(), "browser" | "web" | "webview") {
        spawn_browser_surface(core, payload)
    } else {
        spawn_reader_surface(core, payload)
    }
}

fn target_browser_label(core: &CoreState, payload: &Value) -> Result<String, String> {
    payload_string(payload, "label")
        .or_else(|| core.browser_label.clone())
        .ok_or_else(|| "no browser surface is open".to_string())
}

fn browser_window(core: &CoreState, payload: &Value) -> Result<(String, WebviewWindow), String> {
    let Some(app) = core.app.clone() else {
        return Err("app handle not ready".into());
    };
    let label = target_browser_label(core, payload)?;
    let Some(window) = app.get_webview_window(&label) else {
        return Err(format!("browser surface is not open: {label}"));
    };
    Ok((label, window))
}

fn web_surface_open(core: &mut CoreState, payload: &Value) -> Result<Value, String> {
    spawn_browser_surface(core, payload)
}

fn web_surface_navigate(core: &mut CoreState, payload: &Value) -> Result<Value, String> {
    let raw_url = payload_string(payload, "url").ok_or_else(|| "missing url".to_string())?;
    let url = parse_browser_url(&raw_url, false)?;
    let (label, window) = browser_window(core, payload)?;
    window
        .navigate(url.clone())
        .map_err(|err| err.to_string())?;
    core.browser_label = Some(label.clone());
    Ok(json!({ "label": label, "url": url.as_str() }))
}

fn web_surface_reload(core: &mut CoreState, payload: &Value) -> Result<Value, String> {
    let (label, window) = browser_window(core, payload)?;
    window.reload().map_err(|err| err.to_string())?;
    Ok(json!({ "label": label, "reloaded": true }))
}

fn js_string(value: &str) -> String {
    serde_json::to_string(value).unwrap_or_else(|_| "\"\"".into())
}

fn browser_eval(
    core: &mut CoreState,
    payload: &Value,
    script: String,
) -> Result<(String, usize), String> {
    let (label, window) = browser_window(core, payload)?;
    let bytes = script.len();
    window.eval(script).map_err(|err| err.to_string())?;
    Ok((label, bytes))
}

fn web_cdp_click(core: &mut CoreState, payload: &Value) -> Result<Value, String> {
    let selector =
        payload_string(payload, "selector").ok_or_else(|| "missing selector".to_string())?;
    let script = format!(
        "(() => {{ const el = document.querySelector({}); if (!el) throw new Error('selector not found'); el.scrollIntoView({{ block: 'center', inline: 'center' }}); el.click(); }})();",
        js_string(&selector)
    );
    let (label, bytes) = browser_eval(core, payload, script)?;
    Ok(json!({ "label": label, "selector": selector, "scriptBytes": bytes }))
}

fn web_cdp_type(core: &mut CoreState, payload: &Value) -> Result<Value, String> {
    let selector =
        payload_string(payload, "selector").ok_or_else(|| "missing selector".to_string())?;
    let text = payload_string(payload, "text").unwrap_or_default();
    let script = format!(
        "(() => {{ const el = document.querySelector({}); if (!el) throw new Error('selector not found'); const value = {}; const proto = Object.getPrototypeOf(el); const descriptor = proto ? Object.getOwnPropertyDescriptor(proto, 'value') : null; if (descriptor && descriptor.set) {{ descriptor.set.call(el, value); }} else if ('value' in el) {{ el.value = value; }} else {{ el.textContent = value; }} el.dispatchEvent(new Event('input', {{ bubbles: true }})); el.dispatchEvent(new Event('change', {{ bubbles: true }})); }})();",
        js_string(&selector),
        js_string(&text)
    );
    let (label, bytes) = browser_eval(core, payload, script)?;
    Ok(
        json!({ "label": label, "selector": selector, "chars": text.chars().count(), "scriptBytes": bytes }),
    )
}

fn web_cdp_scroll(core: &mut CoreState, payload: &Value) -> Result<Value, String> {
    let coordinate_x = payload
        .get("x")
        .or_else(|| payload.get("dx"))
        .and_then(Value::as_i64)
        .unwrap_or(0);
    let coordinate_y = payload
        .get("y")
        .or_else(|| payload.get("dy"))
        .and_then(Value::as_i64)
        .unwrap_or(640);
    let script = format!(
        "window.scrollBy({{ left: {coordinate_x}, top: {coordinate_y}, behavior: 'smooth' }});"
    );
    let (label, bytes) = browser_eval(core, payload, script)?;
    Ok(json!({ "label": label, "x": coordinate_x, "y": coordinate_y, "scriptBytes": bytes }))
}

#[cfg(windows)]
unsafe extern "system" fn enum_window(hwnd: HWND, lparam: LPARAM) -> i32 {
    let windows = &mut *(lparam as *mut Vec<WindowInfo>);
    if IsWindowVisible(hwnd) == 0 {
        return TRUE;
    }

    let title_len = GetWindowTextLengthW(hwnd);
    if title_len <= 0 {
        return TRUE;
    }
    let mut buffer = vec![0u16; title_len as usize + 1];
    let read = GetWindowTextW(hwnd, buffer.as_mut_ptr(), buffer.len() as i32);
    if read <= 0 {
        return TRUE;
    }
    let title = String::from_utf16_lossy(&buffer[..read as usize])
        .trim()
        .to_string();
    if title.is_empty() {
        return TRUE;
    }

    let mut pid = 0u32;
    GetWindowThreadProcessId(hwnd, &mut pid);
    windows.push(WindowInfo {
        hwnd: hwnd as usize,
        pid,
        title,
        visible: true,
        focused: hwnd == GetForegroundWindow(),
    });
    TRUE
}

#[cfg(windows)]
fn window_inventory() -> Result<Vec<WindowInfo>, String> {
    let mut windows = Vec::new();
    unsafe {
        if EnumWindows(Some(enum_window), &mut windows as *mut _ as LPARAM) == 0 {
            return Err("EnumWindows failed".into());
        }
    }
    Ok(windows)
}

#[cfg(not(windows))]
fn window_inventory() -> Result<Vec<WindowInfo>, String> {
    Ok(Vec::new())
}

#[cfg(windows)]
fn utf16_array_to_string(values: &[u16]) -> String {
    let len = values
        .iter()
        .position(|value| *value == 0)
        .unwrap_or(values.len());
    String::from_utf16_lossy(&values[..len])
}

#[cfg(windows)]
fn process_inventory() -> Result<Vec<ProcessInfo>, String> {
    unsafe {
        let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
        if snapshot == INVALID_HANDLE_VALUE {
            return Err("CreateToolhelp32Snapshot failed".into());
        }
        let mut entry = std::mem::zeroed::<PROCESSENTRY32W>();
        entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;

        let mut processes = Vec::new();
        if Process32FirstW(snapshot, &mut entry) != 0 {
            loop {
                processes.push(ProcessInfo {
                    pid: entry.th32ProcessID,
                    parent_pid: entry.th32ParentProcessID,
                    exe: utf16_array_to_string(&entry.szExeFile),
                });
                if Process32NextW(snapshot, &mut entry) == 0 {
                    break;
                }
            }
        }
        let _ = CloseHandle(snapshot);
        Ok(processes)
    }
}

#[cfg(not(windows))]
fn process_inventory() -> Result<Vec<ProcessInfo>, String> {
    Ok(Vec::new())
}

fn payload_hwnd(payload: &Value) -> Result<usize, String> {
    payload
        .get("hwnd")
        .or_else(|| payload.get("targetHwnd"))
        .and_then(Value::as_u64)
        .map(|value| value as usize)
        .filter(|value| *value != 0)
        .ok_or_else(|| "missing hwnd".to_string())
}

fn payload_i32(payload: &Value, key: &str, default_value: i32) -> i32 {
    payload
        .get(key)
        .and_then(Value::as_i64)
        .and_then(|value| i32::try_from(value).ok())
        .unwrap_or(default_value)
}

#[cfg(windows)]
fn mouse_lparam(coordinate_x: i32, coordinate_y: i32) -> LPARAM {
    let low_word = (coordinate_x as u32) & 0xffff;
    let high_word = ((coordinate_y as u32) & 0xffff) << 16;
    (low_word | high_word) as LPARAM
}

#[cfg(windows)]
fn hwnd_from_usize(value: usize) -> HWND {
    value as HWND
}

#[cfg(windows)]
fn window_win32_control_click(payload: &Value) -> Result<Value, String> {
    let hwnd_value = payload_hwnd(payload)?;
    let coordinate_x = payload_i32(payload, "x", 8);
    let coordinate_y = payload_i32(payload, "y", 8);
    let hwnd = hwnd_from_usize(hwnd_value);
    let point = mouse_lparam(coordinate_x, coordinate_y);
    unsafe {
        if PostMessageW(hwnd, WM_MOUSEMOVE, 0, point) == 0 {
            return Err("WM_MOUSEMOVE failed".into());
        }
        if PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON_WPARAM, point) == 0 {
            return Err("WM_LBUTTONDOWN failed".into());
        }
        if PostMessageW(hwnd, WM_LBUTTONUP, 0, point) == 0 {
            return Err("WM_LBUTTONUP failed".into());
        }
    }
    Ok(json!({ "hwnd": hwnd_value, "x": coordinate_x, "y": coordinate_y, "posted": true }))
}

#[cfg(not(windows))]
fn window_win32_control_click(_payload: &Value) -> Result<Value, String> {
    Err("window.win32.control_click is only available on Windows".into())
}

#[cfg(windows)]
fn window_win32_set_text(payload: &Value) -> Result<Value, String> {
    let hwnd_value = payload_hwnd(payload)?;
    let text = payload_string(payload, "text").unwrap_or_default();
    let mut wide_text = text.encode_utf16().collect::<Vec<u16>>();
    wide_text.push(0);
    unsafe {
        SendMessageW(
            hwnd_from_usize(hwnd_value),
            WM_SETTEXT,
            0,
            wide_text.as_ptr() as LPARAM,
        );
    }
    Ok(json!({ "hwnd": hwnd_value, "chars": text.chars().count(), "sent": true }))
}

#[cfg(not(windows))]
fn window_win32_set_text(_payload: &Value) -> Result<Value, String> {
    Err("window.win32.set_text is only available on Windows".into())
}

fn cert_status() -> Value {
    let path = mitm_cert_path();
    json!({
        "available": path.is_file(),
        "path": path,
    })
}

fn open_mitm_cert() -> Result<Value, String> {
    let path = mitm_cert_path();
    if !path.is_file() {
        return Err(format!(
            "missing mitmproxy certificate: {}; start mitmproxy once to generate it",
            path.display()
        ));
    }

    #[cfg(windows)]
    {
        Command::new("cmd")
            .arg("/C")
            .arg("start")
            .arg("")
            .arg(&path)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|err| err.to_string())?;
    }

    #[cfg(not(windows))]
    {
        return Err("certificate open UX is only implemented on Windows".into());
    }

    Ok(json!({ "opened": true, "path": path }))
}

fn add_intercept_rule(
    core: &mut CoreState,
    payload: &Value,
    reason: Option<String>,
) -> Result<Value, String> {
    let host = payload_string(payload, "host").ok_or_else(|| "missing host".to_string())?;
    let id = payload_string(payload, "id").unwrap_or_else(|| format!("rule-{}", now_ms()));
    let path = payload_string(payload, "path");
    let enabled = payload
        .get("enabled")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let rule = InterceptRule {
        id: id.clone(),
        host,
        path,
        reason,
        enabled,
        created_ms: now_ms(),
        release_until_ms: None,
    };

    let mut rules = load_rules(&core.mitm.rules_path)?;
    rules.rules.retain(|existing| existing.id != id);
    rules.rules.push(rule.clone());
    core.mitm.rule_count = rules.rules.len();
    save_rules(&core.mitm.rules_path, &rules)?;
    Ok(json!({ "rule": rule, "rulesPath": core.mitm.rules_path }))
}

fn remove_intercept_rule(core: &mut CoreState, payload: &Value) -> Result<Value, String> {
    let id = payload_string(payload, "id");
    let host = payload_string(payload, "host");
    let path = payload_string(payload, "path");
    let mut rules = load_rules(&core.mitm.rules_path)?;
    let before = rules.rules.len();

    rules.rules.retain(|rule| {
        if let Some(id) = &id {
            return rule.id != *id;
        }
        if let Some(host) = &host {
            let same_host = rule.host.eq_ignore_ascii_case(host);
            let same_path = path
                .as_ref()
                .map(|value| rule.path.as_ref() == Some(value))
                .unwrap_or(true);
            return !(same_host && same_path);
        }
        true
    });

    let removed = before.saturating_sub(rules.rules.len());
    core.mitm.rule_count = rules.rules.len();
    save_rules(&core.mitm.rules_path, &rules)?;
    Ok(json!({ "removed": removed, "ruleCount": core.mitm.rule_count }))
}

fn release_intercept_rule(core: &mut CoreState, payload: &Value) -> Result<Value, String> {
    let id = payload_string(payload, "id").ok_or_else(|| "missing id".to_string())?;
    let minutes = payload
        .get("minutes")
        .and_then(Value::as_u64)
        .unwrap_or(10)
        .clamp(1, 240);
    let release_until_ms = now_ms() + u128::from(minutes) * 60_000;
    let mut rules = load_rules(&core.mitm.rules_path)?;
    let mut found = false;

    for rule in &mut rules.rules {
        if rule.id == id {
            rule.release_until_ms = Some(release_until_ms);
            found = true;
        }
    }
    if !found {
        return Err("rule not found".into());
    }

    save_rules(&core.mitm.rules_path, &rules)?;
    Ok(json!({ "id": id, "releaseUntilMs": release_until_ms }))
}

fn start_mitm(core: &mut CoreState) -> Result<Value, String> {
    refresh_mitm_status(core);
    if core.mitm.running {
        return Ok(json!({ "running": true, "pid": core.mitm.pid, "port": core.mitm.port }));
    }
    if !core.mitm.script_path.is_file() {
        return Err(format!(
            "missing mitmproxy script: {}",
            core.mitm.script_path.display()
        ));
    }
    if !core.mitm.rules_path.is_file() {
        save_rules(&core.mitm.rules_path, &empty_rules())?;
    }

    let child = Command::new(&core.mitm.mitmdump_path)
        .arg("-s")
        .arg(&core.mitm.script_path)
        .arg("--listen-port")
        .arg(core.mitm.port.to_string())
        .arg("--set")
        .arg("block_global=false")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|err| err.to_string())?;

    let pid = child.id();
    core.mitm.child = Some(child);
    core.mitm.running = true;
    core.mitm.pid = Some(pid);
    core.mitm.last_error = None;
    Ok(json!({ "running": true, "pid": pid, "port": core.mitm.port }))
}

fn stop_mitm(core: &mut CoreState) -> Result<Value, String> {
    if let Some(mut child) = core.mitm.child.take() {
        let _ = child.kill();
        let _ = child.wait();
    }
    core.mitm.running = false;
    core.mitm.pid = None;
    Ok(json!({ "running": false }))
}

fn toggle_mitm(core: &mut CoreState, payload: &Value) -> Result<Value, String> {
    let should_run = payload
        .get("running")
        .or_else(|| payload.get("enabled"))
        .and_then(Value::as_bool)
        .unwrap_or(!core.mitm.running);
    if should_run {
        start_mitm(core)
    } else {
        stop_mitm(core)
    }
}

fn execute_capability(
    core: &mut CoreState,
    name: &str,
    payload: &Value,
    reason: Option<String>,
) -> Result<ExecutionOutcome, String> {
    let result = match name {
        "web.intercept.add" => add_intercept_rule(core, payload, reason)?,
        "web.intercept.remove" => remove_intercept_rule(core, payload)?,
        "web.intercept.release" => release_intercept_rule(core, payload)?,
        "web.surface.open" => web_surface_open(core, payload)?,
        "web.surface.navigate" => web_surface_navigate(core, payload)?,
        "web.surface.reload" => web_surface_reload(core, payload)?,
        "web.cdp.click" => web_cdp_click(core, payload)?,
        "web.cdp.type" => web_cdp_type(core, payload)?,
        "web.cdp.scroll" => web_cdp_scroll(core, payload)?,
        "window.win32.control_click" => window_win32_control_click(payload)?,
        "window.win32.set_text" => window_win32_set_text(payload)?,
        "mitm.toggle" => toggle_mitm(core, payload)?,
        "mitm.cert.status" => cert_status(),
        "mitm.cert.install" => open_mitm_cert()?,
        "proxy.system.apply" => apply_system_proxy(core)?,
        "proxy.system.restore" => restore_system_proxy(core)?,
        "window.list" => json!({ "windows": window_inventory()? }),
        "process.list" => json!({ "processes": process_inventory()? }),
        "app.spawn" => spawn_app_surface(core, payload)?,
        "reader.set_text" => reader_set_text(core, payload)?,
        "reader.append_text" => reader_append_text(core, payload)?,
        _ => json!({ "mode": "m1_audit_only" }),
    };
    let status = if name.starts_with("web.")
        || name.starts_with("window.win32.")
        || name == "mitm.toggle"
        || name.starts_with("mitm.cert.")
        || name.starts_with("proxy.system.")
    {
        "executed"
    } else {
        "accepted"
    };
    Ok(ExecutionOutcome {
        status: status.into(),
        result,
    })
}

fn publish_result_via_state(state: &SharedState, result: &DispatchResult) {
    let publisher = state.lock().ok().and_then(|core| {
        core.mqtt
            .client
            .as_ref()
            .map(|client| (client.clone(), core.mqtt.settings.clone()))
    });
    let Some((client, settings)) = publisher else {
        return;
    };

    match publish_mqtt_result(&client, &settings, result) {
        Ok(()) => update_mqtt_state(state, |mqtt| {
            mqtt.published += 1;
            mqtt.last_error = None;
        }),
        Err(error) => update_mqtt_state(state, |mqtt| {
            mqtt.last_error = Some(error);
        }),
    }
}

impl DispatchMode {
    fn event(self) -> &'static str {
        match self {
            DispatchMode::DryRun => "dispatch.dry_run",
            DispatchMode::Execute => "dispatch.execute",
            DispatchMode::Confirmed => "dispatch.confirmed",
        }
    }

    fn label(self) -> &'static str {
        match self {
            DispatchMode::DryRun => "dry_run",
            DispatchMode::Execute => "m1_audit_only",
            DispatchMode::Confirmed => "confirmed",
        }
    }
}

fn run_dispatch(
    request: DispatchRequest,
    state: &SharedState,
    mode: DispatchMode,
) -> Result<DispatchResult, String> {
    let audit_id_value = audit_id();
    let capability_name = request.capability.clone();
    let reason = request.reason.clone();
    let payload = request.payload.clone();

    let (root, result, detail) = {
        let mut core = state.lock().map_err(|err| err.to_string())?;
        refresh_mitm_status(&mut core);
        let capability = core.registry.capabilities.get(&capability_name).cloned();
        let mut result = DispatchResult {
            id: request.id.clone(),
            ok: false,
            status: "unknown_capability".into(),
            audit_id: audit_id_value.clone(),
            capability: Some(capability_name.clone()),
            trust: capability.as_ref().map(|capability| capability.trust),
            kind: capability
                .as_ref()
                .map(|capability| capability.kind.clone()),
            result: json!({ "mode": mode.label() }),
        };

        if let Some(capability) = &capability {
            result.status = if !capability.enabled {
                "disabled".into()
            } else if capability.kind == "actuator" && core.panic {
                "panic_active".into()
            } else if capability.kind == "actuator" && core.paused {
                "paused".into()
            } else {
                result.ok = true;
                "accepted".into()
            };
        }

        if result.ok && matches!(mode, DispatchMode::Execute) && result.trust.unwrap_or(0) >= 2 {
            let pending = PendingDispatch {
                request: request.clone(),
                trust: result.trust.unwrap_or(0),
                kind: result.kind.clone().unwrap_or_default(),
                reason: reason.clone(),
                source: "dispatch".into(),
                created_ms: now_ms(),
            };
            core.pending.insert(request.id.clone(), pending);
            result.ok = false;
            result.status = "pending_confirmation".into();
            result.result = json!({
                "confirmationId": request.id,
                "trust": result.trust,
                "mode": "pending_confirmation",
            });
        } else if result.ok && matches!(mode, DispatchMode::Execute | DispatchMode::Confirmed) {
            match execute_capability(&mut core, &capability_name, &payload, reason.clone()) {
                Ok(outcome) => {
                    result.status = outcome.status;
                    result.result = outcome.result;
                }
                Err(error) => {
                    result.ok = false;
                    result.status = "execution_failed".into();
                    result.result = json!({ "error": error });
                }
            }
        }

        let detail = json!({
            "requestId": request.id,
            "payload": payload,
            "paused": core.paused,
            "panic": core.panic,
            "status": result.status,
            "mode": mode.label(),
        });
        (core.root.clone(), result, detail)
    };

    write_audit(
        &root,
        AuditEntry {
            id: audit_id_value,
            ts_ms: now_ms(),
            event: mode.event().into(),
            ok: result.ok,
            capability: Some(capability_name),
            reason,
            detail,
        },
    )?;
    Ok(result)
}

fn set_pause_state(
    state: &SharedState,
    paused: bool,
    reason: Option<String>,
) -> Result<(), String> {
    let (root, previous) = {
        let mut core = state.lock().map_err(|err| err.to_string())?;
        let previous = core.paused;
        core.paused = paused;
        (core.root.clone(), previous)
    };

    write_audit(
        &root,
        AuditEntry {
            id: audit_id(),
            ts_ms: now_ms(),
            event: "pause.set".into(),
            ok: true,
            capability: None,
            reason,
            detail: json!({ "previous": previous, "paused": paused }),
        },
    )?;
    Ok(())
}

#[tauri::command]
fn get_status(state: State<'_, SharedState>) -> Result<StatusView, String> {
    let mut core = state.lock().map_err(|err| err.to_string())?;
    refresh_mitm_status(&mut core);
    Ok(status_view(&core))
}

#[tauri::command]
fn list_capabilities(state: State<'_, SharedState>) -> Result<Vec<CapabilityView>, String> {
    let core = state.lock().map_err(|err| err.to_string())?;
    Ok(registry_views(&core.registry))
}

#[tauri::command]
fn list_pending(state: State<'_, SharedState>) -> Result<Vec<PendingView>, String> {
    let core = state.lock().map_err(|err| err.to_string())?;
    Ok(pending_views(&core))
}

#[tauri::command]
fn list_intercepts(state: State<'_, SharedState>) -> Result<Vec<InterceptHit>, String> {
    let core = state.lock().map_err(|err| err.to_string())?;
    load_intercepts(&intercepts_path(&core.root))
}

#[tauri::command]
fn clear_intercepts(state: State<'_, SharedState>) -> Result<(), String> {
    let root = {
        let core = state.lock().map_err(|err| err.to_string())?;
        core.root.clone()
    };
    let path = intercepts_path(&root);
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    fs::write(&path, "").map_err(|err| err.to_string())?;
    write_audit(
        &root,
        AuditEntry {
            id: audit_id(),
            ts_ms: now_ms(),
            event: "intercepts.clear".into(),
            ok: true,
            capability: None,
            reason: Some("ui".into()),
            detail: json!({ "path": path }),
        },
    )?;
    Ok(())
}

#[tauri::command]
fn list_windows() -> Result<Vec<WindowInfo>, String> {
    window_inventory()
}

#[tauri::command]
fn list_processes() -> Result<Vec<ProcessInfo>, String> {
    process_inventory()
}

#[tauri::command]
fn reload_capabilities(state: State<'_, SharedState>) -> Result<StatusView, String> {
    let (root, status) = {
        let mut core = state.lock().map_err(|err| err.to_string())?;
        let registry = load_registry(&core.root)?;
        core.registry = registry;
        refresh_mitm_status(&mut core);
        let status = status_view(&core);
        (core.root.clone(), status)
    };

    write_audit(
        &root,
        AuditEntry {
            id: audit_id(),
            ts_ms: now_ms(),
            event: "capabilities.reload".into(),
            ok: true,
            capability: None,
            reason: None,
            detail: json!({ "capabilityCount": status.capability_count }),
        },
    )?;
    Ok(status)
}

#[tauri::command]
fn set_paused(
    paused: bool,
    reason: Option<String>,
    state: State<'_, SharedState>,
) -> Result<(), String> {
    set_pause_state(state.inner(), paused, reason)
}

#[tauri::command]
fn panic_stop(reason: Option<String>, state: State<'_, SharedState>) -> Result<(), String> {
    let root = {
        let mut core = state.lock().map_err(|err| err.to_string())?;
        core.panic = true;
        core.paused = true;
        core.root.clone()
    };

    write_audit(
        &root,
        AuditEntry {
            id: audit_id(),
            ts_ms: now_ms(),
            event: "panic.set".into(),
            ok: true,
            capability: None,
            reason,
            detail: json!({ "panic": true, "paused": true }),
        },
    )?;
    Ok(())
}

#[tauri::command]
fn clear_panic(reason: Option<String>, state: State<'_, SharedState>) -> Result<(), String> {
    let root = {
        let mut core = state.lock().map_err(|err| err.to_string())?;
        core.panic = false;
        core.root.clone()
    };

    write_audit(
        &root,
        AuditEntry {
            id: audit_id(),
            ts_ms: now_ms(),
            event: "panic.clear".into(),
            ok: true,
            capability: None,
            reason,
            detail: json!({ "panic": false }),
        },
    )?;
    Ok(())
}

#[tauri::command]
fn dry_run_dispatch(
    request: DispatchRequest,
    state: State<'_, SharedState>,
) -> Result<DispatchResult, String> {
    run_dispatch(request, state.inner(), DispatchMode::DryRun)
}

#[tauri::command]
fn dispatch_once(
    request: DispatchRequest,
    state: State<'_, SharedState>,
) -> Result<DispatchResult, String> {
    run_dispatch(request, state.inner(), DispatchMode::Execute)
}

#[tauri::command]
fn approve_pending(id: String, state: State<'_, SharedState>) -> Result<DispatchResult, String> {
    let request = {
        let mut core = state.lock().map_err(|err| err.to_string())?;
        let Some(pending) = core.pending.remove(&id) else {
            return Err("pending request not found".into());
        };
        pending.request
    };

    let result = run_dispatch(request, state.inner(), DispatchMode::Confirmed)?;
    publish_result_via_state(state.inner(), &result);
    Ok(result)
}

#[tauri::command]
fn deny_pending(
    id: String,
    reason: Option<String>,
    state: State<'_, SharedState>,
) -> Result<DispatchResult, String> {
    let audit_id_value = audit_id();
    let (root, pending) = {
        let mut core = state.lock().map_err(|err| err.to_string())?;
        let Some(pending) = core.pending.remove(&id) else {
            return Err("pending request not found".into());
        };
        (core.root.clone(), pending)
    };

    let result = DispatchResult {
        id: pending.request.id.clone(),
        ok: false,
        status: "denied".into(),
        audit_id: audit_id_value.clone(),
        capability: Some(pending.request.capability.clone()),
        trust: Some(pending.trust),
        kind: Some(pending.kind.clone()),
        result: json!({ "reason": reason }),
    };

    write_audit(
        &root,
        AuditEntry {
            id: audit_id_value,
            ts_ms: now_ms(),
            event: "dispatch.denied".into(),
            ok: false,
            capability: Some(pending.request.capability),
            reason,
            detail: json!({
                "requestId": result.id,
                "trust": pending.trust,
                "source": pending.source,
            }),
        },
    )?;
    publish_result_via_state(state.inner(), &result);
    Ok(result)
}

#[tauri::command]
fn set_mitm_running(running: bool, state: State<'_, SharedState>) -> Result<StatusView, String> {
    let (root, detail, status) = {
        let mut core = state.lock().map_err(|err| err.to_string())?;
        let detail = if running {
            start_mitm(&mut core)?
        } else {
            stop_mitm(&mut core)?
        };
        let status = status_view(&core);
        (core.root.clone(), detail, status)
    };

    write_audit(
        &root,
        AuditEntry {
            id: audit_id(),
            ts_ms: now_ms(),
            event: if running { "mitm.start" } else { "mitm.stop" }.into(),
            ok: true,
            capability: Some("mitm.toggle".into()),
            reason: Some("ui".into()),
            detail,
        },
    )?;
    Ok(status)
}

fn bad_payload_result(error: String) -> DispatchResult {
    DispatchResult {
        id: dispatch_id(),
        ok: false,
        status: "bad_payload".into(),
        audit_id: audit_id(),
        capability: None,
        trust: None,
        kind: None,
        result: json!({ "error": error }),
    }
}

fn publish_mqtt_result(
    client: &Client,
    settings: &MqttSettings,
    result: &DispatchResult,
) -> Result<(), String> {
    let payload = json!({
        "id": result.id,
        "ok": result.ok,
        "status": result.status,
        "audit_id": result.audit_id,
        "capability": result.capability,
        "trust": result.trust,
        "kind": result.kind,
        "result": result.result,
    });
    let body = serde_json::to_vec(&payload).map_err(|err| err.to_string())?;
    client
        .publish(&settings.result_topic, QoS::AtLeastOnce, false, body)
        .map_err(|err| err.to_string())
}

fn handle_mqtt_publish(
    state: &SharedState,
    client: &Client,
    settings: &MqttSettings,
    publish: Publish,
) {
    update_mqtt_state(state, |mqtt| {
        mqtt.received += 1;
    });

    let result = match serde_json::from_slice::<DispatchRequest>(&publish.payload) {
        Ok(request) => run_dispatch(request, state, DispatchMode::Execute)
            .unwrap_or_else(|error| bad_payload_result(error)),
        Err(error) => {
            let result = bad_payload_result(error.to_string());
            write_runtime_audit(
                state,
                "dispatch.bad_payload",
                false,
                Some(publish.topic.clone()),
                json!({ "error": error.to_string() }),
            );
            result
        }
    };

    match publish_mqtt_result(client, settings, &result) {
        Ok(()) => update_mqtt_state(state, |mqtt| {
            mqtt.published += 1;
            mqtt.last_error = None;
        }),
        Err(error) => update_mqtt_state(state, |mqtt| {
            mqtt.last_error = Some(error);
        }),
    }
}

fn start_mqtt_worker(state: SharedState) {
    let settings = match state.lock() {
        Ok(core) => core.mqtt.settings.clone(),
        Err(_) => return,
    };
    if !settings.enabled {
        return;
    }

    update_mqtt_state(&state, |mqtt| {
        mqtt.status = "connecting".into();
        mqtt.last_error = None;
    });

    thread::spawn(move || {
        write_runtime_audit(
            &state,
            "mqtt.start",
            true,
            None,
            json!({
                "host": settings.host,
                "port": settings.port,
                "dispatchTopic": settings.dispatch_topic,
                "resultTopic": settings.result_topic,
            }),
        );

        let mut options = MqttOptions::new(
            settings.client_id.clone(),
            settings.host.clone(),
            settings.port,
        );
        options.set_keep_alive(Duration::from_secs(settings.keepalive));
        options.set_clean_session(false);

        let (client, mut connection) = Client::new(options, 10);
        let wildcard_topic = format!("{}/+", settings.dispatch_topic);
        for topic in [&settings.dispatch_topic, &wildcard_topic] {
            if let Err(error) = client.subscribe(topic, QoS::AtLeastOnce) {
                update_mqtt_state(&state, |mqtt| {
                    mqtt.status = "subscribe_error".into();
                    mqtt.last_error = Some(error.to_string());
                });
                return;
            }
        }
        update_mqtt_state(&state, |mqtt| {
            mqtt.client = Some(client.clone());
        });

        for notification in connection.iter() {
            match notification {
                Ok(Event::Incoming(Incoming::ConnAck(_))) => {
                    update_mqtt_state(&state, |mqtt| {
                        mqtt.connected = true;
                        mqtt.status = "connected".into();
                        mqtt.last_error = None;
                    });
                    write_runtime_audit(&state, "mqtt.connected", true, None, json!({}));
                }
                Ok(Event::Incoming(Incoming::Disconnect)) => {
                    update_mqtt_state(&state, |mqtt| {
                        mqtt.connected = false;
                        mqtt.status = "disconnected".into();
                    });
                }
                Ok(Event::Incoming(Incoming::Publish(publish))) => {
                    handle_mqtt_publish(&state, &client, &settings, publish);
                }
                Ok(_) => {}
                Err(error) => {
                    let message = error.to_string();
                    update_mqtt_state(&state, |mqtt| {
                        mqtt.connected = false;
                        mqtt.status = "error".into();
                        mqtt.last_error = Some(message.clone());
                    });
                    write_runtime_audit(
                        &state,
                        "mqtt.error",
                        false,
                        None,
                        json!({ "error": message }),
                    );
                }
            }
        }

        update_mqtt_state(&state, |mqtt| {
            mqtt.client = None;
            mqtt.connected = false;
            mqtt.status = "stopped".into();
        });
    });
}

fn setup_tray(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let show = MenuItem::with_id(app, "show", "Show Atrium", true, None::<&str>)?;
    let pause = MenuItem::with_id(app, "toggle_pause", "Toggle Pause", true, None::<&str>)?;
    let panic = MenuItem::with_id(app, "panic", "Panic", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&show, &pause, &panic, &quit])?;

    let mut builder = TrayIconBuilder::new()
        .tooltip("Atrium")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "show" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            "toggle_pause" => {
                let state = app.state::<SharedState>();
                let next = state.lock().map(|core| !core.paused).unwrap_or(true);
                let _ = set_pause_state(state.inner(), next, Some("tray".into()));
            }
            "panic" => {
                let state = app.state::<SharedState>();
                let _ = panic_stop(Some("tray".into()), state);
            }
            "quit" => app.exit(0),
            _ => {}
        });

    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }

    builder.build(app)?;
    Ok(())
}

fn main() {
    let root = project_root();
    let registry = load_registry(&root).expect("failed to load capabilities.toml");
    let mqtt = MqttRuntime::new(load_mqtt_settings(&root));
    let mitm = MitmRuntime::new(&root);
    let proxy = ProxyRuntime::new(&root);

    tauri::Builder::default()
        .manage(Arc::new(Mutex::new(CoreState {
            root,
            app: None,
            reader_label: None,
            browser_label: None,
            registry,
            paused: false,
            panic: false,
            mqtt,
            mitm,
            proxy,
            pending: BTreeMap::new(),
        })))
        .setup(|app| {
            setup_tray(app)?;
            let state = app.state::<SharedState>().inner().clone();
            if let Ok(mut core) = state.lock() {
                core.app = Some(app.handle().clone());
            }
            start_mqtt_worker(state);
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            get_status,
            list_capabilities,
            list_pending,
            list_intercepts,
            list_windows,
            list_processes,
            clear_intercepts,
            reload_capabilities,
            set_paused,
            panic_stop,
            clear_panic,
            dry_run_dispatch,
            dispatch_once,
            approve_pending,
            deny_pending,
            set_mitm_running
        ])
        .run(tauri::generate_context!())
        .expect("error while running atrium");
}
