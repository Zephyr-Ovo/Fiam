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
