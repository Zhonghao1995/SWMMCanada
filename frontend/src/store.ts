import { create } from 'zustand'
import type { Feature, FeatureCollection, Polygon, Position } from 'geojson'
import type { Aoi, JobProgress } from './types'
import { fetchPreview, pollTask, submitTask } from './lib/api'

export type LayerKey = 'subcatchments' | 'conduits' | 'junctions'

interface AppState {
  aoi: Aoi | null
  drawing: boolean
  draft: Position[] // in-progress polygon vertices [lng, lat]
  startDate: string
  endDate: string
  job: JobProgress
  preview: FeatureCollection | null // model geometry (network + subcatchments)
  layers: Record<LayerKey, boolean>

  startDraw: () => void
  addVertex: (lng: number, lat: number) => void
  finishDraw: () => void
  cancelDraw: () => void
  clearAoi: () => void
  setUpload: (file: File) => void
  setDates: (start: string, end: string) => void
  toggleLayer: (key: LayerKey) => void
  submit: () => Promise<void>
}

function polygonFromDraft(draft: Position[]): Feature<Polygon> {
  const ring = [...draft, draft[0]] // close the ring
  return { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [ring] } }
}

const TERMINAL = new Set(['succeeded', 'failed', 'cancelled'])
const DEFAULT_LAYERS: Record<LayerKey, boolean> = { subcatchments: true, conduits: true, junctions: true }

export const useStore = create<AppState>((set, get) => ({
  aoi: null,
  drawing: false,
  draft: [],
  startDate: '2020-01-01',
  endDate: '2020-12-31',
  job: { status: 'idle' },
  preview: null,
  layers: DEFAULT_LAYERS,

  startDraw: () => set({ drawing: true, draft: [], aoi: null, job: { status: 'idle' }, preview: null }),
  addVertex: (lng, lat) => set((s) => (s.drawing ? { draft: [...s.draft, [lng, lat]] } : {})),
  finishDraw: () => {
    const { draft } = get()
    if (draft.length < 3) return
    set({ drawing: false, aoi: { source: 'draw', polygon: polygonFromDraft(draft) }, draft: [] })
  },
  cancelDraw: () => set({ drawing: false, draft: [] }),
  clearAoi: () => set({ aoi: null, draft: [], drawing: false, job: { status: 'idle' }, preview: null }),
  setUpload: (file) =>
    set({ aoi: { source: 'upload', file, name: file.name }, drawing: false, draft: [], job: { status: 'idle' }, preview: null }),
  setDates: (startDate, endDate) => set({ startDate, endDate }),
  toggleLayer: (key) => set((s) => ({ layers: { ...s.layers, [key]: !s.layers[key] } })),

  submit: async () => {
    const { aoi, startDate, endDate } = get()
    if (!aoi) return
    set({ job: { status: 'queued' }, preview: null })
    try {
      const { taskId } = await submitTask({ aoi, startDate, endDate })
      for (;;) {
        const p = await pollTask(taskId)
        set({ job: p })
        if (TERMINAL.has(p.status)) {
          if (p.status === 'succeeded') set({ preview: await fetchPreview(taskId) })
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
