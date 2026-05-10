import gcoord from "gcoord"
import type { CoordinateCorrection, StrollTrackPoint } from "./types"

const mainlandChinaBounds = {
  minLng: 73.33,
  maxLng: 135.05,
  minLat: 3.51,
  maxLat: 53.56,
}

export function isInsideChinaBounds(lng: number, lat: number) {
  return (
    lng >= mainlandChinaBounds.minLng &&
    lng <= mainlandChinaBounds.maxLng &&
    lat >= mainlandChinaBounds.minLat &&
    lat <= mainlandChinaBounds.maxLat
  )
}

export function toRenderCoordinate(point: StrollTrackPoint, correction: CoordinateCorrection): [number, number] {
  if (correction !== "gcj02" || !isInsideChinaBounds(point.lng, point.lat)) {
    return [point.lng, point.lat]
  }

  return gcoord.transform([point.lng, point.lat], gcoord.WGS84, gcoord.GCJ02) as [number, number]
}