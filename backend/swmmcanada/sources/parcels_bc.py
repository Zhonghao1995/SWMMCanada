"""ParcelMap BC parcel fabric (ADR 0023 cut 2, #138) — open cadastre for synthesis cells.

BC publishes the province-wide parcel fabric (WHSE_CADASTRE.PMBC_PARCEL_FABRIC_POLY_SVW,
OGL-BC) through the openmaps WFS. The layer's native SRS is BC Albers (EPSG:3005) and its
bbox filter only matches in that SRS, so the 4326 bbox is reprojected for the query while
features are requested back in EPSG:4326 (verified live 2026-07-15: 694 parcels in a
~450 m Langford window).

``PARCEL_CLASS='Road'`` polygons are the road surface itself — they span many cells and
must not join the lot-snapping set; the road surface stays with the geometric remainder
of whichever cell it crosses. Outside BC the bbox matches nothing and the fetch returns
[] — the caller treats that as "no open cadastre here" and keeps geometric cells.
"""
from typing import List

from swmmcanada.sources import _http

WFS = "https://openmaps.gov.bc.ca/geo/pub/ows"
LAYER = "pub:WHSE_CADASTRE.PMBC_PARCEL_FABRIC_POLY_SVW"
_PAGE = 1000
_MAX_PARCELS = 20000   # sanity cap: an AOI needing more is far beyond synthesis scale


def fetch_bc_parcels(bbox_wgs84, *, client=None) -> List[dict]:
    """Non-road parcel Features (GeoJSON, EPSG:4326) intersecting the 4326 bbox.
    Graceful: any failure returns [] — cadastre refines cell boundaries, it never blocks
    a build."""
    try:
        from pyproj import Transformer

        tr = Transformer.from_crs(4326, 3005, always_xy=True)
        left, bottom, right, top = bbox_wgs84
        x0, y0 = tr.transform(left, bottom)
        x1, y1 = tr.transform(right, top)

        get = client or _get_json
        features: List[dict] = []
        start = 0
        while start < _MAX_PARCELS:
            # every page carries the sort key: pages must share ONE ordering or
            # features duplicate/vanish across page boundaries (and GeoServer 400s
            # on startIndex without sortBy)
            payload = get(WFS, {
                "service": "WFS", "version": "2.0.0", "request": "GetFeature",
                "typeName": LAYER, "outputFormat": "application/json",
                "srsName": "EPSG:4326", "count": _PAGE, "startIndex": start,
                "sortBy": "PARCEL_FABRIC_POLY_ID",
                "bbox": f"{x0},{y0},{x1},{y1},EPSG:3005"}) or {}
            page = payload.get("features") or []
            features.extend(
                f for f in page
                if (f.get("properties") or {}).get("PARCEL_CLASS") != "Road"
                and (f.get("geometry") or {}).get("type") in ("Polygon", "MultiPolygon"))
            if len(page) < _PAGE:
                break
            start += _PAGE
        return features
    except Exception:  # noqa: BLE001 — no cadastre is a normal condition, not an error
        return []


def _get_json(url: str, params: dict):
    return _http.request_with_retry("GET", url, params=params, timeout=120).json()
