import type { StrollTrackPoint } from "./types"

const startTime = Date.now() - 15 * 60 * 1000

const rawPoints: Array<[number, number, number]> = [
  [121.47086, 31.22432, 1.2],
  [121.47122, 31.22472, 1.5],
  [121.47178, 31.22504, 1.9],
  [121.47231, 31.22547, 2.4],
  [121.47288, 31.22577, 2.8],
  [121.47352, 31.22586, 3.1],
  [121.47418, 31.22604, 2.5],
  [121.47475, 31.22644, 1.8],
  [121.47524, 31.22684, 1.4],
  [121.47576, 31.2272, 2.2],
  [121.47642, 31.22744, 3.3],
  [121.47704, 31.22762, 3.8],
  [121.47758, 31.22794, 2.6],
  [121.47806, 31.22838, 1.7],
  [121.47852, 31.22882, 1.3],
  [121.4791, 31.22914, 2.1],
]

export const sampleTrack: StrollTrackPoint[] = rawPoints.map(([lng, lat, speed], index) => ({
  id: `sample-${index}`,
  lng,
  lat,
  speed,
  t: startTime + index * 58_000,
  accuracy: 8 + (index % 3) * 2,
  source: "replay",
}))