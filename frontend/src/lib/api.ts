import type { Feature, FeatureCollection, MultiPolygon, Polygon } from 'geojson'
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
  infiltration: InfiltrationMethod
  designStorm: DesignStormChoice | null // null = historical observed rain (default)
}

// ADR 0013: build-time infiltration choice; Horton is the default (municipal practice).
export type InfiltrationMethod = 'HORTON' | 'CURVE_NUMBER' | 'GREEN_AMPT'

// ADR 0018: user-selected design storm (return period × duration from the nearest
// ECCC IDF station) instead of historical observed rain.
export interface DesignStormChoice {
  returnPeriodYr: 2 | 5 | 10 | 25 | 50 | 100
  durationH: number // 1–24 h
}

const API = `${import.meta.env.VITE_API_URL ?? ''}/api/v1`

const STATE_MAP: Record<string, JobStatus> = {
  QUEUED: 'queued',
  RUNNING: 'running',
  SUCCEEDED: 'succeeded',
  FAILED: 'failed',
  CANCELLED: 'cancelled',
}

export interface AoiPreview {
  boundary: Feature<Polygon | MultiPolygon>
  bbox: Bbox
  areaKm2: number
}

// Parse an uploaded boundary on the backend WITHOUT starting a build, so the UI can
// draw it, show its area, and surface parse errors (bad CRS, oversize) immediately.
export async function previewAoi(file: File): Promise<AoiPreview> {
  const body = new FormData()
  body.append('file', file)
  const r = await fetch(`${API}/aoi/preview`, { method: 'POST', body })
  if (!r.ok) {
    let detail = `HTTP ${r.status}`
    try {
      detail = ((await r.json()) as { detail?: string }).detail ?? detail
    } catch {
      // non-JSON error body; keep the status text
    }
    throw new Error(detail)
  }
  const j = (await r.json()) as {
    geometry: Polygon | MultiPolygon
    bbox: Bbox
    area_km2: number
  }
  return {
    boundary: { type: 'Feature', properties: {}, geometry: j.geometry },
    bbox: j.bbox,
    areaKm2: j.area_km2,
  }
}

export async function submitTask(params: SubmitParams): Promise<{ taskId: string }> {
  const body = new FormData()
  body.append('start_date', params.startDate)
  body.append('end_date', params.endDate)
  body.append('infiltration', params.infiltration)
  if (params.designStorm) {
    // ADR 0018: presence of the return period IS the mode selection.
    body.append('design_storm_yr', String(params.designStorm.returnPeriodYr))
    body.append('design_storm_h', String(params.designStorm.durationH))
  }
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

// --- Rainfall (forcing) availability check -----------------------------------
// Same data source the build uses for the [RAINGAGES] forcing: the MSC GeoMet OGC
// API (ECCC climate-stations + climate-daily, see acquire/climate.py). Queried
// directly from the browser — GeoMet is a public, CORS-enabled open API — so the
// check needs no backend and works on the static deployment. We answer "does the
// nearest ECCC gauge have daily rain records for this period?" and, when it does
// not, suggest the most recent window of the same length that does.

const GEOMET = 'https://api.weather.gc.ca'

export type Bbox = [number, number, number, number] // [minLng, minLat, maxLng, maxLat]

export interface RainfallCheck {
  available: boolean
  spanDays: number // length of the requested window, in days (inclusive)
  station?: string // name of the nearest / chosen ECCC gauge
  daysWithData?: number // days in the window with a precip record
  dataStart?: string // station's earliest daily record (ISO)
  dataEnd?: string // station's latest daily record (ISO)
  suggestStart?: string // suggested replacement window (ISO), when unavailable
  suggestEnd?: string
  message: string
}

interface GeoMetProps {
  TOTAL_PRECIPITATION?: number | null
  CLIMATE_IDENTIFIER?: string
  STATION_NAME?: string
  LOCAL_DATE?: string
}
interface GeoMetFeature {
  geometry?: { coordinates?: [number, number] }
  properties?: GeoMetProps
}
interface GeoMetFC {
  features?: GeoMetFeature[]
}

async function geomet(url: string): Promise<GeoMetFC> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`GeoMet HTTP ${r.status}`)
  return (await r.json()) as GeoMetFC
}

function isoAddDays(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + days)
  return d.toISOString().slice(0, 10)
}

function isoDayDiff(a: string, b: string): number {
  return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86_400_000)
}

const bboxStr = (b: number[]) => b.map((v) => v.toFixed(5)).join(',')

