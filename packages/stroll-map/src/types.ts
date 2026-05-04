export type StrollMapMode = "2d" | "3d"

export type WeatherKind = "clear" | "rain" | "snow"

export type CoordinateCorrection = "gcj02" | "none"

export type StrollTrackPoint = {
  id?: string
  lng: number
  lat: number
  t: number
  speed?: number
  accuracy?: number
  heading?: number
  source?: "phone" | "limen" | "replay"
}

export type RenderTrackPoint = StrollTrackPoint & {
  coordinate: [number, number]
  speedMps: number
  distanceM: number
  progress: number
}

export type WeatherSnapshot = {
  kind: WeatherKind
  intensity?: number
  source?: "fallback" | "open-meteo" | "endpoint"
  observedAt?: number
}

export type StrollMapLabel = {
  id: string
  lng: number
  lat: number
  text: string
  tone?: "start" | "current" | "note"
}