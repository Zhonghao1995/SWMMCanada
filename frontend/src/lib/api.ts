import type { FeatureCollection } from 'geojson'
import type { Aoi, JobProgress, JobStatus } from '../types'

// Client for the backend tasks-api async contract
// (docs/specs/00-integration.md §2 — authoritative HTTP surface):
//   POST   /api/v1/tasks              -> 202 { task_id, status }
//   GET    /api/v1/tasks/{id}         -> 200 TaskStatus { state, progress_pct, stage, error? }
//   GET    /api/v1/tasks/{id}/result  -> 200 (zip)
// Exact TaskStatus JSON field names follow docs/specs/10-tasks-api.md; adjust the
// small mapping below if they differ. With no backend running, fetch throws and the
// store surfaces a clear "backend not running" status.

export interface SubmitParams {
  aoi: Aoi
  startDate: string
  endDate: string
}

const API = '/api/v1'

const STATE_MAP: Record<string, JobStatus> = {
  QUEUED: 'queued',
  RUNNING: 'running',
  SUCCEEDED: 'succeeded',
  FAILED: 'failed',
  CANCELLED: 'cancelled',
}

export async function submitTask(params: SubmitParams): Promise<{ taskId: string }> {
  const body = new FormData()
  body.append('start_date', params.startDate)
  body.append('end_date', params.endDate)
  if (params.aoi.source === 'upload') body.append('file', params.aoi.file)
  else body.append('polygon', JSON.stringify(params.aoi.polygon))

  const r = await fetch(`${API}/tasks`, { method: 'POST', body })
  if (!r.ok) throw new Error(`submit failed: HTTP ${r.status}`)
  const j = (await r.json()) as { task_id: string }
  return { taskId: j.task_id }
}

interface TaskStatusDto {
  state: string
  progress_pct?: number
  stage?: string
  mode?: string
  error?: { message?: string }
  message?: string
}

export async function pollTask(taskId: string): Promise<JobProgress> {
  const r = await fetch(`${API}/tasks/${taskId}`)
  if (!r.ok) throw new Error(`poll failed: HTTP ${r.status}`)
  const j = (await r.json()) as TaskStatusDto
  const status = STATE_MAP[j.state] ?? 'running'
  return {
    status,
    stage: j.stage,
    progressPct: j.progress_pct,
    message: j.error?.message ?? j.message,
    mode: j.mode,
    resultUrl: status === 'succeeded' ? `${API}/tasks/${taskId}/result` : undefined,
  }
}

// The model preview (GeoJSON of network + subcatchments) for the map layers.
export async function fetchPreview(taskId: string): Promise<FeatureCollection | null> {
  try {
    const r = await fetch(`${API}/tasks/${taskId}/preview`)
    if (!r.ok) return null
    return (await r.json()) as FeatureCollection
  } catch {
    return null
  }
}
