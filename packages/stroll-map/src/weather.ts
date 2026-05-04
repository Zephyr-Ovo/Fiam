import type { StrollTrackPoint, WeatherKind, WeatherSnapshot } from "./types"

type OpenMeteoCurrent = {
  weather_code?: number
  rain?: number
  snowfall?: number
  precipitation?: number
}

type WeatherResponse = {
  kind?: WeatherKind
  intensity?: number
  current?: OpenMeteoCurrent
}

export async function fetchWeatherSnapshot(
  point: Pick<StrollTrackPoint, "lat" | "lng">,
  endpoint?: string,
  signal?: AbortSignal,
): Promise<WeatherSnapshot> {
  const url = endpoint ? weatherEndpointUrl(endpoint, point) : openMeteoUrl(point)
  const response = await fetch(url, { signal })
  if (!response.ok) throw new Error(`Weather request failed: ${response.status}`)
  const data = (await response.json()) as WeatherResponse

  if (data.kind) {
    return {
      kind: data.kind,
      intensity: clampIntensity(data.intensity ?? 0.32),
      source: endpoint ? "endpoint" : "open-meteo",
      observedAt: Date.now(),
    }
  }

  const current = data.current ?? {}
  return {
    kind: weatherCodeToKind(current.weather_code, current),
    intensity: weatherIntensity(current),
    source: endpoint ? "endpoint" : "open-meteo",
    observedAt: Date.now(),
  }
}

function openMeteoUrl(point: Pick<StrollTrackPoint, "lat" | "lng">) {
  const params = new URLSearchParams({
    latitude: String(point.lat),
    longitude: String(point.lng),
    current: "weather_code,precipitation,rain,snowfall,cloud_cover",
    timezone: "auto",
  })
  return `https://api.open-meteo.com/v1/forecast?${params}`
}

function weatherEndpointUrl(endpoint: string, point: Pick<StrollTrackPoint, "lat" | "lng">) {
  const url = new URL(endpoint, window.location.origin)
  url.searchParams.set("lat", String(point.lat))
  url.searchParams.set("lng", String(point.lng))
  return url.toString()
}

function weatherCodeToKind(code: number | undefined, current: OpenMeteoCurrent): WeatherKind {
  if ((current.snowfall ?? 0) > 0 || (code !== undefined && code >= 71 && code <= 86)) return "snow"
  if ((current.rain ?? 0) > 0 || (current.precipitation ?? 0) > 0) return "rain"
  if (code !== undefined && ((code >= 51 && code <= 67) || (code >= 80 && code <= 82) || code >= 95)) return "rain"
  return "clear"
}

function weatherIntensity(current: OpenMeteoCurrent) {
  const precipitation = Math.max(current.precipitation ?? 0, current.rain ?? 0, current.snowfall ?? 0)
  return clampIntensity(precipitation > 0 ? precipitation / 6 : 0.24)
}

function clampIntensity(value: number) {
  return Math.max(0.18, Math.min(0.72, value))
}
