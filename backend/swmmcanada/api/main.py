"""Uvicorn entry point: `uvicorn swmmcanada.api.main:app --port 8000`.
Runs the live pipeline (OSM + NRCan MRDEM + ECCC GeoMet) in a background thread per task."""
from swmmcanada.api import create_app

app = create_app()
