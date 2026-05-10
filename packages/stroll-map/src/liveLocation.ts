import type { StrollTrackPoint } from "./types"

const liveTrackStorageKey = "favilla:stroll-live-track"
const minPointIntervalMs = 4_000
const maxStoredPoints = 900

export function loadStoredLiveTrack(): StrollTrackPoint[] {
  try {
    const raw = window.localStorage.getItem(liveTrackStorageKey)
    if (!raw) return []
    const parsed = JSON.parse(raw) as StrollTrackPoint[]
    return parsed.filter(isTrackPoint).slice(-maxStoredPoints)
  } catch {
    return []
  }
}

export function saveStoredLiveTrack(track: StrollTrackPoint[]) {
  try {
    window.localStorage.setItem(liveTrackStorageKey, JSON.stringify(track.slice(-maxStoredPoints)))
  } catch {
    return
  }
}

export function positionToTrackPoint(position: GeolocationPosition): StrollTrackPoint {
  const { coords, timestamp } = position
  return {
    id: `phone-${Math.round(timestamp)}`,
    lng: coords.longitude,
    lat: coords.latitude,
    t: timestamp,
    speed: coords.speed ?? undefined,
    accuracy: coords.accuracy,
    heading: coords.heading ?? undefined,
    source: "phone",
  }
}

export function appendLivePoint(track: StrollTrackPoint[], nextPoint: StrollTrackPoint) {
  const previousPoint = track[track.length - 1]
  if (previousPoint && nextPoint.t - previousPoint.t < minPointIntervalMs) return track
  return [...track, nextPoint].slice(-maxStoredPoints)
}

function isTrackPoint(point: StrollTrackPoint) {
  return (
    typeof point.lng === "number" &&
    typeof point.lat === "number" &&
    typeof point.t === "number" &&
    Number.isFinite(point.lng) &&
    Number.isFinite(point.lat) &&
    Number.isFinite(point.t)
  )
}
