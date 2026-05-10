import { distanceMeters } from "./route"
import type { StrollMapAnnotation, StrollPhotoMarkerInput, StrollPhotoRef, StrollSpatialRecord } from "./types"

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
      createdAt: Math.max(...refs.map((ref) => ref.takenAt || 0).filter(Boolean), 0) || undefined,
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

export function createSpatialRecordAnnotation(record: StrollSpatialRecord): StrollMapAnnotation {
  if (record.kind === "photo") {
    const attachmentSource = record.attachment?.source
    const source = attachmentSource === "phone" || attachmentSource === "limen" || attachmentSource === "replay" ? attachmentSource : undefined
    const photo = record.attachment ? { ...record.attachment, takenAt: record.attachment.takenAt || record.createdAt, source } : undefined
    return {
      id: record.id,
      kind: "photo",
      lng: record.lng,
      lat: record.lat,
      text: record.text,
      emoji: record.emoji,
      photos: photo ? [photo] : [],
      count: photo ? 1 : undefined,
      source: record.origin,
      recordKind: record.kind,
      placeKind: record.placeKind,
      origin: record.origin,
      distanceM: record.distanceM,
      radiusM: record.radiusM,
      createdAt: record.createdAt,
      updatedAt: record.updatedAt,
      attachment: record.attachment,
    }
  }
  return {
    id: record.id,
    kind: "ai",
    lng: record.lng,
    lat: record.lat,
    text: record.text,
    emoji: record.emoji || "✦",
    source: record.origin,
    recordKind: record.kind,
    placeKind: record.placeKind,
    origin: record.origin,
    distanceM: record.distanceM,
    radiusM: record.radiusM,
    createdAt: record.createdAt,
    updatedAt: record.updatedAt,
    attachment: record.attachment,
  }
}