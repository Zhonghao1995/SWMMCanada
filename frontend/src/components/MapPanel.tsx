import { useEffect, useMemo, useRef, useState } from 'react'
import Map, {
  Layer,
  NavigationControl,
  ScaleControl,
  Source,
  type MapLayerMouseEvent,
  type MapRef,
} from 'react-map-gl/maplibre'
import type { FilterSpecification, LngLatBoundsLike, StyleSpecification } from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Feature, FeatureCollection } from 'geojson'
import { Eye, EyeOff, Layers as LayersIcon, LocateFixed, X } from 'lucide-react'
import { useStore, type LayerKey } from '../store'

// Free CARTO Positron raster basemap — no API token. The glyphs endpoint (same CARTO
// CDN as the tiles) serves the flow-direction arrows (symbol layers need a font
// source); if it is unreachable the arrows simply do not render, nothing else breaks.
const MAP_STYLE: StyleSpecification = {
  version: 8,
  glyphs: 'https://tiles.basemaps.cartocdn.com/fonts/{fontstack}/{range}.pbf',
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
// carries. Clicks hit the invisible fat "hit" layers (a 1.6px line is not a click
// target); topmost declared wins: outfall > junction > conduit > subcatchment.
const INSPECT_LAYERS = ['m-outfall-hit', 'm-junction-hit', 'm-conduit-hit', 'm-sub-fill']

const KIND_COLORS: Record<string, string> = {
  subcatchment: '#22c55e',
  conduit: '#2563eb',
  junction: '#1d4ed8',
  outfall: '#ef4444',
}

// Two drainage systems, two colours (ADR 0011 tags ride the preview): storm stays the
// blue family, sanitary goes brick — the classic utility-drawing split.
export const STORM_COLOR = '#2563eb'
export const SANITARY_COLOR = '#c2410c'
const CONDUIT_COLOR = ['match', ['get', 'system'], 'sanitary', SANITARY_COLOR, STORM_COLOR]
const NODE_COLOR = ['match', ['get', 'system'], 'sanitary', SANITARY_COLOR, '#1d4ed8']

// Pipe width encodes diameter (m -> px), clamped at the interpolation ends.
const CONDUIT_WIDTH = [
  'interpolate', ['linear'], ['get', 'diameter_m'],
  0.2, 1.2,
  0.45, 2.4,
  0.9, 4.2,
  1.8, 7,
]

// Hovered OR selected features get emphasised (feature-states set from
// onMouseMove / onClick; the selected one stays lit while its info card is open).
const hoverCase = (on: unknown, off: unknown) => [
  'case',
  ['any',
    ['boolean', ['feature-state', 'hover'], false],
    ['boolean', ['feature-state', 'selected'], false]],
  on, off,
]

// MapLibre expression arrays vs react-map-gl's typed paint props.
const expr = (e: unknown) => e as unknown as number
const exprColor = (e: unknown) => e as unknown as string
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
  fid: number | string | undefined // source feature id, keeps the selection lit
  props: Record<string, unknown>
}

