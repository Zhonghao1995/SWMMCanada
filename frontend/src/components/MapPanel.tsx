import { useEffect, useRef, useState } from 'react'
import Map, {
  Layer,
  NavigationControl,
  Popup,
  ScaleControl,
  Source,
  type MapLayerMouseEvent,
  type MapRef,
} from 'react-map-gl/maplibre'
import type { FilterSpecification, LngLatBoundsLike, StyleSpecification } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Feature, FeatureCollection } from 'geojson'
import { useStore } from '../store'

// Free CARTO Positron raster basemap — no API token.
const MAP_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    carto: {
      type: 'raster',
      tiles: [
        'https://a.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png',
        'https://b.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png',
        'https://c.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png',
      ],
      tileSize: 256,
      attribution: '© OpenStreetMap contributors © CARTO',
    },
  },
  layers: [{ id: 'carto-base', type: 'raster', source: 'carto' }],
}

const EMPTY: FeatureCollection = { type: 'FeatureCollection', features: [] }
const kindIs = (k: string): FilterSpecification => ['==', ['get', 'kind'], k] as FilterSpecification
const vis = (on: boolean) => ({ visibility: (on ? 'visible' : 'none') as 'visible' | 'none' })

// Click-to-inspect (ADR 0019): the popup reads only what the preview GeoJSON already
// carries. Rows are [label, property key, unit]; missing keys are skipped.
const INSPECT_LAYERS = ['m-outfall', 'm-junction', 'm-conduit', 'm-sub-fill']
const POPUP_ROWS: Record<string, [string, string, string?][]> = {
  subcatchment: [
    ['Area', 'area_ha', 'ha'],
    ['Impervious', 'pct_imperv', '%'],
    ['CN', 'cn'],
    ['Slope', 'pct_slope', '%'],
    ['Width', 'width_m', 'm'],
    ['Outlet node', 'outlet_node'],
  ],
  conduit: [
    ['Diameter', 'diameter_m', 'm'],
    ['Length', 'length_m', 'm'],
    ['Roughness n', 'roughness_n'],
    ['From node', 'from_node'],
    ['To node', 'to_node'],
    ['System', 'system'],
  ],
  junction: [
    ['Invert', 'invert_m', 'm'],
    ['Max depth', 'max_depth_m', 'm'],
    ['System', 'system'],
  ],
  outfall: [
    ['Invert', 'invert_m', 'm'],
    ['Type', 'outfall_type'],
    ['System', 'system'],
  ],
}

interface Picked {
  lng: number
  lat: number
  props: Record<string, unknown>
}

function bboxOf(fc: FeatureCollection): LngLatBoundsLike | null {
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity, found = false
  const walk = (c: unknown): void => {
    const arr = c as number[]
    if (typeof arr[0] === 'number') {
      const x = arr[0], y = arr[1]
      if (x < minx) minx = x
      if (y < miny) miny = y
      if (x > maxx) maxx = x
      if (y > maxy) maxy = y
      found = true
    } else {
      ;(c as unknown[]).forEach(walk)
    }
  }
  for (const f of fc.features) {
    if (f.geometry && 'coordinates' in f.geometry) walk((f.geometry as { coordinates: unknown }).coordinates)
  }
  return found ? [[minx, miny], [maxx, maxy]] : null
}

