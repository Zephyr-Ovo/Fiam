# @fiam/stroll-map

Shared Stroll map module for Favilla and future Fiam surfaces.

## Contents

- `StrollMapView`: Mapbox Standard renderer for route, tail, footsteps, current marker, and custom labels.
- `sampleTrack`: replay data used while real location is not connected.
- `weather`: Open-Meteo/default endpoint weather adapter.
- `mapboxStyle`: Mapbox Standard light/weather configuration.

## Runtime Inputs

- `VITE_MAPBOX_TOKEN`: required by the consuming Vite app to show the live Mapbox basemap.
- `VITE_STROLL_WEATHER_ENDPOINT`: optional. If omitted, `fetchWeatherSnapshot` uses Open-Meteo directly and needs no token.

Mapbox client tokens are public frontend tokens. Weather provider secrets should stay behind a backend endpoint.

## AI Spatial Context

AI should consume structured map data, not screenshots, for spatial awareness. The shared contract exposes:

- time-indexed route points with `lng`, `lat`, `t`, `speed`, `heading`, `accuracy`, and source;
- photo annotations merged within 20m by `buildPhotoAnnotations`;
- AI emoji annotations rendered as white droplet pins;
- nearby memory/place records through `StrollSpatialContext`.

The intended AI tools are shaped around queries such as current location context, route segment context, nearby memories, and add/update map annotation. Screenshots remain only a visual fallback for UI debugging.
