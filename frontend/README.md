# SWMMCanada — Frontend

Static single-page app: **draw or upload an Area of Interest** over Canada, submit an
async build job, watch progress, and view/download the resulting SWMM model.

## Stack

React 19 · Vite · TypeScript · **MapLibre GL** (`react-map-gl`) · Zustand · Tailwind v4 · lucide.

## Run

```bash
npm install
npm run dev          # http://localhost:5175 ; /api is proxied to the backend on :8000
```

The map + AOI drawing/upload run standalone. Building a model needs the backend on `:8000`;
without it, the job surfaces a clear "backend not running" error.

## Layout

- `src/components/MapPanel.tsx` — MapLibre map + AOI draw + generated-model layers.
- `src/components/ControlPanel.tsx` — draw/upload, date range, submit, progress, layers, download.
- `src/store.ts` — Zustand store (AOI + job + preview state).
- `src/lib/api.ts` — async tasks-api client (submit → poll → result / preview).
