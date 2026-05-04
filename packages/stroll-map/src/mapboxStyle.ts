import type { Map as MapboxMap } from "mapbox-gl"
import type { StrollMapMode, WeatherSnapshot } from "./types"

export const standardStyleUrl = "mapbox://styles/mapbox/standard"

type WeatherCapableMap = MapboxMap & {
  setRain?: (rain: Record<string, unknown> | null) => void
  setSnow?: (snow: Record<string, unknown> | null) => void
}

export function currentLightPreset(date = new Date()) {
  const hour = date.getHours()
  if (hour >= 5 && hour < 8) return "dawn"
  if (hour >= 8 && hour < 17) return "day"
  if (hour >= 17 && hour < 20) return "dusk"
  return "night"
}

export function displayLightPreset(date = new Date()) {
  const preset = currentLightPreset(date)
  return preset
}

export function applyStandardConfig(map: MapboxMap, mode: StrollMapMode, weather: WeatherSnapshot) {
  setBasemapConfig(map, "lightPreset", displayLightPreset())
  setBasemapConfig(map, "showPointOfInterestLabels", false)
  setBasemapConfig(map, "showRoadLabels", false)
  setBasemapConfig(map, "showTransitLabels", false)
  setBasemapConfig(map, "showPlaceLabels", false)
  setBasemapConfig(map, "show3dObjects", mode === "3d")
  setBasemapConfig(map, "showRain", weather.kind === "rain")
  setBasemapConfig(map, "showSnow", weather.kind === "snow")
  applyWeather(map, weather)
}

function setBasemapConfig(map: MapboxMap, key: string, value: string | boolean) {
  try {
    map.setConfigProperty("basemap", key, value)
  } catch {
    return
  }
}

export function removeDefaultLabelLayers(map: MapboxMap) {
  const layers = map.getStyle().layers ?? []
  for (const layer of layers) {
    if (layer.id.startsWith("stroll-") || layer.type !== "symbol") continue
    try {
      map.removeLayer(layer.id)
    } catch {
      continue
    }
  }
}

export function quietDefaultMapLayers(map: MapboxMap) {
  const layers = map.getStyle().layers ?? []
  for (const layer of layers) {
    if (layer.id.startsWith("stroll-")) continue

    if (layer.type === "background") {
      setPaint(map, layer.id, "background-color", "#8FA7B2")
      setPaint(map, layer.id, "background-opacity", 0.88)
      continue
    }

    if (layer.type === "fill") {
      setPaint(map, layer.id, "fill-color", "#8FA7B2")
      setPaint(map, layer.id, "fill-opacity", layer.id.includes("water") ? 0.16 : 0.06)
      continue
    }

    if (layer.type === "line") {
      const isBoundary = layer.id.includes("admin") || layer.id.includes("boundary") || layer.id.includes("road")
      setPaint(map, layer.id, "line-color", isBoundary ? "#F6E8DD" : "#D8E4E6")
      setPaint(map, layer.id, "line-opacity", isBoundary ? 0.32 : 0.16)
      continue
    }

    if (layer.type === "circle") {
      setPaint(map, layer.id, "circle-opacity", 0)
      continue
    }

    if (layer.type === "fill-extrusion") {
      setPaint(map, layer.id, "fill-extrusion-opacity", 0)
      continue
    }

    if (layer.type === "raster") {
      setPaint(map, layer.id, "raster-opacity", 0.08)
      continue
    }

    if (layer.type === "model") {
      setVisibility(map, layer.id, "none")
    }
  }
}

function setPaint(map: MapboxMap, layerId: string, property: string, value: string | number) {
  try {
    const paintMap = map as MapboxMap & { setPaintProperty: (layerId: string, property: string, value: string | number) => void }
    paintMap.setPaintProperty(layerId, property, value)
  } catch {
    return
  }
}

function setVisibility(map: MapboxMap, layerId: string, visibility: "visible" | "none") {
  try {
    map.setLayoutProperty(layerId, "visibility", visibility)
  } catch {
    return
  }
}

function applyWeather(map: MapboxMap, weather: WeatherSnapshot) {
  const weatherMap = map as WeatherCapableMap
  const intensity = weather.intensity ?? 0.32

  if (weatherMap.setRain) {
    weatherMap.setRain(weather.kind === "rain" ? { density: intensity, intensity, color: "#A3B9C9" } : null)
  }

  if (weatherMap.setSnow) {
    weatherMap.setSnow(weather.kind === "snow" ? { density: intensity * 0.55, intensity } : null)
  }
}