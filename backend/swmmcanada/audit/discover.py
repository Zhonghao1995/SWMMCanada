"""Service discovery — where a city's published layers can be enumerated.

**Single source of truth:** the URLs are read back out of the city adapter modules rather
than copied into a second catalogue. An adapter already has to know where its data lives;
a hand-maintained duplicate would be a second truth that silently drifts the first time a
city migrates a server (ADR 0030's whole thesis, applied to itself).

Non-ArcGIS cities (static SHP/ZIP downloads, non-Esri open-data APIs) cannot be enumerated
this way. They are listed explicitly in ``NON_ARCGIS`` so they appear in the audit as a
known, named gap instead of vanishing from the fleet count.
"""
from __future__ import annotations

import importlib
import re
from typing import Dict, List, Optional

from swmmcanada.sources.cities.registry import CITIES

#: Cities whose data is not served from an enumerable ArcGIS catalogue, with the reason.
#: They still get capability rows — measured by hand-written probes or left as gaps — but
#: they must never be silently absent from the fleet report.
#: Cities whose ArcGIS *catalogue* cannot be walked because each service sits behind its own
#: proxy hash — only the services an adapter already names can be measured. Recorded so the
#: report can say "these layer counts are a floor, not a census".
PER_SERVICE_PROXY: Dict[str, str] = {
    "kingston": "utility.arcgis.com/usrsvcs proxy: one opaque hash per service, no folder listing",
}

NON_ARCGIS: Dict[str, str] = {
    "northvandistrict": "static SHP bundle download (geoweb.dnv.org/Products/Data/SHP)",
    "windsor": "static ZIP download (opendata.citywindsor.ca)",
}

# Matches every ArcGIS host shape in the fleet: `/arcgis/rest/services`, `/server/rest/services`
# and Kingston-style per-service proxies `/usrsvcs/servers/<hash>/rest/services`.
_ARCGIS_ROOT = re.compile(r"^https://[^\s\"']+/rest/services(?:/.*)?$")
# A service root we can enumerate directly ends at .../MapServer or .../FeatureServer;
# anything shallower is a *folder* whose services must be listed first.
_SERVICE_END = re.compile(r"/(MapServer|FeatureServer)(/\d+)?/?$")


def _module_for(city_key: str):
    return importlib.import_module(f"swmmcanada.sources.cities.{city_key}")


def _parent_folder(root: str) -> Optional[str]:
    """The catalogue folder containing a service, or None if the URL is already a folder.

    Adapters name only the two or three services they consume, so scanning exactly what they
    reference finds exactly what we already use — and the audit exists to find what we do
    *not*. Victoria references 3 services and publishes 16; its curbs, curb drops and
    sidewalks (the Level 3 raw material) live in one of the 13 we never looked at.
    """
    if not _SERVICE_END.search(root):
        return None
    parent = root.rstrip("/").rsplit("/", 2)[0]
    return parent if "/rest/services" in parent else None


def service_roots(city_key: str) -> List[str]:
    """Distinct ArcGIS service/folder roots referenced by a city adapter's module-level
    string constants. Layer-id suffixes are trimmed so ``.../MapServer/12`` collapses to the
    service itself (the scanner enumerates every layer, not just the ones we already use —
    finding the layers we *don't* consume is the point of the audit)."""
    mod = _module_for(city_key)
    roots: List[str] = []
    for name in dir(mod):
        if name.startswith("__"):
            continue
        val = getattr(mod, name, None)
        if not isinstance(val, str) or not _ARCGIS_ROOT.match(val):
            continue
        root = re.sub(r"/\d+/?$", "", val.rstrip("/"))
        if root not in roots:
            roots.append(root)
    # Also walk the folder each referenced service sits in, so sibling services the adapter
    # never needed still enter the audit.
    for root in list(roots):
        parent = _parent_folder(root)
        if parent and parent not in roots:
            roots.append(parent)
    return roots


def is_enumerable(root: str) -> bool:
    """True when the URL is a service we can list layers on directly."""
    return bool(_SERVICE_END.search(root))


def fleet() -> List[str]:
    """Every supported city key, plus the external reference cities (ADR 0030) that are in
    the audit but not in the supported registry."""
    return [c.key for c in CITIES] + list(EXTERNAL_REFERENCE)


#: Not supported cities — audited as reference/validation only (ADR 0030). Hamilton is the
#: only known Level 1 candidate anywhere, so excluding it risks a false "no instances exist"
#: conclusion and an official-source branch with no test subject.
EXTERNAL_REFERENCE: Dict[str, List[str]] = {
    # ArcGIS Online org behind open.hamilton.ca (owner "OpenHamilton"), located 2026-08-12
    # via the AGOL item search for the combined-catchment layer itself.
    "hamilton": [
        "https://services.arcgis.com/rYz782eMbySr2srL/arcgis/rest/services",
    ],
}
