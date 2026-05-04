import { useEffect, useMemo, useRef } from "react"
import mapboxgl, {
  type CircleLayerSpecification,
  type ExpressionSpecification,
  type GeoJSONSource,
  type LineLayerSpecification,
  type Map as MapboxMap,
  type SymbolLayerSpecification,
} from "mapbox-gl"
import type { FeatureCollection, Point } from "geojson"
import {
  buildFootstepFeatures,
  buildRouteFeature,
  buildSpeedGradient,
  buildTailFeature,
  normalizeTrack,
} from "./route"
import { toRenderCoordinate } from "./coordinates"
import { applyStandardConfig, displayLightPreset, quietDefaultMapLayers, removeDefaultLabelLayers, standardStyleUrl } from "./mapboxStyle"
import type { CoordinateCorrection, StrollMapLabel, StrollMapMode, StrollTrackPoint, WeatherSnapshot } from "./types"

const routeSourceId = "stroll-route"
const tailSourceId = "stroll-tail"
const footstepSourceId = "stroll-footsteps"
const labelSourceId = "stroll-custom-labels"
const routeGlowLayerId = "stroll-route-glow"
const routeCasingLayerId = "stroll-route-casing"
const routeGradientLayerId = "stroll-route-gradient"
const tailLayerId = "stroll-tail-line"
const footstepLayerId = "stroll-footsteps-circle"
const labelLayerId = "stroll-custom-labels-symbol"

type Props = {
  token: string
  track: StrollTrackPoint[]
  labels?: StrollMapLabel[]
  mode: StrollMapMode
  weather: WeatherSnapshot
  coordinateCorrection: CoordinateCorrection
}

export function StrollMapView({ token, track, labels = [], mode, weather, coordinateCorrection }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<MapboxMap | null>(null)
  const markerRef = useRef<mapboxgl.Marker | null>(null)
  const fittedRef = useRef(false)
  const initialModeRef = useRef(mode)
  const initialWeatherRef = useRef(weather)
  const renderPoints = useMemo(() => normalizeTrack(track, coordinateCorrection), [track, coordinateCorrection])
  const latestMapStateRef = useRef({ renderPoints, labels, coordinateCorrection, mode })

  useEffect(() => {
    latestMapStateRef.current = { renderPoints, labels, coordinateCorrection, mode }
  }, [renderPoints, labels, coordinateCorrection, mode])

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return
    mapboxgl.accessToken = token

    const firstPoint = latestMapStateRef.current.renderPoints[0]
    const initialMode = initialModeRef.current
    const map = new mapboxgl.Map({
      container: containerRef.current,
      style: standardStyleUrl,
      center: firstPoint?.coordinate ?? [121.4737, 31.2262],
      zoom: 15.2,
      pitch: initialMode === "3d" ? 55 : 0,
      bearing: initialMode === "3d" ? -18 : 0,
      dragPan: true,
      dragRotate: false,
      scrollZoom: true,
      touchZoomRotate: true,
      pitchWithRotate: false,
      attributionControl: false,
      antialias: true,
      config: {
        basemap: {
          lightPreset: displayLightPreset(),
          showPointOfInterestLabels: false,
          showRoadLabels: false,
          showTransitLabels: false,
          showPlaceLabels: false,
        },
      },
    })

    map.dragPan.enable()
    map.touchZoomRotate.enable()
    map.touchZoomRotate.disableRotation()
    mapRef.current = map

    map.on("style.load", () => {
      const latest = latestMapStateRef.current
      removeDefaultLabelLayers(map)
      quietDefaultMapLayers(map)
      addStrollSourcesAndLayers(map)
      applyStandardConfig(map, initialModeRef.current, initialWeatherRef.current)
      updateMapData(map, latest.renderPoints, latest.labels, latest.coordinateCorrection, latest.mode)
      fitRouteOnce(map, latest.renderPoints, fittedRef)
    })

    return () => {
      markerRef.current?.remove()
      markerRef.current = null
      mapRef.current?.remove()
      mapRef.current = null
      fittedRef.current = false
    }
  }, [token])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const observer = new ResizeObserver(() => mapRef.current?.resize())
    observer.observe(container)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !map.isStyleLoaded()) return
    updateMapData(map, renderPoints, labels, coordinateCorrection, mode)
    fitRouteOnce(map, renderPoints, fittedRef)
  }, [renderPoints, labels, coordinateCorrection, mode])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !map.isStyleLoaded()) return
    applyStandardConfig(map, mode, weather)
    setModeVisibility(map, mode)
    const currentPoint = renderPoints[renderPoints.length - 1]
    if (mode === "2d") {
      fitRoute(map, renderPoints, 760)
    }
    map.easeTo({
      center: mode === "3d" && currentPoint ? currentPoint.coordinate : undefined,
      pitch: mode === "3d" ? 55 : 0,
      bearing: mode === "3d" ? -18 : 0,
      duration: 850,
      essential: true,
    })
  }, [mode, renderPoints, weather])

  useEffect(() => {
    const map = mapRef.current
    const currentPoint = renderPoints[renderPoints.length - 1]
    if (!map || !currentPoint) return

    if (!markerRef.current) {
      markerRef.current = new mapboxgl.Marker({ element: createMarkerElement(), anchor: "center" })
    }
    markerRef.current.setLngLat(currentPoint.coordinate).addTo(map)
  }, [renderPoints])

  return (
    <div className="stroll-map">
      <div ref={containerRef} className="stroll-map__canvas" />
      <div className="stroll-map__tint" />
    </div>
  )
}

