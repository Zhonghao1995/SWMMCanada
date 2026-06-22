import type { Feature, Polygon } from 'geojson'

// The AOI the user provides — a drawn polygon OR an uploaded shapefile.
// Drawn polygons are GeoJSON here; uploaded shapefiles are parsed by the backend
// `geo` module, so the frontend only carries the file until submit.
export interface DrawnAoi {
  source: 'draw'
  polygon: Feature<Polygon>
}
export interface UploadedAoi {
  source: 'upload'
  file: File
  name: string
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
  resultUrl?: string // download URL for the .inp + forcing package (zip)
}
