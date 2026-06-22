import { useMemo, useRef } from 'react'
import { Download, Loader2, MapPin, Play, Trash2, Upload } from 'lucide-react'
import { useStore } from '../store'

export default function ControlPanel() {
  const fileRef = useRef<HTMLInputElement>(null)
  const aoi = useStore((s) => s.aoi)
  const drawing = useStore((s) => s.drawing)
  const draft = useStore((s) => s.draft)
  const startDate = useStore((s) => s.startDate)
  const endDate = useStore((s) => s.endDate)
  const job = useStore((s) => s.job)
  const startDraw = useStore((s) => s.startDraw)
  const finishDraw = useStore((s) => s.finishDraw)
  const cancelDraw = useStore((s) => s.cancelDraw)
  const clearAoi = useStore((s) => s.clearAoi)
  const setUpload = useStore((s) => s.setUpload)
  const setDates = useStore((s) => s.setDates)
  const submit = useStore((s) => s.submit)
  const preview = useStore((s) => s.preview)
  const layers = useStore((s) => s.layers)
  const toggleLayer = useStore((s) => s.toggleLayer)

  const counts = useMemo(() => {
    const c: Record<string, number> = {}
    preview?.features.forEach((f) => {
      const k = (f.properties as { kind?: string } | null)?.kind
      if (k) c[k] = (c[k] || 0) + 1
    })
    return c
  }, [preview])

  const busy = job.status === 'queued' || job.status === 'running'

  return (
    <aside className="flex h-full w-80 shrink-0 flex-col gap-5 overflow-y-auto border-r border-slate-200 bg-white p-4">
      <header>
        <h1 className="text-lg font-semibold text-slate-800">SWMMCanada</h1>
        <p className="text-xs text-slate-500">
          Draw or upload an area → build a SWMM model from Canadian open data.
        </p>
      </header>

      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">1 · Area of interest</h2>
        {!drawing ? (
          <button
            onClick={startDraw}
            className="flex w-full items-center gap-2 rounded-md bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700"
          >
            <MapPin size={16} /> Draw polygon
          </button>
        ) : (
          <div className="flex gap-2">
            <button
              onClick={finishDraw}
              disabled={draft.length < 3}
              className="flex-1 rounded-md bg-emerald-600 px-3 py-2 text-sm font-medium text-white disabled:opacity-40"
            >
              Finish ({draft.length})
            </button>
            <button onClick={cancelDraw} className="rounded-md border border-slate-300 px-3 py-2 text-sm">
              Cancel
            </button>
          </div>
        )}
        <button
          onClick={() => fileRef.current?.click()}
          className="flex w-full items-center gap-2 rounded-md border border-slate-300 px-3 py-2 text-sm hover:bg-slate-50"
        >
          <Upload size={16} /> Upload boundary (.geojson / .zip)
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".geojson,.json,.zip"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) setUpload(f)
          }}
        />
        {aoi && (
          <div className="flex items-center justify-between rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-600">
            <span className="truncate">{aoi.source === 'draw' ? 'Polygon drawn' : aoi.name}</span>
            <button onClick={clearAoi} className="ml-2 text-slate-400 hover:text-red-500">
              <Trash2 size={15} />
            </button>
          </div>
        )}
        {drawing && (
          <p className="text-[11px] text-slate-400">Click to add vertices; double-click to finish.</p>
        )}
      </section>

      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">2 · Period</h2>
        <div className="flex items-center gap-2 text-sm">
          <input
            type="date"
            value={startDate}
            onChange={(e) => setDates(e.target.value, endDate)}
            className="w-full rounded-md border border-slate-300 px-2 py-1"
          />
          <span className="text-slate-400">→</span>
          <input
            type="date"
            value={endDate}
            onChange={(e) => setDates(startDate, e.target.value)}
            className="w-full rounded-md border border-slate-300 px-2 py-1"
          />
        </div>
      </section>

      <section className="space-y-2">
        <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">3 · Build</h2>
        <button
          onClick={submit}
          disabled={!aoi || busy}
          className="flex w-full items-center justify-center gap-2 rounded-md bg-slate-800 px-3 py-2 text-sm font-medium text-white hover:bg-slate-900 disabled:opacity-40"
        >
          {busy ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
          {busy ? (job.stage ?? 'Building…') : 'Build SWMM model'}
        </button>
        {job.status !== 'idle' && (
          <div className="rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-600">
            <div>
              Status: {job.status}
              {job.stage ? ` · ${job.stage}` : ''}
              {typeof job.progressPct === 'number' ? ` · ${Math.round(job.progressPct)}%` : ''}
            </div>
            {job.mode && (
              <div
                className={`mt-1 inline-block rounded px-1.5 py-0.5 font-medium ${
                  job.mode.startsWith('Real')
                    ? 'bg-emerald-100 text-emerald-700'
                    : 'bg-amber-100 text-amber-700'
                }`}
              >
                {job.mode.startsWith('Real') ? '🏙️ ' : '🧩 '}
                {job.mode}
              </div>
            )}
            {job.message && <div className="mt-1 text-slate-500">{job.message}</div>}
            {job.status === 'succeeded' && job.resultUrl && (
              <a href={job.resultUrl} className="mt-2 flex items-center gap-1 font-medium text-blue-600">
                <Download size={14} /> Download model
              </a>
            )}
          </div>
        )}
      </section>

      {preview && (
        <section className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">4 · Model layers</h2>
          <p className="text-[11px] text-slate-500">
            {counts.subcatchment ?? 0} subcatchments · {counts.conduit ?? 0} conduits ·{' '}
            {counts.junction ?? 0} junctions · {counts.outfall ?? 0} outfall
          </p>
          {(
            [
              ['subcatchments', 'Subcatchments', '#22c55e'],
              ['conduits', 'Conduits', '#2563eb'],
              ['junctions', 'Junctions', '#1d4ed8'],
            ] as const
          ).map(([key, label, color]) => (
            <label key={key} className="flex items-center gap-2 text-sm text-slate-600">
              <input type="checkbox" checked={layers[key]} onChange={() => toggleLayer(key)} />
              <span className="inline-block h-3 w-3 rounded-sm" style={{ background: color }} />
              {label}
            </label>
          ))}
        </section>
      )}

      <footer className="mt-auto text-[10px] leading-relaxed text-slate-400">
        SWMMCanada · React · MapLibre · Tailwind. Basemap © OpenStreetMap © CARTO.
      </footer>
    </aside>
  )
}