export default function MapPanel() {
  const mapRef = useRef<MapRef>(null)
  const drawing = useStore((s) => s.drawing)
  const draft = useStore((s) => s.draft)
  const aoi = useStore((s) => s.aoi)
  const addVertex = useStore((s) => s.addVertex)
  const finishDraw = useStore((s) => s.finishDraw)
  const preview = useStore((s) => s.preview)
  const layers = useStore((s) => s.layers)
  const [picked, setPicked] = useState<Picked | null>(null)
  const [hovering, setHovering] = useState(false)

  // Fit the map to the model when a preview loads; a new build voids the old popup.
  useEffect(() => {
    setPicked(null)
    if (preview && mapRef.current) {
      const b = bboxOf(preview)
      if (b) mapRef.current.fitBounds(b, { padding: 50, duration: 800 })
    }
  }, [preview])

  // Fit the map to an uploaded boundary as soon as the backend has parsed it.
  useEffect(() => {
    if (aoi?.source === 'upload' && aoi.bbox && mapRef.current) {
      const [minx, miny, maxx, maxy] = aoi.bbox
      mapRef.current.fitBounds([[minx, miny], [maxx, maxy]], { padding: 60, duration: 800 })
    }
  }, [aoi])

  const aoiFeature: Feature | null =
    aoi?.source === 'draw' ? aoi.polygon : aoi?.source === 'upload' && aoi.boundary ? aoi.boundary : null
  const aoiFc: FeatureCollection = aoiFeature
    ? { type: 'FeatureCollection', features: [aoiFeature] }
    : EMPTY

  const draftFc: FeatureCollection = draft.length
    ? {
        type: 'FeatureCollection',
        features: [
          { type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: draft } },
          ...draft.map((c) => ({
            type: 'Feature' as const, properties: {},
            geometry: { type: 'Point' as const, coordinates: c },
          })),
        ],
      }
    : EMPTY

  const model: FeatureCollection = preview ?? EMPTY

  return (
    <Map
      ref={mapRef}
      initialViewState={{ longitude: -123.363, latitude: 48.424, zoom: 14 }}
      mapStyle={MAP_STYLE}
      style={{ width: '100%', height: '100%' }}
      cursor={drawing ? 'crosshair' : hovering ? 'pointer' : ''}
      interactiveLayerIds={drawing ? [] : INSPECT_LAYERS}
      onClick={(e: MapLayerMouseEvent) => {
        if (drawing) {
          addVertex(e.lngLat.lng, e.lngLat.lat)
          return
        }
        // Topmost feature wins (outfall > junction > conduit > subcatchment).
        const f = e.features?.[0]
        if (f?.properties) setPicked({ lng: e.lngLat.lng, lat: e.lngLat.lat, props: f.properties })
        else setPicked(null)
      }}
      onMouseEnter={() => setHovering(true)}
      onMouseLeave={() => setHovering(false)}
      onDblClick={(e: MapLayerMouseEvent) => {
        if (drawing) {
          e.preventDefault()
          finishDraw()
        }
      }}
    >
      <NavigationControl position="top-right" showCompass={false} />
      <ScaleControl position="bottom-right" />

      {/* Generated model: subcatchments / conduits / junctions / outfall */}
      <Source id="model" type="geojson" data={model}>
        <Layer id="m-sub-fill" type="fill" filter={kindIs('subcatchment')} layout={vis(layers.subcatchments)}
          paint={{ 'fill-color': '#22c55e', 'fill-opacity': 0.18 }} />
        <Layer id="m-sub-line" type="line" filter={kindIs('subcatchment')} layout={vis(layers.subcatchments)}
          paint={{ 'line-color': '#16a34a', 'line-width': 0.6, 'line-opacity': 0.55 }} />
        <Layer id="m-conduit" type="line" filter={kindIs('conduit')}
          layout={{ visibility: layers.conduits ? 'visible' : 'none', 'line-cap': 'round' }}
          paint={{ 'line-color': '#2563eb', 'line-width': 1.6 }} />
        <Layer id="m-junction" type="circle" filter={kindIs('junction')} layout={vis(layers.junctions)}
          paint={{ 'circle-radius': 2.6, 'circle-color': '#1d4ed8' }} />
        <Layer id="m-outfall" type="circle" filter={kindIs('outfall')}
          paint={{ 'circle-radius': 7, 'circle-color': '#ef4444', 'circle-stroke-width': 2, 'circle-stroke-color': '#ffffff' }} />
      </Source>

      {/* AOI (committed) */}
      <Source id="aoi" type="geojson" data={aoiFc}>
        <Layer id="aoi-fill" type="fill" paint={{ 'fill-color': '#2563eb', 'fill-opacity': 0.10 }} />
        <Layer id="aoi-line" type="line" paint={{ 'line-color': '#2563eb', 'line-width': 2 }} />
      </Source>

      {/* In-progress draft */}
      <Source id="draft" type="geojson" data={draftFc}>
        <Layer id="draft-line" type="line" paint={{ 'line-color': '#f59e0b', 'line-width': 2 }} />
        <Layer id="draft-pts" type="circle"
          paint={{ 'circle-radius': 4, 'circle-color': '#f59e0b', 'circle-stroke-width': 1.5, 'circle-stroke-color': '#ffffff' }} />
      </Source>

      {/* Click-to-inspect popup (ADR 0019): first-pass QC, read-only */}
      {picked && (
        <Popup
          longitude={picked.lng}
          latitude={picked.lat}
          anchor="bottom"
          maxWidth="240px"
          closeButton
          closeOnClick={false}
          onClose={() => setPicked(null)}
        >
          <div className="text-xs text-slate-700">
            <div className="mb-1 font-semibold capitalize">
              {String(picked.props.kind)} {String(picked.props.id ?? '')}
            </div>
            <table>
              <tbody>
                {(POPUP_ROWS[String(picked.props.kind)] ?? [])
                  .filter(([, key]) => picked.props[key] !== undefined && picked.props[key] !== null)
                  .map(([label, key, unit]) => (
                    <tr key={key}>
                      <td className="pr-2 text-slate-400">{label}</td>
                      <td className="font-medium">
                        {String(picked.props[key])}
                        {unit ? ` ${unit}` : ''}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </Popup>
      )}
    </Map>
  )
}
