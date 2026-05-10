import { distanceMeters } from "./route"
import type { StrollPlaceKind, StrollSpatialRecord, StrollTrackPoint } from "./types"

const defaultCellSizeM = 50
const latMeters = 111_320

export function strollCellId(point: Pick<StrollTrackPoint, "lng" | "lat">, cellSizeM = defaultCellSizeM) {
  const lngMeters = latMeters * Math.max(0.12, Math.cos((point.lat * Math.PI) / 180))
  const y = Math.floor((point.lat * latMeters) / cellSizeM)
  const x = Math.floor((point.lng * lngMeters) / cellSizeM)
  return `${cellSizeM}m:${y}:${x}`
}

export function strollNeighborCellIds(cellId: string) {
  const match = /^(\d+)m:([-\d]+):([-\d]+)$/.exec(cellId)
  if (!match) return [cellId]
  const size = match[1]
  const y = Number(match[2])
  const x = Number(match[3])
  const ids: string[] = []
  for (let dy = -1; dy <= 1; dy += 1) {
    for (let dx = -1; dx <= 1; dx += 1) ids.push(`${size}m:${y + dy}:${x + dx}`)
  }
  return ids
}

export function bearingDegrees(from: Pick<StrollTrackPoint, "lng" | "lat">, to: Pick<StrollTrackPoint, "lng" | "lat">) {
  const fromLat = toRadians(from.lat)
  const toLat = toRadians(to.lat)
  const lngDelta = toRadians(to.lng - from.lng)
  const y = Math.sin(lngDelta) * Math.cos(toLat)
  const x = Math.cos(fromLat) * Math.sin(toLat) - Math.sin(fromLat) * Math.cos(toLat) * Math.cos(lngDelta)
  return (toDegrees(Math.atan2(y, x)) + 360) % 360
}

export function normalizePlaceKind(value: unknown): StrollPlaceKind {
  return value === "road" || value === "green" || value === "building" || value === "water" ? value : "unknown"
}

export function withDistanceFromCurrent<T extends StrollSpatialRecord>(records: T[], current: Pick<StrollTrackPoint, "lng" | "lat">, radiusM = defaultCellSizeM) {
  return records
    .map((record) => ({
      ...record,
      distanceM: distanceMeters(current, record),
      bearingDeg: bearingDegrees(current, record),
    }))
    .filter((record) => record.distanceM <= radiusM)
    .sort((a, b) => (a.distanceM ?? 0) - (b.distanceM ?? 0))
}

function toRadians(degrees: number) {
  return (degrees * Math.PI) / 180
}

function toDegrees(radians: number) {
  return (radians * 180) / Math.PI
}