import { distanceMeters } from "./route"
import type { StrollMapAnnotation, StrollPhotoMarkerInput, StrollPhotoRef } from "./types"

export const defaultPhotoMergeRadiusM = 20

type PhotoCluster = {
  lng: number
  lat: number
  photos: StrollPhotoMarkerInput[]
}

export function buildPhotoAnnotations(photos: StrollPhotoMarkerInput[], radiusM = defaultPhotoMergeRadiusM): StrollMapAnnotation[] {
  const clusters: PhotoCluster[] = []

  for (const photo of photos) {
    const cluster = clusters.find((candidate) => distanceMeters(candidate, photo) <= radiusM)
    if (!cluster) {
      clusters.push({ lng: photo.lng, lat: photo.lat, photos: [photo] })
      continue
    }

    cluster.photos.push(photo)
    cluster.lng = cluster.photos.reduce((sum, item) => sum + item.lng, 0) / cluster.photos.length
    cluster.lat = cluster.photos.reduce((sum, item) => sum + item.lat, 0) / cluster.photos.length
  }

  return clusters.map((cluster) => {
    const refs: StrollPhotoRef[] = cluster.photos.map(({ id, url, thumbUrl, takenAt, source }) => ({ id, url, thumbUrl, takenAt, source }))
    const firstPhoto = refs[0]
    return {
      id: `photo-${firstPhoto?.id ?? cluster.lng.toFixed(5)}`,
      kind: "photo",
      lng: cluster.lng,
      lat: cluster.lat,
      photos: refs,
      count: refs.length,
      mergedRadiusM: radiusM,
      source: "user",
    }
  })
}

export function createAiEmojiAnnotation(input: Omit<StrollMapAnnotation, "kind" | "source">): StrollMapAnnotation {
  return {
    ...input,
    kind: "ai",
    source: "ai",
  }
}