function addStrollSourcesAndLayers(map: MapboxMap) {
  if (!map.getSource(routeSourceId)) {
    map.addSource(routeSourceId, {
      type: "geojson",
      lineMetrics: true,
      data: buildRouteFeature([]),
    })
  }
  if (!map.getSource(tailSourceId)) {
    map.addSource(tailSourceId, {
      type: "geojson",
      lineMetrics: true,
      data: buildTailFeature([]),
    })
  }
  if (!map.getSource(footstepSourceId)) {
    map.addSource(footstepSourceId, {
      type: "geojson",
      data: buildFootstepFeatures([]),
    })
  }
  if (!map.getSource(labelSourceId)) {
    map.addSource(labelSourceId, {
      type: "geojson",
      data: buildLabelFeatures([], "none"),
    })
  }

  addLayer(map, {
    id: routeGlowLayerId,
    type: "line",
    source: routeSourceId,
    slot: "top",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": "#FAF4E5",
      "line-width": 24,
      "line-opacity": 0.92,
      "line-blur": 2,
    },
  })

  addLayer(map, {
    id: routeCasingLayerId,
    type: "line",
    source: routeSourceId,
    slot: "top",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": "#1E2843",
      "line-width": 13,
      "line-opacity": 0.82,
    },
  })

  addLayer(map, {
    id: routeGradientLayerId,
    type: "line",
    source: routeSourceId,
    slot: "top",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-gradient": buildSpeedGradient([]) as ExpressionSpecification,
      "line-width": 8.5,
      "line-opacity": 0.98,
    },
  })

  addLayer(map, {
    id: tailLayerId,
    type: "line",
    source: tailSourceId,
    slot: "top",
    layout: { "line-cap": "round", "line-join": "round", visibility: "none" },
    paint: {
      "line-color": "#EDAB98",
      "line-width": 9,
      "line-opacity": 0.96,
      "line-blur": 0.4,
    },
  })

  addLayer(map, {
    id: footstepLayerId,
    type: "circle",
    source: footstepSourceId,
    slot: "top",
    layout: { visibility: "none" },
    paint: {
      "circle-color": "#1E2843",
      "circle-opacity": ["interpolate", ["linear"], ["get", "age"], 0, 0.28, 1, 0.74],
      "circle-radius": ["interpolate", ["linear"], ["get", "age"], 0, 3, 1, 5.4],
      "circle-stroke-color": "#FAF4E5",
      "circle-stroke-width": 1.8,
    },
  })

  addLayer(map, {
    id: labelLayerId,
    type: "symbol",
    source: labelSourceId,
    slot: "top",
    layout: {
      "text-field": ["get", "text"],
      "text-size": 12,
      "text-font": ["DIN Pro Medium", "Arial Unicode MS Regular"],
      "text-anchor": "top",
      "text-offset": [0, 1.1],
      "text-allow-overlap": true,
      "text-ignore-placement": true,
    },
    paint: {
      "text-color": "#1E2843",
      "text-halo-color": "#FAF4E5",
      "text-halo-width": 1.8,
      "text-opacity": 0.96,
    },
  })
}

