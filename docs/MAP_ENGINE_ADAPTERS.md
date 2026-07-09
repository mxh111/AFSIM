# Map Engine Adapters

## Current Status

The frontend now routes map rendering through adapter modules under:

```text
app/static/js/map_adapters/
```

Available adapters:

- `canvas_adapter.js`: active default. It delegates to the existing `map_renderer.js`.
- `maplibre_adapter.js`: optional placeholder. It reports `unavailable` if `window.maplibregl` is not loaded.
- `cesium_adapter.js`: optional placeholder. It reports `unavailable` if `window.Cesium` is not loaded.

The default engine remains **Canvas Tactical**. MapLibre and Cesium do not emit authoritative data and do not replace the existing Canvas/Three.js tactical renderer yet.

## Unified Map Input

Adapters receive one normalized input object:

```js
{
  schema_version: "afsim-map-frame.v1",
  platforms: [],
  entities: [],
  tracks: [],
  detections: [],
  communications: [],
  events: [],
  frame: null
}
```

This keeps future MapLibre and Cesium work focused on rendering, not on reinterpreting workbench/replay/realtime payloads.

## Local Static Resources

Do not add CDN dependencies to `index.html`. Place optional engine assets under the existing local vendor tree:

```text
app/static/vendor/maplibre/
  maplibre-gl.js
  maplibre-gl.css

app/static/vendor/cesium/
  Cesium.js
  Widgets/widgets.css
  Assets/
  Workers/
  ThirdParty/
```

After assets are present, load them before `app/static/js/app.js` in `index.html`, or add a local loader that checks for these files and inserts script/link tags. The adapters intentionally use global probes (`window.maplibregl`, `window.Cesium`) so the application still boots when the optional libraries are absent.

## Follow-Up Work

MapLibre 2D:

- Create a local style using `/api/afsim/maps/{map_id}/{z}/{x}/{y}.png`.
- Add sources/layers for platforms, tracks, detections, communications, and events.
- Preserve current selection and timeline behavior.

Cesium 3D:

- Configure `CESIUM_BASE_URL` for the local vendor directory.
- Use AFSIM raster textures and optional terrain only from local assets.
- Convert normalized entities/tracks into Cesium primitives/entities.

Guardrails:

- Keep Canvas Tactical as the fallback.
- Never label placeholder engines as authoritative.
- Keep AFSIM installation directories read-only.
