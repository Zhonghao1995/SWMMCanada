import type { Feature, MultiPolygon, Polygon } from 'geojson'

// The AOI the user provides — a drawn polygon OR an uploaded boundary file.
// Uploads are parsed by the backend the moment they are picked (POST /aoi/preview),
// which returns the boundary geometry + bbox + area so the map and the rainfall
// check work for shapefiles too. The raw File is still what gets submitted.
export interface DrawnAoi {
  source: 'draw'
  polygon: Feature<Polygon>
}
export interface UploadedAoi {
  source: 'upload'
  file: File
  name: string
  boundary?: Feature<Polygon | MultiPolygon> // parsed by the backend on upload
  bbox?: [number, number, number, number]    // lon/lat, from the same parse
  areaKm2?: number
}
export type Aoi = DrawnAoi | UploadedAoi

// Async build job — mirrors the backend tasks-api state machine
// (docs/specs/10-tasks-api.md + docs/specs/00-integration.md §2):
// QUEUED → RUNNING → {SUCCEEDED | FAILED | CANCELLED}. 'idle' is pre-submit (UI only).
export type JobStatus = 'idle' | 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface JobProgress {
  status: JobStatus
  stage?: string // VALIDATING | ACQUIRING | DERIVING | NETWORK | BUILDING | PACKAGING
  progressPct?: number // 0–100, monotonic, coarse
  message?: string
  mode?: string // build pathway: "Real municipal network — …" or "Synthesized from open data"
  resultUrl?: string // download URL for the .inp + forcing package (zip)
}
