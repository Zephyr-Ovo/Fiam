/**
 * App configuration (decoupled from hardcoded values).
 *
 * Defaults can be overridden at runtime by writing to `localStorage["favilla:config"]`
 * as a JSON object with any subset of these keys. A future settings page will UI this.
 */

import bgDefault from "./assets/brand/bg.jpg"

export type AppConfig = {
  /** Display name of the AI persona (header & namechip). */
  aiName: string
  /** Display name of the user (currently unused but reserved). */
  userName: string
  /** Background image URL (imported asset or remote URL). */
  bg: string
  /** API base for backend (proxied via vite dev server). */
  apiBase: string
  /** Auth token for backend. */
  ingestToken: string
  /** OpenRouter API key (sk-or-...). Sent to backend as X-OpenRouter-Key when set;
   *  backend falls back to its own env var if empty. */
  openrouterKey: string
  /** Default backend used by sendChat: "auto" | "cc" | "api". */
  defaultBackend: "auto" | "cc" | "api"
}

const defaults: AppConfig = {
  aiName: "Favilla",
  userName: "Zephyr",
  bg: bgDefault,
  apiBase: (import.meta.env.VITE_API_BASE as string) ?? (import.meta.env.VITE_API_TARGET as string) ?? "",
  ingestToken: (import.meta.env.VITE_INGEST_TOKEN as string) ?? "",
  openrouterKey: (import.meta.env.VITE_OPENROUTER_KEY as string) ?? "",
  defaultBackend: "auto",
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
