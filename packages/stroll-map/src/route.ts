import type { Feature, FeatureCollection, LineString, Point } from "geojson"
import { toRenderCoordinate } from "./coordinates"
import type { CoordinateCorrection, RenderTrackPoint, StrollTrackPoint } from "./types"

const earthRadiusM = 6_371_000
const lowSpeedColor = "#506D99"
const cruiseSpeedColor = "#108B7F"
const highSpeedColor = "#E94F37"

export function normalizeTrack(track: StrollTrackPoint[], correction: CoordinateCorrection): RenderTrackPoint[] {
  if (track.length === 0) return []

  const distances: number[] = [0]
  let totalDistanceM = 0

  for (let index = 1; index < track.length; index += 1) {
    const previous = track[index - 1]
    const current = track[index]
    const segmentDistanceM = distanceMeters(previous, current)
    totalDistanceM += segmentDistanceM
    distances[index] = totalDistanceM
  }

  return track.map((point, index) => {
    const previous = index > 0 ? track[index - 1] : undefined
    const next = index < track.length - 1 ? track[index + 1] : undefined
    const derivedSpeed = deriveSpeed(point, previous, next)

    return {
      ...point,
      coordinate: toRenderCoordinate(point, correction),
      speedMps: point.speed ?? derivedSpeed,
      distanceM: distances[index] ?? 0,
      progress: totalDistanceM > 0 ? (distances[index] ?? 0) / totalDistanceM : 0,
    }
  })
}

export function buildRouteFeature(points: RenderTrackPoint[]): FeatureCollection<LineString> {
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: {},
        geometry: {
          type: "LineString",
          coordinates: points.map((point) => point.coordinate),
        },
      },
    ],
  }
}

export function buildTailFeature(points: RenderTrackPoint[], tailPointCount = 10): FeatureCollection<LineString> {
  return buildRouteFeature(points.slice(Math.max(points.length - tailPointCount, 0)))
}

export function buildFootstepFeatures(points: RenderTrackPoint[], tailPointCount = 9): FeatureCollection<Point> {
  const tail = points.slice(Math.max(points.length - tailPointCount - 1, 0), -1)
  const features: Array<Feature<Point>> = tail.map((point, index) => ({
    type: "Feature",
    properties: {
      age: tail.length > 1 ? index / (tail.length - 1) : 1,
      speed: point.speedMps,
    },
    geometry: {
      type: "Point",
      coordinates: point.coordinate,
    },
  }))

  return { type: "FeatureCollection", features }
}

export function buildSpeedGradient(points: RenderTrackPoint[]) {
  if (points.length < 2) {
    return ["interpolate", ["linear"], ["line-progress"], 0, cruiseSpeedColor, 1, highSpeedColor]
  }

  const maxSpeedMps = Math.max(...points.map((point) => point.speedMps), 1)
  const stops: Array<number | string> = []
  let lastProgress = 0

  points.forEach((point, index) => {
    const rawProgress = index === points.length - 1 ? 1 : point.progress
    const progress = index === 0 ? 0 : Math.min(1, Math.max(rawProgress, lastProgress + 0.001))
    lastProgress = progress
    stops.push(progress, speedColor(point.speedMps, maxSpeedMps))
  })

  return ["interpolate", ["linear"], ["line-progress"], ...stops]
}

export function summarizeTrack(track: StrollTrackPoint[]) {
  if (track.length < 2) return { distanceKm: 0, averageSpeedKmh: 0 }

  let totalDistanceM = 0
  for (let index = 1; index < track.length; index += 1) {
    totalDistanceM += distanceMeters(track[index - 1], track[index])
  }

  const firstPoint = track[0]
  const lastPoint = track[track.length - 1]
  const durationHours = Math.max((lastPoint.t - firstPoint.t) / 3_600_000, 1 / 3600)
  return {
    distanceKm: totalDistanceM / 1000,
    averageSpeedKmh: totalDistanceM / 1000 / durationHours,
  }
}

function deriveSpeed(point: StrollTrackPoint, previous?: StrollTrackPoint, next?: StrollTrackPoint) {
  const anchor = previous ?? next
  if (!anchor) return 0
  const durationSeconds = Math.max(Math.abs(point.t - anchor.t) / 1000, 1)
  return distanceMeters(point, anchor) / durationSeconds
}

function distanceMeters(first: Pick<StrollTrackPoint, "lng" | "lat">, second: Pick<StrollTrackPoint, "lng" | "lat">) {
  const firstLatRad = degreesToRadians(first.lat)
  const secondLatRad = degreesToRadians(second.lat)
  const latDeltaRad = degreesToRadians(second.lat - first.lat)
  const lngDeltaRad = degreesToRadians(second.lng - first.lng)
  const haversineSeed =
    Math.sin(latDeltaRad / 2) * Math.sin(latDeltaRad / 2) +
    Math.cos(firstLatRad) * Math.cos(secondLatRad) * Math.sin(lngDeltaRad / 2) * Math.sin(lngDeltaRad / 2)
  const angularDistance = 2 * Math.atan2(Math.sqrt(haversineSeed), Math.sqrt(1 - haversineSeed))
  return earthRadiusM * angularDistance
}

function degreesToRadians(degrees: number) {
  return (degrees * Math.PI) / 180
}

function speedColor(speedMps: number, maxSpeedMps: number) {
  const normalized = Math.max(0, Math.min(1, speedMps / maxSpeedMps))
  if (normalized < 0.52) return lowSpeedColor
  if (normalized < 0.76) return cruiseSpeedColor
  return highSpeedColor
}