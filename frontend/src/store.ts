import { create } from 'zustand'
import type { Feature, FeatureCollection, Polygon, Position } from 'geojson'
import type { Aoi, JobProgress } from './types'
import { checkRainfall as apiCheckRainfall, fetchPreview, pollTask, previewAoi, submitTask } from './lib/api'
import type { DesignStormChoice, ForcingInfo, InfiltrationMethod } from './lib/api'
import { fetchForcing } from './lib/api'
import type { Bbox, RainfallCheck } from './lib/api'

export type LayerKey = 'subcatchments' | 'conduits' | 'junctions'

export interface RainfallState {
  status: 'idle' | 'checking' | 'done' | 'error'
  result?: RainfallCheck
  error?: string
}

interface AppState {
  aoi: Aoi | null
  drawing: boolean
  draft: Position[] // in-progress polygon vertices [lng, lat]
  startDate: string
  endDate: string
  infiltration: InfiltrationMethod // ADR 0013: pervious-area loss model for the build
  designStorm: DesignStormChoice | null // ADR 0018: null = historical observed rain (default)
  job: JobProgress
  preview: FeatureCollection | null // model geometry (network + subcatchments)
  forcing: ForcingInfo | null // rain tier/station the build actually used (ADR 0014/0015)
  layers: Record<LayerKey, boolean>
  rainfall: RainfallState // ECCC rain-data availability for the chosen AOI + period
  uploadError: string | null // boundary parse error, shown the moment a file is picked

  startDraw: () => void
  addVertex: (lng: number, lat: number) => void
  finishDraw: () => void
  cancelDraw: () => void
  clearAoi: () => void
  setUpload: (file: File) => Promise<void>
  setDates: (start: string, end: string) => void
  setInfiltration: (method: InfiltrationMethod) => void
  setDesignStorm: (choice: DesignStormChoice | null) => void
  toggleLayer: (key: LayerKey) => void
  checkRainfall: () => Promise<void>
  submit: () => Promise<void>
}

function polygonFromDraft(draft: Position[]): Feature<Polygon> {
  const ring = [...draft, draft[0]] // close the ring
  return { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [ring] } }
}

// Bbox of any nested GeoJSON coordinate array, into acc = [minX, minY, maxX, maxY].
function walkBbox(coords: unknown, acc: number[]): void {
  const a = coords as number[]
  if (typeof a[0] === 'number') {
    acc[0] = Math.min(acc[0], a[0])
    acc[1] = Math.min(acc[1], a[1])
    acc[2] = Math.max(acc[2], a[0])
    acc[3] = Math.max(acc[3], a[1])
  } else {
    ;(coords as unknown[]).forEach((c) => walkBbox(c, acc))
  }
}

// AOI → lon/lat bbox for the rainfall check. Uploads carry the bbox the backend
// parsed on pick (shapefiles included); drawn polygons and .geojson read locally.
async function aoiBbox(aoi: Aoi): Promise<Bbox | null> {
  if (aoi.source === 'upload' && aoi.bbox) return aoi.bbox
  const acc = [Infinity, Infinity, -Infinity, -Infinity]
  if (aoi.source === 'draw') {
    walkBbox(aoi.polygon.geometry.coordinates, acc)
  } else if (/\.(geo)?json$/i.test(aoi.name)) {
    try {
      const gj = JSON.parse(await aoi.file.text())
      const geoms =
        gj.type === 'FeatureCollection'
          ? gj.features.map((f: Feature) => f.geometry)
          : gj.type === 'Feature'
            ? [gj.geometry]
            : [gj]
      for (const g of geoms) if (g?.coordinates) walkBbox(g.coordinates, acc)
    } catch {
      return null
    }
  } else {
    return null // .zip shapefile — geometry only available to the backend at build time
  }
  return acc[0] === Infinity ? null : (acc as Bbox)
}

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled'])
const DEFAULT_LAYERS: Record<LayerKey, boolean> = { subcatchments: true, conduits: true, junctions: true }
const IDLE_RAIN: RainfallState = { status: 'idle' }

