import { useEffect, useRef } from 'react'
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

  // Fit the map to the model when a preview loads.
  useEffect(() => {
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
      cursor={drawing ? 'crosshair' : ''}
      onClick={(e: MapLayerMouseEvent) => {
        if (drawing) addVertex(e.lngLat.lng, e.lngLat.lat)
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
    </Map>
  )
}
