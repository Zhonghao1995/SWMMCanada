# SWMMCanada backend — FastAPI + heavy geospatial stack.
# Modern manylinux wheels for rasterio/fiona/pyproj/shapely/geopandas/netcdf4 bundle
# GDAL/GEOS/PROJ/netcdf, so python:3.11-slim needs no apt GDAL. The one exception is
# libexpat1 (XML), which rasterio's bundled GDAL dynamically links but does not ship.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends libexpat1 \
 && rm -rf /var/lib/apt/lists/*

# Install the package (runtime deps only), version-locked: constraints.txt pins every
# dependency to the exact versions the test suite runs on, so image builds stay
# reproducible as upstream geo packages move. Copy the dependency manifests and the
# package first so the pip layer is cached when only app code below changes.
COPY pyproject.toml constraints.txt ./
COPY swmmcanada/ ./swmmcanada/
RUN pip install --no-cache-dir -c constraints.txt .

# Fail the build early if any geo wheel is missing a bundled system lib.
RUN python -c "import geopandas, rasterio, netCDF4, osmnx, fastapi, uvicorn; print('imports ok')"

# Run as a non-root user.
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

# Round-2 F-023: the running service and every manifest it writes identify the exact
# source revision the image was built from.
ARG SOURCE_REVISION=unknown
ENV SOURCE_REVISION=${SOURCE_REVISION}

# urllib avoids adding curl to the slim image just for the healthcheck.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://localhost:'+os.environ.get('PORT','8000')+'/api/v1/healthz').status==200 else 1)"

# Hosts (Render/Fly/Railway) inject $PORT; default to 8000.
CMD ["sh","-c","uvicorn swmmcanada.api.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
