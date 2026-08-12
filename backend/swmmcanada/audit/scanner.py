"""ArcGIS catalogue walk + measurement — the machine half of the Phase 0 audit (ADR 0030).

Responsibilities, in the order the walk performs them:

1. **enumerate** every service under a city's roots (one cheap request per folder);
2. **record** every service name, matched or not — an unrecognised service becomes an
   ``unclassified`` row rather than disappearing, so a city adding a layer fails loudly;
3. **measure** only the layers that classification recognises: feature count, geometry
   type, extent, fields.

Every HTTP response is cached to disk keyed by URL, so a re-run costs nothing for the parts
that did not change and an interrupted scan resumes where it stopped. The cache is the
reason this is a rerunnable asset instead of a one-off script.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from swmmcanada.sources.cities.base import ArcGISClient

#: Service/layer names worth descending into. Deliberately generous: a false positive costs
#: one request, a false negative costs a missed capability — the asymmetry that matters here.
KEYWORDS = (
    "storm", "sewer", "sanitary", "wastewater", "drain", "combined", "catch", "basin",
    "inlet", "lateral", "manhole", "outfall", "discharge", "cso", "overflow", "interceptor",
    "parcel", "propert", "building", "structure", "curb", "planimetr", "land", "utilit",
    "subcatch", "watershed", "subwatershed", "catchment", "wwtp", "treatment", "pump",
)


def _relevant(name: str) -> bool:
    low = name.lower()
    return any(k in low for k in KEYWORDS)


@dataclass
class ScanStats:
    requests: int = 0
    cache_hits: int = 0
    errors: List[str] = field(default_factory=list)


class Catalogue:
    """Cached ArcGIS REST reader. One instance per scan run."""

    def __init__(self, cache_dir: Path, client: Optional[ArcGISClient] = None,
                 *, pause: float = 0.0):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = client or ArcGISClient(timeout=60.0)
        self.pause = pause
        self.stats = ScanStats()

    def _get(self, url: str, params: Dict) -> Optional[dict]:
        key = hashlib.sha256(f"{url}?{sorted(params.items())}".encode()).hexdigest()[:24]
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            self.stats.cache_hits += 1
            return json.loads(path.read_text())
        try:
            data = self.client.get_json(url, params)
        except Exception as exc:  # noqa: BLE001 — a dead service is a finding, not a crash
            self.stats.errors.append(f"{url}: {type(exc).__name__}: {exc}")
            return None
        self.stats.requests += 1
        path.write_text(json.dumps(data))
        if self.pause:
            time.sleep(self.pause)
        return data

    # --- catalogue walk ---------------------------------------------------------------
    def services(self, root: str) -> List[str]:
        """Service URLs under a folder root. A root that already *is* a service returns
        itself, so callers need not care which shape an adapter happened to reference."""
        if root.endswith(("/MapServer", "/FeatureServer")):
            return [root]
        data = self._get(root, {"f": "json"})
        if not data:
            return []
        base = root.rstrip("/").rsplit("/rest/services", 1)[0] + "/rest/services"
        out = []
        for svc in data.get("services") or []:
            name, typ = svc.get("name"), svc.get("type")
            if not name or typ not in ("MapServer", "FeatureServer"):
                continue
            out.append(f"{base}/{name}/{typ}")
        for folder in data.get("folders") or []:
            out.extend(self.services(f"{base}/{folder}"))
        return out

    def layers(self, service: str) -> List[dict]:
        data = self._get(service, {"f": "json"})
        if not data:
            return []
        return [l for l in (data.get("layers") or []) if l.get("name")]

    def layer_meta(self, service: str, layer_id: int) -> Optional[dict]:
        return self._get(f"{service}/{layer_id}", {"f": "json"})

    def count(self, service: str, layer_id: int) -> Optional[int]:
        data = self._get(f"{service}/{layer_id}/query",
                         {"where": "1=1", "returnCountOnly": "true", "f": "json"})
        if not data:
            return None
        return data.get("count")

    def count_within(self, service: str, layer_id: int, extent: Dict) -> Optional[int]:
        """Feature count restricted to an envelope — the cheap half of `coverage_geometry`.

        ADR 0030 requires the anchor denominator to be measured inside the same coverage as
        the polygon numerator. Hamilton is why: its 8,147 combined catchments cover only the
        old combined district, and dividing them by the city-wide manhole count dilutes a
        per-segment layer into "review required". One spatial-count request per
        (polygon layer x anchor layer) pair buys the correct denominator.

        An envelope is coarser than the true footprint, so this is a floor on precision, not
        the final word — but it is the difference between a right answer and a wrong one.
        """
        if not extent:
            return None
        sr = (extent.get("spatialReference") or {}).get("wkid")
        env = {"xmin": extent.get("xmin"), "ymin": extent.get("ymin"),
               "xmax": extent.get("xmax"), "ymax": extent.get("ymax")}
        if any(v is None for v in env.values()):
            return None
        if sr:
            env["spatialReference"] = {"wkid": sr}
        data = self._get(f"{service}/{layer_id}/query", {
            "where": "1=1", "returnCountOnly": "true", "f": "json",
            "geometry": json.dumps(env), "geometryType": "esriGeometryEnvelope",
            "inSR": str(sr) if sr else "", "spatialRel": "esriSpatialRelIntersects"})
        return None if not data else data.get("count")