export async function checkRainfall(bbox: Bbox, startDate: string, endDate: string): Promise<RainfallCheck> {
  const spanDays = Math.max(1, isoDayDiff(startDate, endDate) + 1)
  const buf = 0.5 // ~50 km — ECCC climate gauges are sparse, so look beyond the AOI itself
  const qbbox = [bbox[0] - buf, bbox[1] - buf, bbox[2] + buf, bbox[3] + buf]

  // 1) Any daily precip records anywhere in the buffered bbox for the period?
  const daily = await geomet(
    `${GEOMET}/collections/climate-daily/items?bbox=${bboxStr(qbbox)}` +
      `&datetime=${startDate}/${endDate}&limit=10000&sortby=LOCAL_DATE&f=json`,
  )
  const byStation = new Map<string, { name: string; days: number; lon: number; lat: number }>()
  for (const f of daily.features ?? []) {
    const p = f.properties ?? {}
    if (p.TOTAL_PRECIPITATION == null) continue
    const id = p.CLIMATE_IDENTIFIER ?? '?'
    const c = f.geometry?.coordinates ?? [NaN, NaN]
    const e = byStation.get(id) ?? { name: p.STATION_NAME ?? id, days: 0, lon: c[0], lat: c[1] }
    e.days += 1
    byStation.set(id, e)
  }
  let best: { name: string; days: number; lon: number; lat: number } | undefined
  for (const e of byStation.values()) if (!best || e.days > best.days) best = e
  if (best) {
    // Honest framing: this is the nearest/most-complete gauge WITH records — it may sit
    // tens of km away (ECCC gauges are sparse); say so, with the distance, instead of
    // letting the user think we grabbed the wrong city's data.
    const km = _kmFromAoi(best.lon, best.lat, bbox)
    const where = km != null ? ` — nearest gauge with records: ${best.name}, ~${km} km from your area` : ` (gauge: ${best.name})`
    return {
      available: true,
      spanDays,
      station: best.name,
      daysWithData: best.days,
      message: `Daily rain records: ${best.days} of ${spanDays} day(s)${where}. ` +
        `The build auto-selects hourly data where available; the station and resolution actually used are shown after the build.`,
    }
  }

  // 2) No records in the period — find a nearby gauge and suggest a window from its record.
  const station = await nearestStation(qbbox, bbox)
  if (!station) {
    return {
      available: false,
      spanDays,
      message: 'No ECCC climate gauge within ~50 km — the build will fall back to a synthetic IDF design storm (T=5 yr, alternating block), clearly labelled in the result. Fine for structural checks; not real observed rain.',
    }
  }
  const range = await stationRange(station.id)
  if (!range) {
    return {
      available: false,
      spanDays,
      station: station.name,
      message: `Nearest gauge (${station.name}) has no daily rain records. Try a different area.`,
    }
  }
  let suggestEnd = range.end
  let suggestStart = isoAddDays(suggestEnd, -(spanDays - 1))
  if (isoDayDiff(range.start, suggestStart) < 0) suggestStart = range.start // clamp to record start
  return {
    available: false,
    spanDays,
    station: station.name,
    dataStart: range.start,
    dataEnd: range.end,
    suggestStart,
    suggestEnd,
    message:
      `No rain records for this period at the nearest gauge (${station.name}); ` +
      `it has data ${range.start} → ${range.end}.`,
  }
}

function _kmFromAoi(lon: number, lat: number, aoiBbox: Bbox): number | null {
  if (!Number.isFinite(lon) || !Number.isFinite(lat)) return null
  const cx = (aoiBbox[0] + aoiBbox[2]) / 2
  const cy = (aoiBbox[1] + aoiBbox[3]) / 2
  const kmPerDegLat = 111.2
  const kmPerDegLon = kmPerDegLat * Math.cos((cy * Math.PI) / 180)
  return Math.round(Math.hypot((lon - cx) * kmPerDegLon, (lat - cy) * kmPerDegLat))
}

// The rainfall record the BUILD actually used (ADR 0014/0015) — tier, station, coverage —
// read from the task's validation.json. Null until the build reached the climate stage.
export interface ForcingInfo {
  rainfall_resolution: string
  station_name?: string
  coverage_pct?: number
  idf_station_name?: string
  return_period_yr?: number
  duration_h?: number
  total_mm?: number
  fallback_reason?: string
  requested?: boolean // true = user-selected design storm (ADR 0018), not a fallback
}

export async function fetchForcing(taskId: string): Promise<ForcingInfo | null> {
  try {
    const r = await fetch(`${API}/tasks/${taskId}/validation`)
    if (!r.ok) return null
    const j = (await r.json()) as { forcing?: ForcingInfo }
    return j.forcing ?? null
  } catch {
    return null
  }
}

async function nearestStation(qbbox: number[], aoiBbox: Bbox): Promise<{ id: string; name: string } | null> {
  const fc = await geomet(
    `${GEOMET}/collections/climate-stations/items?bbox=${bboxStr(qbbox)}&f=json&limit=500`,
  )
  const cx = (aoiBbox[0] + aoiBbox[2]) / 2
  const cy = (aoiBbox[1] + aoiBbox[3]) / 2
  let best: { id: string; name: string; d2: number } | null = null
  for (const f of fc.features ?? []) {
    const c = f.geometry?.coordinates
    const id = f.properties?.CLIMATE_IDENTIFIER
    if (!c || !id) continue
    const d2 = (c[0] - cx) ** 2 + (c[1] - cy) ** 2
    if (!best || d2 < best.d2) best = { id, name: f.properties?.STATION_NAME ?? id, d2 }
  }
  return best ? { id: best.id, name: best.name } : null
}

async function stationRange(climateId: string): Promise<{ start: string; end: string } | null> {
  const base = `${GEOMET}/collections/climate-daily/items?CLIMATE_IDENTIFIER=${encodeURIComponent(climateId)}&limit=1&f=json`
  const [first, last] = await Promise.all([geomet(`${base}&sortby=LOCAL_DATE`), geomet(`${base}&sortby=-LOCAL_DATE`)])
  const fd = first.features?.[0]?.properties?.LOCAL_DATE
  const ld = last.features?.[0]?.properties?.LOCAL_DATE
  return fd && ld ? { start: String(fd).slice(0, 10), end: String(ld).slice(0, 10) } : null
}
