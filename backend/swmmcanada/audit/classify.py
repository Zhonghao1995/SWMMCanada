"""Layer -> (role, system) classification (ADR 0030).

Two stages, deliberately separated:

* ``suggest()`` is a **high-precision pattern matcher**. It exists only to bound the human's
  work — it proposes, it never decides. It returns ``None`` freely; a missing suggestion
  costs one human glance, a wrong one costs a wrong model.
* ``ROLE_MAP`` (in ``sources.cities.capability``) holds what a human **confirmed**. A layer
  absent from it is reported ``unclassified`` and fails the audit loudly, which is how a
  renamed or newly published municipal layer announces itself.

Why a matcher cannot be trusted alone: a city's *name* for a layer does not tell you what it
is. Victoria publishes "Sewer SubCatchment Areas" — 57 polygons at a 30 ha median, i.e. a
pump-station basin, not the per-segment unit the name implies. Names propose; counts decide.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from swmmcanada.sources.cities.capability import Role

#: Ordered, first-match-wins. Longer/more specific patterns must precede their substrings
#: ("subcatchment" before "catchment", "catch basin" before "basin").
_ROLE_PATTERNS: Tuple[Tuple[str, Role], ...] = (
    (r"sub[\s_-]?catchment|sub[\s_-]?watershed|sub[\s_-]?basin", Role.SUBCATCHMENT),
    (r"catchment|drainage[\s_-]?area|sewershed|watershed", Role.CATCHMENT),
    (r"catch[\s_-]?basin", Role.CATCH_BASIN),
    (r"\binlet", Role.INLET),
    (r"lateral|service[\s_-]?connection|\bservices?\b|\bstub\b", Role.LATERAL),
    (r"combined[\s_-]?(sewer[\s_-]?)?overflow|\bcso\b|\bscso\b|overflow", Role.CSO),
    (r"interceptor|trunk", Role.INTERCEPTOR),
    (r"outfall|discharge[\s_-]?point|\boutlet(s)?\b", Role.OUTFALL),
    (r"manhole|maintenance[\s_-]?hole|\bmh\b|access[\s_-]?chamber", Role.MANHOLE),
    (r"gravity[\s_-]?main|\bmain(s)?\b|\bpipe(s|line)?\b|\bsewer(s)?\b|conduit|culvert", Role.GRAVITY_MAIN),
    (r"parcel|\bproperty|\bfolio|cadastr", Role.PARCEL),
    (r"building|structure[\s_-]?footprint|\broof", Role.BUILDING),
    (r"\bcurb|\bkerb|gutter|sidewalk", Role.CURB),
)

#: Utilities that are not drainage. Their layers use the same asset words ("Water Mains",
#: "Methane Conduit", "Water Service Connections") and would otherwise be counted as pipe
#: segments and laterals — Coquitlam alone publishes 7,172 water mains and 26,929 water
#: service connections, enough to swamp any anchor denominator they landed in.
_NOT_DRAINAGE = re.compile(
    r"\bwater[\s_-]?(main|lateral|service|valve|meter|hydrant|node|line|pipe|distribution)|"
    r"potable|methane|\bgas\b|hydro|electric|fib(re|er)|telecom|irrigation|"
    r"\bwatermain", re.I)

#: Pressurised pipes are not gravity segments. City adapters already exclude force mains
#: from the routable graph; the audit must exclude them from the anchor count for the same
#: reason — a force main has no contributing drainage area of its own.
_PRESSURISED = re.compile(r"force[\s_-]?main|pressuri[sz]ed|siphon|\bfm\b", re.I)

#: Layers that are *about* drainage assets without *being* them. Matched before any role
#: pattern, because their names contain the asset they depict: a flow-arrow layer says
#: "Gravity Mains", a cleanout says "Sewer". Left unclassified so a human sees them.
_DERIVATIVE = re.compile(
    r"flow[\s_-]?arrow|arrow|annotation|\blabel|dimension|index[\s_-]?grid|"
    r"clean[\s_-]?out|abandoned|proposed|decommission|shadow|outline", re.I)

_SANITARY = re.compile(r"sanitary|wastewater|waste[\s_-]?water|\bsan\b|foul", re.I)
_COMBINED = re.compile(r"combined", re.I)
_STORM = re.compile(r"storm|surface[\s_-]?water|land[\s_-]?drain|\bdrainage\b", re.I)


def suggest_system(service_name: str, layer_name: str) -> Optional[str]:
    """System for a layer: **the layer name decides, the service name only fills silence.**

    The service name is the department that publishes the data, not a statement about the
    layer. Ottawa serves ``Storm Pipes``, ``Sanitary Pipes`` and ``Combined Pipes`` from one
    service called ``WastewaterInfrastructure``; letting the service win would file every
    storm pipe in the city as sanitary. Only when a layer name carries no system signal at
    all does catalogue context help ("Gravity Mains" inside "OpenData_StormDrain").

    Within one name, ``combined`` outranks the others: "Combined Overflow Wastewater
    Catchment Areas" is combined, and the word "Wastewater" there is describing what
    overflows, not which network.

    A bare "Sewer" resolves to ``None`` on purpose: it means sanitary in Victoria's
    catalogue and *everything* in Toronto's. Guessing would be worse than asking.
    """
    for text in (_spaced(layer_name), _spaced(service_name)):
        if not text:
            continue
        if _COMBINED.search(text):
            return "combined"
        if _SANITARY.search(text):
            return "sanitary"
        if _STORM.search(text):
            return "storm"
    return None


def _spaced(name: str) -> str:
    """Split camelCase/PascalCase so word-boundary patterns can see the words.

    Municipal catalogues mix conventions freely — Victoria writes "Storm Drain Gravity
    Mains", Whiterock writes "StormPipe". A ``\bpipe\b`` pattern matches the first and
    misses the second, and a missed pipe layer is a missing anchor denominator.
    """
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", name or "")


def suggest_role(layer_name: str) -> Optional[Role]:
    name = _spaced(layer_name)
    if _DERIVATIVE.search(name) or _NOT_DRAINAGE.search(name) or _PRESSURISED.search(name):
        return None
    if re.search(r"\bprelim(inary)?\b|\bdraft\b|\btest\b", name, re.I):
        return None  # preliminary/draft copies duplicate the live layer
    low = name.lower()
    for pattern, role in _ROLE_PATTERNS:
        if re.search(pattern, low):
            return role
    return None


def suggest(service_name: str, layer_name: str) -> Tuple[Optional[Role], Optional[str]]:
    """(role, system) proposal. Either half may be ``None`` — that is a request for a human,
    not a failure."""
    return suggest_role(layer_name), suggest_system(service_name, layer_name)
