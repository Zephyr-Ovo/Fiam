import type { Feature, FeatureCollection, LineString, Point } from "geojson"
import { toRenderCoordinate } from "./coordinates"
import type { CoordinateCorrection, RenderTrackPoint, StrollTrackPoint } from "./types"

const earthRadiusM = 6_371_000
const cyclingSpeedCapMps = 6.8
const speedStops = [
  { speedMps: 0, color: "#6E45D9" },
  { speedMps: 1.7, color: "#EA5AA9" },
  { speedMps: 4.1, color: "#F3C84B" },
  { speedMps: cyclingSpeedCapMps, color: "#E23B3B" },
]

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
    return ["interpolate", ["linear"], ["line-progress"], 0, speedStops[0].color, 0.34, speedStops[1].color, 0.68, speedStops[2].color, 1, speedStops[3].color]
  }

  const stops: Array<number | string> = []
  let lastProgress = 0

  points.forEach((point, index) => {
    const rawProgress = index === points.length - 1 ? 1 : point.progress
    const progress = index === 0 ? 0 : Math.min(1, Math.max(rawProgress, lastProgress + 0.001))
    lastProgress = progress
    stops.push(progress, speedColor(point.speedMps))
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

export function distanceMeters(first: Pick<StrollTrackPoint, "lng" | "lat">, second: Pick<StrollTrackPoint, "lng" | "lat">) {
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

function speedColor(speedMps: number) {
  const clampedSpeed = Math.max(0, Math.min(cyclingSpeedCapMps, speedMps))
  const upperIndex = speedStops.findIndex((stop) => clampedSpeed <= stop.speedMps)
  if (upperIndex <= 0) return speedStops[0].color
  const lowerStop = speedStops[upperIndex - 1]
  const upperStop = speedStops[upperIndex]
  const ratio = (clampedSpeed - lowerStop.speedMps) / Math.max(upperStop.speedMps - lowerStop.speedMps, 0.001)
  return mixHexColor(lowerStop.color, upperStop.color, ratio)
}

function mixHexColor(firstColor: string, secondColor: string, ratio: number) {
  const firstRgb = hexToRgb(firstColor)
  const secondRgb = hexToRgb(secondColor)
  const mixed = firstRgb.map((channel, index) => Math.round(channel + (secondRgb[index] - channel) * ratio))
  return `#${mixed.map((channel) => channel.toString(16).padStart(2, "0")).join("")}`
}

function hexToRgb(color: string): [number, number, number] {
  const normalized = color.replace("#", "")
  return [
    Number.parseInt(normalized.slice(0, 2), 16),
    Number.parseInt(normalized.slice(2, 4), 16),
    Number.parseInt(normalized.slice(4, 6), 16),
  ]
}