const LAYER_ROWS: [LayerKey, string, string][] = [
  ['subcatchments', 'Subcatchments', '#22c55e'],
  ['storm', 'Storm network', STORM_COLOR],
  ['sanitary', 'Sanitary network', SANITARY_COLOR],
]

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
  const toggleLayer = useStore((s) => s.toggleLayer)
  const [picked, setPicked] = useState<Picked | null>(null)
  const [hovering, setHovering] = useState(false)
  const hoverId = useRef<number | string | null>(null)

  // Per-layer element counts for the floating Layers card.
  const layerCounts = useMemo(() => {
    const c: Record<LayerKey, number> = { subcatchments: 0, storm: 0, sanitary: 0 }
    preview?.features.forEach((f) => {
      const p = f.properties as { kind?: string; system?: string } | null
      if (!p?.kind) return
      if (p.kind === 'subcatchment') c.subcatchments++
      else if (p.system === 'sanitary') c.sanitary++
      else c.storm++
    })
    return c
  }, [preview])

  // System toggles filter pipes AND their nodes together (an engineer hides a system,
  // not an element type). An empty allow-list must match nothing, hence the sentinel.
  const allowedSystems = [
    ...(layers.storm ? ['storm_minor', 'storm_major'] : []),
    ...(layers.sanitary ? ['sanitary'] : []),
  ]
  const sysFilter = ['in', ['get', 'system'],
    ['literal', allowedSystems.length ? allowedSystems : ['__none__']]]
  const conduitFilter = ['all', kindIs('conduit'), sysFilter] as FilterSpecification
  const junctionFilter = ['all', kindIs('junction'), sysFilter] as FilterSpecification

  const setHover = (id: number | string | undefined) => {
    const map = mapRef.current
    if (!map) return
    if (hoverId.current !== null && hoverId.current !== id) {
      map.setFeatureState({ source: 'model', id: hoverId.current }, { hover: false })
      hoverId.current = null
    }
    if (id !== undefined) {
      map.setFeatureState({ source: 'model', id }, { hover: true })
      hoverId.current = id
    }
  }

  // Selection follows the info card: light the clicked feature, clear the previous one.
  const select = (next: Picked | null) => {
    const map = mapRef.current
    setPicked((prev) => {
      if (map && prev?.fid !== undefined && prev.fid !== next?.fid) {
        map.setFeatureState({ source: 'model', id: prev.fid }, { selected: false })
      }
      if (map && next?.fid !== undefined) {
        map.setFeatureState({ source: 'model', id: next.fid }, { selected: true })
      }
      return next
    })
  }

  // Fit the map to the model when a preview loads; a new build voids the old selection.
  useEffect(() => {
    setPicked(null)
    if (preview && mapRef.current) {
      const b = bboxOf(preview)
      if (b) mapRef.current.fitBounds(b, { padding: 50, duration: 800 })
    }
  }, [preview])

  // Drawing owns the map: drop the info card while placing vertices.
  useEffect(() => {
    if (drawing) setPicked(null)
  }, [drawing])

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
    <div className="relative h-full w-full">
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
        if (f?.properties) {
          select({ lng: e.lngLat.lng, lat: e.lngLat.lat, fid: f.id, props: f.properties })
        } else {
          select(null)
        }
      }}
      onMouseMove={(e: MapLayerMouseEvent) => {
        if (drawing) return
        const f = e.features?.[0]
        setHover(f?.id as number | string | undefined)
        setHovering(!!f)
      }}
      onMouseLeave={() => {
        setHover(undefined)
        setHovering(false)
      }}
      onDblClick={(e: MapLayerMouseEvent) => {
        if (drawing) {
          e.preventDefault()
          finishDraw()
        }
      }}
    >
      <NavigationControl position="top-right" showCompass={false} />
      <ScaleControl position="bottom-right" />

      {/* Generated model: subcatchments / conduits / junctions / outfall.
          generateId powers the hover feature-state. */}
      <Source id="model" type="geojson" data={model} generateId>
        <Layer id="m-sub-fill" type="fill" filter={kindIs('subcatchment')} layout={vis(layers.subcatchments)}
          paint={{ 'fill-color': '#22c55e',
                   'fill-opacity': expr(hoverCase(0.32, 0.18)) }} />
        <Layer id="m-sub-line" type="line" filter={kindIs('subcatchment')} layout={vis(layers.subcatchments)}
          paint={{ 'line-color': '#16a34a', 'line-width': 0.6, 'line-opacity': 0.55 }} />
        <Layer id="m-conduit" type="line" filter={conduitFilter}
          layout={{ 'line-cap': 'round' }}
          paint={{ 'line-color': exprColor(CONDUIT_COLOR),
                   'line-width': expr(hoverCase(['+', CONDUIT_WIDTH, 1.6], CONDUIT_WIDTH)) }} />
        {/* Flow direction: white chevrons with a system-coloured halo riding the pipe —
            same-colour arrows disappear into the line; white cores read on any background. */}
        <Layer id="m-flow" type="symbol" filter={conduitFilter} minzoom={14}
          layout={{ 'symbol-placement': 'line', 'symbol-spacing': 64,
                    'text-field': '>', 'text-size': 13, 'text-font': ['Open Sans Bold'],
                    'text-keep-upright': false, 'text-allow-overlap': true,
                    'text-rotation-alignment': 'map' }}
          paint={{ 'text-color': '#ffffff',
                   'text-halo-color': exprColor(CONDUIT_COLOR), 'text-halo-width': 1.6 }} />
        <Layer id="m-junction" type="circle" filter={junctionFilter}
          paint={{ 'circle-radius': expr(hoverCase(5, 3.4)),
                   'circle-color': exprColor(NODE_COLOR),
                   'circle-stroke-width': 1, 'circle-stroke-color': '#ffffff' }} />
        <Layer id="m-outfall" type="circle" filter={kindIs('outfall')}
          paint={{ 'circle-radius': expr(hoverCase(8.5, 7)), 'circle-color': '#ef4444',
                   'circle-stroke-width': 2, 'circle-stroke-color': '#ffffff' }} />

        {/* Invisible fat hit targets for click-to-inspect; declared last = queried first. */}
        <Layer id="m-conduit-hit" type="line" filter={conduitFilter}
          paint={{ 'line-color': '#000000', 'line-width': 12, 'line-opacity': 0 }} />
        <Layer id="m-junction-hit" type="circle" filter={junctionFilter}
          paint={{ 'circle-radius': 9, 'circle-color': '#000000', 'circle-opacity': 0 }} />
        <Layer id="m-outfall-hit" type="circle" filter={kindIs('outfall')}
          paint={{ 'circle-radius': 12, 'circle-color': '#000000', 'circle-opacity': 0 }} />
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

    </Map>

    {/* Floating Layers card (top-left), Agentic-SWMM style */}
    {preview && (
      <div className="absolute left-3 top-3 z-10 w-52 overflow-hidden rounded-xl bg-white/95 shadow-lg ring-1 ring-slate-900/5 backdrop-blur">
        <div className="flex items-center gap-1.5 border-b border-slate-100 px-3 py-2">
          <LayersIcon size={13} className="text-slate-500" />
          <span className="text-xs font-semibold text-slate-700">Layers</span>
        </div>
        <div className="p-1.5">
          {LAYER_ROWS.filter(([key]) => key !== 'sanitary' || layerCounts.sanitary > 0).map(
            ([key, label, color]) => (
              <button
                key={key}
                onClick={() => toggleLayer(key)}
                className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-xs hover:bg-slate-50"
              >
                <span
                  className={`h-2.5 w-2.5 shrink-0 rounded-full ${layers[key] ? '' : 'opacity-25'}`}
                  style={{ background: color }}
                />
                <span className={`flex-1 ${layers[key] ? 'text-slate-700' : 'text-slate-400'}`}>
                  {label}
                </span>
                <span className="text-[10px] tabular-nums text-slate-400">{layerCounts[key]}</span>
                {layers[key] ? (
                  <Eye size={13} className="text-slate-400" />
                ) : (
                  <EyeOff size={13} className="text-slate-300" />
                )}
              </button>
            ),
          )}
        </div>
        <p className="border-t border-slate-100 px-3 py-1.5 text-[10px] leading-snug text-slate-400">
          Width = pipe diameter · arrows = flow direction (zoom in) · click any element
        </p>
      </div>
    )}

    {/* Floating info card (bottom-left): click-to-inspect, first-pass QC (ADR 0019) */}
    {picked && (
      <div className="absolute bottom-6 left-3 z-10 w-60 overflow-hidden rounded-xl bg-white shadow-lg ring-1 ring-slate-900/5">
        <div className="flex items-center gap-1.5 border-b border-slate-100 px-3 py-2">
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-full"
            style={{
              background:
                picked.props.system === 'sanitary'
                  ? SANITARY_COLOR
                  : KIND_COLORS[String(picked.props.kind)] ?? '#64748b',
            }}
          />
          <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400">
            {String(picked.props.kind)}
          </span>
          <span className="ml-auto font-mono text-xs font-semibold text-slate-700">
            {String(picked.props.id ?? '')}
          </span>
          <button
            onClick={() => select(null)}
            className="rounded p-0.5 text-slate-300 hover:bg-slate-100 hover:text-slate-500"
          >
            <X size={13} />
          </button>
        </div>
        <div className="px-3 py-1.5">
          {(POPUP_ROWS[String(picked.props.kind)] ?? [])
            .filter(([, key]) => picked.props[key] !== undefined && picked.props[key] !== null)
            .map(([label, key, unit]) => (
              <div key={key} className="flex items-baseline justify-between gap-4 py-[3px] text-xs">
                <span className="text-slate-400">{label}</span>
                <span className="font-medium text-slate-700">
                  {String(picked.props[key])}
                  {unit ? <span className="ml-0.5 font-normal text-slate-400">{unit}</span> : null}
                </span>
              </div>
            ))}
        </div>
        <div className="px-2.5 pb-2.5">
          <button
            onClick={() =>
              mapRef.current?.flyTo({
                center: [picked.lng, picked.lat],
                zoom: Math.max(mapRef.current.getZoom(), 16.5),
                duration: 700,
              })
            }
            className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-700"
          >
            <LocateFixed size={13} /> Fly to
          </button>
        </div>
      </div>
    )}
    </div>
  )
}
