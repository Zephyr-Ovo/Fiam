import { useEffect, useMemo, useRef, type MutableRefObject } from "react"
import mapboxgl, {
  type ExpressionSpecification,
  type GeoJSONSource,
  type LineLayerSpecification,
  type Map as MapboxMap,
  type SymbolLayerSpecification,
} from "mapbox-gl"
import type { FeatureCollection, Point } from "geojson"
import {
  buildRouteFeature,
  buildSpeedGradient,
  normalizeTrack,
} from "./route"
import { toRenderCoordinate } from "./coordinates"
import { applyStandardConfig, displayLightPreset, quietDefaultMapLayers, removeDefaultLabelLayers, standardStyleUrl } from "./mapboxStyle"
import type { CoordinateCorrection, StrollMapAnnotation, StrollMapLabel, StrollTrackPoint, WeatherSnapshot } from "./types"

const routeSourceId = "stroll-route"
const labelSourceId = "stroll-custom-labels"
const routeGlowLayerId = "stroll-route-glow"
const routeCasingLayerId = "stroll-route-casing"
const routeGradientLayerId = "stroll-route-gradient"
const labelLayerId = "stroll-custom-labels-symbol"

type Props = {
  token: string
  track: StrollTrackPoint[]
  labels?: StrollMapLabel[]
  annotations?: StrollMapAnnotation[]
  weather: WeatherSnapshot
  coordinateCorrection: CoordinateCorrection
  onAnnotationClick?: (annotation: StrollMapAnnotation) => void
}

export function StrollMapView({ token, track, labels = [], annotations = [], weather, coordinateCorrection, onAnnotationClick }: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const mapRef = useRef<MapboxMap | null>(null)
  const markerRef = useRef<mapboxgl.Marker | null>(null)
  const annotationMarkersRef = useRef<mapboxgl.Marker[]>([])
  const fittedRef = useRef(false)
  const initialWeatherRef = useRef(weather)
  const latestWeatherRef = useRef(weather)
  const renderPoints = useMemo(() => normalizeTrack(track, coordinateCorrection), [track, coordinateCorrection])
  const latestMapStateRef = useRef({ renderPoints, labels, annotations, coordinateCorrection, onAnnotationClick })

  useEffect(() => {
    latestMapStateRef.current = { renderPoints, labels, annotations, coordinateCorrection, onAnnotationClick }
  }, [renderPoints, labels, annotations, coordinateCorrection, onAnnotationClick])

  useEffect(() => {
    latestWeatherRef.current = weather
  }, [weather])

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return
    mapboxgl.accessToken = token

    const firstPoint = latestMapStateRef.current.renderPoints[0]
    const map = new mapboxgl.Map({
      container: containerRef.current,
      style: standardStyleUrl,
      center: firstPoint?.coordinate ?? [121.4737, 31.2262],
      zoom: 15.2,
      pitch: 0,
      bearing: 0,
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
      applyStandardConfig(map, initialWeatherRef.current)
      updateMapData(map, latest.renderPoints, latest.labels, latest.coordinateCorrection)
      renderAnnotationMarkers(map, annotationMarkersRef, latest.annotations, latest.coordinateCorrection, latest.onAnnotationClick)
      fitRouteOnce(map, latest.renderPoints, fittedRef)
    })

    return () => {
      clearAnnotationMarkers(annotationMarkersRef)
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
    const timer = window.setInterval(() => {
      const map = mapRef.current
      if (!map || !map.isStyleLoaded()) return
      applyStandardConfig(map, latestWeatherRef.current)
    }, 60_000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !map.isStyleLoaded()) return
    updateMapData(map, renderPoints, labels, coordinateCorrection)
    fitRouteOnce(map, renderPoints, fittedRef)
  }, [renderPoints, labels, coordinateCorrection])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !map.isStyleLoaded()) return
    applyStandardConfig(map, weather)
  }, [weather])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    renderAnnotationMarkers(map, annotationMarkersRef, annotations, coordinateCorrection, onAnnotationClick)
  }, [annotations, coordinateCorrection, onAnnotationClick])

  useEffect(() => {
    const map = mapRef.current
    const currentPoint = renderPoints[renderPoints.length - 1]
    if (!map || !currentPoint) return

    if (!markerRef.current) {
      const markerElement = createMarkerElement()
      markerElement.addEventListener("click", (event) => {
        event.stopPropagation()
        const latestPoint = latestMapStateRef.current.renderPoints.at(-1)
        if (latestPoint) focusCurrentLocation(mapRef.current, latestPoint.coordinate)
      })
      markerRef.current = new mapboxgl.Marker({ element: markerElement, anchor: "bottom" })
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
  layer: (LineLayerSpecification | SymbolLayerSpecification) & { slot?: string },
) {
  if (map.getLayer(layer.id)) return
  map.addLayer(layer)
}

function updateMapData(
  map: MapboxMap,
  points: ReturnType<typeof normalizeTrack>,
  labels: StrollMapLabel[],
  correction: CoordinateCorrection,
) {
  getGeoJsonSource(map, routeSourceId)?.setData(buildRouteFeature(points))
  getGeoJsonSource(map, labelSourceId)?.setData(buildLabelFeatures(labels, correction))
  if (map.getLayer(routeGradientLayerId)) {
    map.setPaintProperty(routeGradientLayerId, "line-gradient", buildSpeedGradient(points) as ExpressionSpecification)
  }
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

function focusCurrentLocation(map: MapboxMap | null, coordinate: [number, number]) {
  if (!map) return
  map.easeTo({
    center: coordinate,
    zoom: Math.max(map.getZoom(), 16.35),
    pitch: 0,
    bearing: 0,
    duration: 620,
    essential: true,
  })
}

function renderAnnotationMarkers(
  map: MapboxMap,
  markersRef: MutableRefObject<mapboxgl.Marker[]>,
  annotations: StrollMapAnnotation[],
  correction: CoordinateCorrection,
  onAnnotationClick?: (annotation: StrollMapAnnotation) => void,
) {
  clearAnnotationMarkers(markersRef)
  markersRef.current = annotations.map((annotation) => {
    const markerElement = createAnnotationMarkerElement(annotation)
    markerElement.addEventListener("click", (event) => {
      event.stopPropagation()
      onAnnotationClick?.(annotation)
    })
    return new mapboxgl.Marker({ element: markerElement, anchor: annotation.kind === "ai" ? "bottom" : "center" })
      .setLngLat(toRenderCoordinate({ lng: annotation.lng, lat: annotation.lat, t: 0 }, correction))
      .addTo(map)
  })
}

function clearAnnotationMarkers(markersRef: MutableRefObject<mapboxgl.Marker[]>) {
  markersRef.current.forEach((marker) => marker.remove())
  markersRef.current = []
}

function createMarkerElement() {
  const marker = document.createElement("div")
  marker.className = "stroll-marker"
  marker.setAttribute("aria-label", "Current Stroll position")
  return marker
}

function createAnnotationMarkerElement(annotation: StrollMapAnnotation) {
  const marker = document.createElement("button")
  marker.type = "button"
  marker.className = `stroll-map-annotation stroll-map-annotation--${annotation.kind}`
  marker.setAttribute("aria-label", annotation.text ?? `${annotation.kind} marker`)

  if (annotation.kind === "photo") {
    marker.dataset.count = annotation.count && annotation.count > 1 ? String(annotation.count) : ""
  } else {
    marker.textContent = annotation.emoji ?? "•"
  }

  return marker
}
