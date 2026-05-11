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
  /** API base for the Favilla server (proxied via vite dev server). */
  apiBase: string
  /** Auth token for the Favilla server. */
  ingestToken: string
  /** Local Limen/XIAO HTTP base URL, e.g. http://192.168.39.19. */
  limenBaseUrl: string
  /** Default runtime used by sendChat: "auto" | "cc" | "api". */
  defaultRuntime: "auto" | "cc" | "api"
}

const defaults: AppConfig = {
  userName: "Zephyr",
  aiName: "ai",
  bg: bgDefault,
  userBubbleBg: "rgba(208,188,190,0.92)",
  agentBubbleBg: "rgba(245,245,245,0.88)",
  apiBase: (import.meta.env.VITE_API_BASE as string) ?? (import.meta.env.VITE_API_TARGET as string) ?? "",
  ingestToken: (import.meta.env.VITE_INGEST_TOKEN as string) ?? "",
  limenBaseUrl: (import.meta.env.VITE_LIMEN_BASE_URL as string) ?? "",
  defaultRuntime: "auto",
}

const STORAGE_KEY = "favilla:config"

function loadOverrides(): Partial<AppConfig> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === "object" ? parsed : {}
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
  } catch {
    /* SSR / no DOM */
  }
}
applyThemeVars(appConfig)

/** Persist a partial config patch and update the live `appConfig` object. */
export function saveConfig(patch: Partial<AppConfig>) {
  Object.assign(appConfig, patch)
  const merged = { ...loadOverrides(), ...patch }
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(merged))
  } catch {
    /* quota / privacy mode */
  }
  applyThemeVars(appConfig)
  // Notify subscribers so live components (header peer name, etc.) re-read.
  try {
    window.dispatchEvent(new CustomEvent("favilla:config-changed"))
  } catch {
    /* SSR / non-browser */
  }
}