function addLayer(
  map: MapboxMap,
  layer: (LineLayerSpecification | CircleLayerSpecification | SymbolLayerSpecification) & { slot?: string },
) {
  if (map.getLayer(layer.id)) return
  map.addLayer(layer)
}

function updateMapData(
  map: MapboxMap,
  points: ReturnType<typeof normalizeTrack>,
  labels: StrollMapLabel[],
  correction: CoordinateCorrection,
  mode: StrollMapMode,
) {
  getGeoJsonSource(map, routeSourceId)?.setData(buildRouteFeature(points))
  getGeoJsonSource(map, tailSourceId)?.setData(buildTailFeature(points))
  getGeoJsonSource(map, footstepSourceId)?.setData(buildFootstepFeatures(points))
  getGeoJsonSource(map, labelSourceId)?.setData(buildLabelFeatures(labels, correction))
  if (map.getLayer(routeGradientLayerId)) {
    map.setPaintProperty(routeGradientLayerId, "line-gradient", buildSpeedGradient(points) as ExpressionSpecification)
  }
  setModeVisibility(map, mode)
}

function setModeVisibility(map: MapboxMap, mode: StrollMapMode) {
  const routeVisibility = mode === "2d" ? "visible" : "none"
  const liveVisibility = mode === "3d" ? "visible" : "none"
  setLayerVisibility(map, routeGlowLayerId, routeVisibility)
  setLayerVisibility(map, routeCasingLayerId, routeVisibility)
  setLayerVisibility(map, routeGradientLayerId, routeVisibility)
  setLayerVisibility(map, tailLayerId, liveVisibility)
  setLayerVisibility(map, footstepLayerId, liveVisibility)
  setLayerVisibility(map, labelLayerId, "visible")
}

function setLayerVisibility(map: MapboxMap, layerId: string, visibility: "visible" | "none") {
  if (map.getLayer(layerId)) map.setLayoutProperty(layerId, "visibility", visibility)
}

function getGeoJsonSource(map: MapboxMap, sourceId: string) {
  return map.getSource(sourceId) as GeoJSONSource | undefined
}

function buildLabelFeatures(labels: StrollMapLabel[], correction: CoordinateCorrection): FeatureCollection<Point> {
  return {
    type: "FeatureCollection",
    features: labels.map((label) => ({
      type: "Feature",
      properties: { text: label.text, tone: label.tone ?? "note" },
      geometry: {
        type: "Point",
        coordinates: toRenderCoordinate({ ...label, t: 0 }, correction),
      },
    })),
  }
}

function fitRouteOnce(
  map: MapboxMap,
  points: ReturnType<typeof normalizeTrack>,
  fittedRef: React.MutableRefObject<boolean>,
) {
  if (fittedRef.current || points.length < 2) return
  fitRoute(map, points, 650)
  fittedRef.current = true
}

function fitRoute(map: MapboxMap, points: ReturnType<typeof normalizeTrack>, duration: number) {
  if (points.length < 2) return
  const firstPoint = points[0]
  const bounds = new mapboxgl.LngLatBounds(firstPoint.coordinate, firstPoint.coordinate)
  points.forEach((point) => bounds.extend(point.coordinate))
  map.fitBounds(bounds, { padding: 118, duration, maxZoom: 16.15 })
}

function createMarkerElement() {
  const marker = document.createElement("div")
  marker.className = "stroll-marker"
  marker.setAttribute("aria-label", "Current Stroll position")
  return marker
}
