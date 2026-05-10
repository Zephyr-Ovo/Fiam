/**
 * App configuration (decoupled from hardcoded values).
 *
 * Defaults can be overridden at runtime by writing to `localStorage["favilla:config"]`
 * as a JSON object with any subset of these keys. A future settings page will UI this.
 */

import bgDefault from "./assets/brand/bg.jpg"

export type AppConfig = {
  /** Display name of the user (currently unused but reserved). */
  userName: string
  /** Display name of the assistant (used in UI labels). */
  aiName: string
  /** Background image URL (imported asset or remote URL). */
  bg: string
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

/** Persist a partial config patch and update the live `appConfig` object. */
export function saveConfig(patch: Partial<AppConfig>) {
  Object.assign(appConfig, patch)
  const merged = { ...loadOverrides(), ...patch }
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(merged))
  } catch {
    /* quota / privacy mode */
  }
  // Notify subscribers so live components (header peer name, etc.) re-read.
  try {
    window.dispatchEvent(new CustomEvent("favilla:config-changed"))
  } catch {
    /* SSR / non-browser */
  }
}
