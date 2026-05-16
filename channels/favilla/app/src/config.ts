/**
 * App configuration (decoupled from hardcoded values).
 *
 * Defaults can be overridden at runtime by writing to `localStorage["favilla:config"]`
 * as a JSON object with any subset of these keys. A future settings page will UI this.
 *
 * Chat background swap:
 * - Bundled default lives at `src/assets/brand/bg.jpg` (imported below).
 * - Runtime override: drop any image at `public/bg.jpg` and set
 *   `localStorage["favilla:config"] = '{"bg":"/bg.jpg"}'` (no rebuild required).
 * - Or override with any remote URL via the same `bg` key.
 */

import bgDefault from "./assets/brand/bg.jpg"
import { loadBgImage, saveBgImage, loadImage } from "./lib/bg-store"

/** Sentinel stored in the (size-limited) config blob when the real
 *  background image lives in IndexedDB. Resolved to the actual data URI at
 *  runtime so the persisted config stays tiny. */
export const BG_IDB = "idb:bg"

/** Same IndexedDB-sentinel scheme for the user / AI avatars. */
export const AVATAR_USER_IDB = "idb:avatar:user"
export const AVATAR_AI_IDB = "idb:avatar:ai"
export const AVATAR_USER_KEY = "avatar:user"
export const AVATAR_AI_KEY = "avatar:ai"

export type AppConfig = {
  /** Display name of the user (currently unused but reserved). */
  userName: string
  /** Display name of the assistant (used in UI labels). */
  aiName: string
  /** Background image URL (imported asset or remote URL). */
  bg: string
  /** Chat user bubble background (any CSS color). */
  userBubbleBg: string
  /** Chat agent bubble background (any CSS color). */
  agentBubbleBg: string
  /** Brand/theme color (drives --color-cocoa: send button, accents). */
  themeColor: string
  /** API base for the Favilla server. Empty means same-origin/Vite proxy. */
  apiBase: string
  /** Auth token for the Favilla server. */
  ingestToken: string
  /** Local Limen/XIAO HTTP base URL, e.g. http://192.168.39.19. */
  limenBaseUrl: string
  /** Default runtime used by sendChat: "auto" | "cc" | "api". */
  defaultRuntime: "auto" | "cc" | "api"
  /** STT provider: browser or OpenAI-compatible endpoint. */
  sttProvider: "browser" | "openai_compatible"
  /** STT endpoint base URL. */
  sttBaseUrl: string
  /** STT API key (stored locally for now). */
  sttApiKey: string
  /** STT model name for compatible endpoints. */
  sttModel: string
  /** TTS provider: browser, OpenAI-compatible, or mimo. */
  ttsProvider: "browser" | "openai_compatible" | "mimo"
  /** TTS endpoint base URL. */
  ttsBaseUrl: string
  /** TTS API key (stored locally for now). */
  ttsApiKey: string
  /** TTS model name for remote providers. */
  ttsModel: string
  /** TTS voice id/name. */
  ttsVoice: string
  /** Auto-speak latest AI reply when available. */
  ttsAutoPlayAi: boolean
  /** User avatar (IndexedDB sentinel or data URI). Empty = initial letter. */
  userAvatar: string
  /** AI avatar (IndexedDB sentinel or data URI). Empty = initial letter. */
  aiAvatar: string
}

const defaults: AppConfig = {
  userName: "Zephyr",
  aiName: "ai",
  bg: bgDefault,
  userBubbleBg: "#d0bcbe",
  agentBubbleBg: "#f5f5f5",
  themeColor: "#664E44",
  apiBase: (import.meta.env.VITE_API_BASE as string) ?? "",
  ingestToken: (import.meta.env.VITE_INGEST_TOKEN as string) ?? "",
  limenBaseUrl: (import.meta.env.VITE_LIMEN_BASE_URL as string) ?? "",
  defaultRuntime: "auto",
  sttProvider: "browser",
  sttBaseUrl: "",
  sttApiKey: "",
  sttModel: "whisper-1",
  ttsProvider: "browser",
  ttsBaseUrl: "",
  ttsApiKey: "",
  ttsModel: "gpt-4o-mini-tts",
  ttsVoice: "",
  ttsAutoPlayAi: false,
  userAvatar: "",
  aiAvatar: "",
}

const STORAGE_KEY = "favilla:config"
const INVALID_TOKEN_OVERRIDES = new Set(["", "test-token"])

