# Deploying SWMMCanada

The app is **decoupled**: a containerized backend service and a static frontend.

```
 ┌─────────────────────────────┐        HTTPS         ┌──────────────────────────────┐
 │  Frontend (static SPA)      │   /api/v1/tasks ...  │  Backend (FastAPI container)  │
 │  GitHub Pages               │ ───────────────────▶ │  ghcr.io/.../swmmcanada       │
 │  …github.io/SWMMCanada/     │ ◀─────────────────── │  any Docker host              │
 └─────────────────────────────┘   model .zip + GeoJSON└──────────────────────────────┘
        built with                                       CORS must allow the
        VITE_API_URL ──────────────── points at ───────▶ Pages origin (ALLOWED_ORIGINS)
```

Two independent moving parts, wired by two values: the frontend's `VITE_API_URL` points at
the backend; the backend's `ALLOWED_ORIGINS` lets the frontend's origin call it.

---

## Backend — Docker image on GHCR

**Image:** `ghcr.io/zhonghao1995/swmmcanada`
Built and pushed by [`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml)
on every push to `main` that touches `backend/**`, on `v*` tags, and via manual dispatch.
Tags: `latest` (main), `sha-<commit>` (every build), and `vX.Y.Z` / `vX.Y` (on release tags).

**Run it anywhere Docker runs:**

```bash
docker run --rm -p 8000:8000 \
  -e ALLOWED_ORIGINS=https://zhonghao1995.github.io \
  ghcr.io/zhonghao1995/swmmcanada:latest
# health: curl http://localhost:8000/api/v1/healthz  -> {"status":"ok"}
```

**Runtime config (env vars):**

| Var | Default | Purpose |
|-----|---------|---------|
| `PORT` | `8000` | Listen port. PaaS hosts (Render/Fly/Railway) inject this automatically. |
| `ALLOWED_ORIGINS` | `*` | Comma-separated CORS origins. In production set it to your frontend origin, e.g. `https://zhonghao1995.github.io`. |

**Notes**
- The live pipeline fetches external open data (OpenStreetMap, NRCan, ECCC) at request time —
  the host needs outbound network and a bit of RAM/CPU headroom (the geospatial stack is heavy).
  Good fits: Fly.io, Render, Railway, or any small VM.
- The GHCR package is **private** on first push (linked to the repo). To allow anonymous
  `docker pull`, make it public under the repo's **Packages** settings.

---

## Frontend — static site on GitHub Pages

Deployed by [`.github/workflows/deploy-frontend.yml`](.github/workflows/deploy-frontend.yml)
on every push to `main` that touches `frontend/**` (and via manual dispatch) to the project
site at **https://zhonghao1995.github.io/SWMMCanada/**.

**One-time repo setup** (Settings, not code — the deploy is inert until these are done):

1. **Settings → Pages → Source = GitHub Actions.**
2. **Settings → Secrets and variables → Actions → Variables →** add a **variable** (not a secret)
   named **`VITE_API_URL`** = the absolute backend URL, no trailing slash
   (e.g. `https://swmmcanada.fly.dev`). If unset, the built site's `/api/v1` calls hit Pages'
   own origin and 404.
3. On the backend, set `ALLOWED_ORIGINS=https://zhonghao1995.github.io` so the browser's
   cross-origin calls are allowed.

**Build-time env** (the workflow sets these): `VITE_BASE=/SWMMCanada/` (project-page base path)
and `VITE_API_URL` (from the repo variable above).

**Local dev is unchanged:** `cd frontend && npm run dev` — leave `VITE_API_URL` empty and Vite
proxies `/api` to the backend on `localhost:8000`. See [`frontend/.env.example`](frontend/.env.example).

---

## Go-live checklist

- [ ] Backend deployed to a Docker host; `ALLOWED_ORIGINS` includes the Pages origin.
- [ ] Actions variable `VITE_API_URL` set to that backend's URL.
- [ ] Settings → Pages → Source = GitHub Actions.
- [ ] (Optional) GHCR package made public for anonymous pulls.
- [ ] Push to `main` (or run the two workflows manually) and confirm both go green.