export const useStore = create<AppState>((set, get) => ({
  aoi: null,
  drawing: false,
  draft: [],
  startDate: '2020-01-01',
  endDate: '2020-01-07', // default to a 1-week window
  infiltration: 'HORTON', // engineering-practice default (ADR 0013)
  designStorm: null, // historical observed rain unless the user opts in (ADR 0018)
  job: { status: 'idle' },
  preview: null,
  forcing: null,
  layers: DEFAULT_LAYERS,
  rainfall: IDLE_RAIN,
  uploadError: null,

  startDraw: () =>
    set({ drawing: true, draft: [], aoi: null, job: { status: 'idle' }, preview: null, rainfall: IDLE_RAIN, uploadError: null }),
  addVertex: (lng, lat) => set((s) => (s.drawing ? { draft: [...s.draft, [lng, lat]] } : {})),
  finishDraw: () => {
    const { draft } = get()
    if (draft.length < 3) return
    set({ drawing: false, aoi: { source: 'draw', polygon: polygonFromDraft(draft) }, draft: [], rainfall: IDLE_RAIN })
  },
  cancelDraw: () => set({ drawing: false, draft: [] }),
  clearAoi: () =>
    set({ aoi: null, draft: [], drawing: false, job: { status: 'idle' }, preview: null, rainfall: IDLE_RAIN, uploadError: null }),
  setUpload: async (file) => {
    set({
      aoi: { source: 'upload', file, name: file.name },
      drawing: false,
      draft: [],
      job: { status: 'idle' },
      preview: null,
      rainfall: IDLE_RAIN,
      uploadError: null,
    })
    // Parse on the backend right away: boundary on the map, area in the panel, and
    // any parse error (bad CRS, oversize, broken zip) now instead of at build time.
    try {
      const parsed = await previewAoi(file)
      const cur = get().aoi
      if (cur?.source === 'upload' && cur.file === file) {
        set({ aoi: { ...cur, boundary: parsed.boundary, bbox: parsed.bbox, areaKm2: parsed.areaKm2 } })
      }
    } catch (err) {
      const cur = get().aoi
      if (cur?.source === 'upload' && cur.file === file) {
        set({ aoi: null, uploadError: err instanceof Error ? err.message : `${err}` })
      }
    }
  },
  setDates: (startDate, endDate) => set({ startDate, endDate, rainfall: IDLE_RAIN }),
  setInfiltration: (infiltration) => set({ infiltration }),
  setDesignStorm: (designStorm) => set({ designStorm }),
  toggleLayer: (key) => set((s) => ({ layers: { ...s.layers, [key]: !s.layers[key] } })),

  checkRainfall: async () => {
    const { aoi, startDate, endDate } = get()
    if (!aoi) return
    if (endDate < startDate) {
      set({ rainfall: { status: 'error', error: 'End date is before start date.' } })
      return
    }
    set({ rainfall: { status: 'checking' } })
    try {
      const bbox = await aoiBbox(aoi)
      if (!bbox) {
        set({
          rainfall: {
            status: 'done',
            result: {
              available: false,
              spanDays: 0,
              message:
                'Could not read the boundary extent in the browser. Rainfall is checked again at build time.',
            },
          },
        })
        return
      }
      const result = await apiCheckRainfall(bbox, startDate, endDate)
      set({ rainfall: { status: 'done', result } })
    } catch (err) {
      set({ rainfall: { status: 'error', error: `${err}` } })
    }
  },

  submit: async () => {
    const { aoi, startDate, endDate, infiltration, designStorm } = get()
    if (!aoi) return
    set({ job: { status: 'queued' }, preview: null, forcing: null })
    try {
      const { taskId } = await submitTask({ aoi, startDate, endDate, infiltration, designStorm })
      for (;;) {
        const p = await pollTask(taskId)
        set({ job: p })
        if (TERMINAL.has(p.status)) {
          if (p.status === 'succeeded') set({ preview: await fetchPreview(taskId) })
          set({ forcing: await fetchForcing(taskId) })   // what rain the build really used
          break
        }
        await new Promise((r) => setTimeout(r, 1500))
      }
    } catch (err) {
      set({ job: { status: 'failed', message: `${err}` } })
    }
  },
}))

// Dev-only test hook (so automated previews can drive the real build flow without
// fighting synthetic map events). No effect in production builds.
if (typeof window !== 'undefined' && import.meta.env?.DEV) {
  ;(window as unknown as { __store?: typeof useStore }).__store = useStore
}