function loadOverrides(): Partial<AppConfig> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== "object") return {}
    const overrides = { ...parsed } as Partial<AppConfig>
    if (typeof overrides.apiBase === "string") {
      const apiBase = overrides.apiBase.trim()
      if (!apiBase && defaults.apiBase) delete overrides.apiBase
      else overrides.apiBase = apiBase
    }
    if (typeof overrides.ingestToken === "string") {
      const ingestToken = overrides.ingestToken.trim()
      if (INVALID_TOKEN_OVERRIDES.has(ingestToken)) delete overrides.ingestToken
      else overrides.ingestToken = ingestToken
    }
    if (typeof overrides.sttBaseUrl === "string") overrides.sttBaseUrl = overrides.sttBaseUrl.trim()
    if (typeof overrides.sttApiKey === "string") overrides.sttApiKey = overrides.sttApiKey.trim()
    if (typeof overrides.sttModel === "string") overrides.sttModel = overrides.sttModel.trim()
    if (typeof overrides.ttsBaseUrl === "string") overrides.ttsBaseUrl = overrides.ttsBaseUrl.trim()
    if (typeof overrides.ttsApiKey === "string") overrides.ttsApiKey = overrides.ttsApiKey.trim()
    if (typeof overrides.ttsModel === "string") overrides.ttsModel = overrides.ttsModel.trim()
    if (typeof overrides.ttsVoice === "string") overrides.ttsVoice = overrides.ttsVoice.trim()
    return overrides
  } catch {
    return {}
  }
}

export const appConfig: AppConfig = { ...defaults, ...loadOverrides() }

/** Push themable colors into CSS custom properties so all chat bubbles
 *  re-paint immediately when settings change — no React re-render dance.
 *  Safe to call repeatedly; idempotent. */
function applyThemeVars(cfg: AppConfig) {
  if (typeof document === "undefined") return
  try {
    document.documentElement.style.setProperty("--user-bubble-bg", cfg.userBubbleBg)
    document.documentElement.style.setProperty("--agent-bubble-bg", cfg.agentBubbleBg)
    document.documentElement.style.setProperty("--color-cocoa", cfg.themeColor)
  } catch {
    /* SSR / no DOM */
  }
}
/** If `bg` is the IndexedDB sentinel, fall back to the bundled default
 *  immediately, then asynchronously swap in the stored image and notify
 *  subscribers so the chat background repaints without a reload. */
function resolveBgSentinel() {
  if (appConfig.bg !== BG_IDB) return
  appConfig.bg = bgDefault
  loadBgImage()
    .then((data) => {
      if (!data) return
      appConfig.bg = data
      try {
        window.dispatchEvent(new CustomEvent("favilla:config-changed"))
      } catch {
        /* SSR / non-browser */
      }
    })
    .catch(() => {})
}

function migrateInlineBgToIndexedDb() {
  if (!String(appConfig.bg || "").startsWith("data:image/")) return
  const dataUri = appConfig.bg
  saveBgImage(dataUri)
    .then(() => {
      const overrides = { ...loadOverrides(), bg: BG_IDB }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(overrides))
      appConfig.bg = dataUri
      window.dispatchEvent(new CustomEvent("favilla:config-changed"))
    })
    .catch(() => {})
}

/** Swap the IndexedDB avatar sentinels for their real data URIs. */
function resolveAvatarSentinels() {
  const pairs: [keyof AppConfig, string, string][] = [
    ["userAvatar", AVATAR_USER_IDB, AVATAR_USER_KEY],
    ["aiAvatar", AVATAR_AI_IDB, AVATAR_AI_KEY],
  ]
  for (const [field, sentinel, key] of pairs) {
    if (appConfig[field] !== sentinel) continue
    ;(appConfig as Record<string, unknown>)[field] = ""
    loadImage(key)
      .then((data) => {
        if (!data) return
        ;(appConfig as Record<string, unknown>)[field] = data
        try {
          window.dispatchEvent(new CustomEvent("favilla:config-changed"))
        } catch {
          /* SSR / non-browser */
        }
      })
      .catch(() => {})
  }
}

applyThemeVars(appConfig)
resolveBgSentinel()
resolveAvatarSentinels()
migrateInlineBgToIndexedDb()

/** Persist a partial config patch and update the live `appConfig` object. */
export function saveConfig(patch: Partial<AppConfig>) {
  Object.assign(appConfig, patch)
  const merged = { ...loadOverrides(), ...patch }
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(merged))
  } catch (e) {
    console.warn("saveConfig: localStorage write failed, retrying without bg", e)
    const withoutBg = { ...merged }
    delete (withoutBg as Record<string, unknown>).bg
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(withoutBg)) } catch { /* give up */ }
  }
  applyThemeVars(appConfig)
  resolveBgSentinel()
  resolveAvatarSentinels()
  // Notify subscribers so live components (header peer name, etc.) re-read.
  try {
    window.dispatchEvent(new CustomEvent("favilla:config-changed"))
  } catch {
    /* SSR / non-browser */
  }
}
