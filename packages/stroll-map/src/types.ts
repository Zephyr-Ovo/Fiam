export type WeatherKind = "clear" | "rain" | "snow"

export type CoordinateCorrection = "gcj02" | "none"

export type StrollPlaceKind = "road" | "green" | "building" | "water" | "unknown"

export type StrollOrigin = "user" | "ai" | "phone" | "limen" | "replay"

export type StrollTrackPoint = {
  id?: string
  lng: number
  lat: number
  t: number
  speed?: number
  accuracy?: number
  heading?: number
  source?: Extract<StrollOrigin, "phone" | "limen" | "replay">
  cellId?: string
  placeKind?: StrollPlaceKind
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
  source?: Extract<StrollOrigin, "phone" | "limen" | "replay">
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
  source?: StrollOrigin
  recordKind?: StrollSpatialRecordKind
  placeKind?: StrollPlaceKind
  origin?: StrollOrigin
  distanceM?: number
  radiusM?: number
  createdAt?: number
  updatedAt?: number
  attachment?: StrollPhotoRef
}

export type StrollSpatialRecordKind = "note" | "photo" | "marker" | "action"

export type StrollSpatialRecord = {
  id: string
  kind: StrollSpatialRecordKind
  lng: number
  lat: number
  cellId: string
  radiusM: number
  distanceM?: number
  bearingDeg?: number
  placeKind: StrollPlaceKind
  origin: StrollOrigin
  text?: string
  emoji?: string
  attachment?: StrollPhotoRef
  createdAt: number
  updatedAt: number
}

export type StrollNearbyMemory = {
  id: string
  lng: number
  lat: number
  radiusM: number
  cellId?: string
  distanceM?: number
  bearingDeg?: number
  placeKind?: StrollPlaceKind
  title: string
  lastSeenAt?: number
  sourceIds?: string[]
  origin?: StrollOrigin
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
  spatialRecords?: StrollSpatialRecord[]
  cellId?: string
  placeKind?: StrollPlaceKind
  contextVersion?: string
  weather?: WeatherSnapshot
  lightPreset?: "dawn" | "day" | "dusk" | "night"
}