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

export type StrollPhotoRef = {
  id: string
  url?: string
  thumbUrl?: string
  takenAt?: number
  source?: "phone" | "limen" | "replay"
}

export type StrollPhotoMarkerInput = StrollPhotoRef & {
  lng: number
  lat: number
}

export type StrollMapAnnotation = {
  id: string
  kind: "photo" | "ai"
  lng: number
  lat: number
  text?: string
  emoji?: string
  photos?: StrollPhotoRef[]
  count?: number
  mergedRadiusM?: number
  source?: "user" | "ai" | "limen" | "replay"
}

export type StrollNearbyMemory = {
  id: string
  lng: number
  lat: number
  radiusM: number
  title: string
  lastSeenAt?: number
  sourceIds?: string[]
}

export type StrollSpatialContext = {
  current?: StrollTrackPoint
  route: {
    points: StrollTrackPoint[]
    distanceKm?: number
    averageSpeedKmh?: number
  }
  annotations: StrollMapAnnotation[]
  nearbyMemories?: StrollNearbyMemory[]
  weather?: WeatherSnapshot
  lightPreset?: "dawn" | "day" | "dusk" | "night"
}