"""A city's pipe schema as data instead of code.

Every one of the 35 adapters does the same work: page the source, take the two ends of each
line, name the pipe, read two inverts, a diameter and a material, convert units, and hand
the result to :func:`base.assemble_network`. The spine already lives in ``base`` and is used
by all of them — ``assemble_network`` 35/35, ``num`` 35/35, ``material_roughness`` 34/35.

What is genuinely per-city is small and factual: which field carries each value, what a
missing value looks like, and what the units are. Those facts were being expressed as ~130
lines of near-identical code per city, where they are hard to review and easy to get subtly
wrong — the elevation audit found 884 impossible manhole depths that all came from reading
one of them incorrectly. As a table they can be checked at a glance against the city's own
documentation.

This does not replace the hand-written adapters. Cities whose data needs real decisions —
combined branches, topology inferred from geometry, seeds extracted from pipe endpoints —
keep their code, and a description here can always be swapped for it. It exists so a city
that is only a schema costs a table rather than a file.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Tuple

from swmmcanada.sources.cities import base

#: Divisor from the published diameter unit to metres.
DIAMETER_UNITS = {"mm": 1000.0, "cm": 100.0, "m": 1.0}

#: How a city writes "no value". ``zero`` is the common one; a negative sentinel needs a
#: floor rather than ``> 0`` because coastal cities have genuine near-zero inverts — Delta
#: goes to -3.65 m, and screening on zero would delete real elevations.
INVERT_MISSING = ("zero", "none")
NEGATIVE_SENTINEL_PREFIX = "below:"


@dataclass(frozen=True)
class PipeFields:
    """Which field carries what, for one city's pipe layer."""

    name: Tuple[str, ...]                       # first non-empty wins
    invert_up: str
    invert_down: str
    diameter: Optional[str] = None
    diameter_unit: str = "mm"
    material: Optional[str] = None
    #: Spelled-out material values mapped onto the codes ``material_roughness`` knows.
    material_aliases: Mapping[str, str] = field(default_factory=dict)
    length: Optional[str] = None
    shape: Optional[str] = None
    height: Optional[str] = None
    #: Published node ids at each end, when the city has them — these label the snapped
    #: nodes so the model carries the city's own manhole names.
    node_from: Optional[str] = None
    node_to: Optional[str] = None
    #: Circular pipes are as wide as they are round; cities that publish a shape column
    #: need the width filled so the non-circular sections read correctly downstream.
    width_equals_diameter: bool = False
    invert_missing: str = "zero"                # "zero" | "none" | "below:-90"
    #: What to do when two pipes claim the same name. Cities differ, and the choice is
    #: visible in their published ids, so it is described rather than guessed.
    dedupe: str = "count"                       # "count" | "objectid" | "none"
    #: Fall back to the GeoJSON feature id when every name field is empty (SHP dumps).
    name_fallback_feature_id: bool = False

    def __post_init__(self):
        if self.diameter_unit not in DIAMETER_UNITS:
            raise ValueError(f"unknown diameter unit {self.diameter_unit!r}; "
                             f"known: {sorted(DIAMETER_UNITS)}")
        if not (self.invert_missing in INVERT_MISSING
                or self.invert_missing.startswith(NEGATIVE_SENTINEL_PREFIX)):
            raise ValueError(f"unknown invert_missing {self.invert_missing!r}; use one of "
                             f"{INVERT_MISSING} or '{NEGATIVE_SENTINEL_PREFIX}<floor>'")
        if self.dedupe not in ("count", "objectid", "none"):
            raise ValueError(f"unknown dedupe policy {self.dedupe!r}")


def _invert_reader(policy: str):
    if policy == "zero":
        return lambda v: base.num(v, zero_missing=True)
    if policy == "none":
        return lambda v: base.num(v)
    floor = float(policy[len(NEGATIVE_SENTINEL_PREFIX):])

    def read(v):
        f = base.num(v)
        return f if (f is not None and f > floor) else None
    return read


def build_pipes(features, fields: PipeFields,
                config: base.AssembleConfig) -> Tuple[List[base.RawPipe], Dict]:
    """Described fields -> the same ``RawPipe`` list a hand-written adapter builds.

    Returns ``(pipes, diagnostics)``; ``diagnostics["label_points"]`` carries the published
    node ids when the city has them, for the caller to pass to ``base.safe_labels``.
    """
    read_invert = _invert_reader(fields.invert_missing)
    to_m = DIAMETER_UNITS[fields.diameter_unit]

    pipes: List[base.RawPipe] = []
    label_points: List = []
    n_no_geom = 0
    seen: Dict[str, int] = {}

    for f in features:
        p = f.get("properties") or {}
        a, b = base.line_ends(f.get("geometry"))
        if a is None or b is None:
            n_no_geom += 1
            continue

        name = next((str(p[k]) for k in fields.name if p.get(k) not in (None, "")), None)
        if name is None and fields.name_fallback_feature_id:
            name = str(f.get("id"))
        name = name if name is not None else str(p.get("OBJECTID"))
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            if fields.dedupe == "count":
                name = f"{name}_{seen[name]}"
            elif fields.dedupe == "objectid":
                name = f"{name}_{p.get('OBJECTID')}"

        dia = base.num(p.get(fields.diameter), zero_missing=True) if fields.diameter else None
        hgt = base.num(p.get(fields.height), zero_missing=True) if fields.height else None
        material = str(p.get(fields.material) or "") if fields.material else ""
        if material and fields.material_aliases:
            material = fields.material_aliases.get(material.upper(), material)

        pipes.append(base.RawPipe(
            name=name, end_a=a, end_b=b,
            inv_a=read_invert(p.get(fields.invert_up)),
            inv_b=read_invert(p.get(fields.invert_down)),
            diameter_m=(dia / to_m) if dia else None,
            roughness_n=base.material_roughness(material, config.default_roughness),
            length_m=base.num(p.get(fields.length), zero_missing=True) if fields.length else None,
            shape=p.get(fields.shape) if fields.shape else None,
            height_m=(hgt / to_m) if hgt else None,
            width_m=(dia / to_m) if (dia and fields.width_equals_diameter) else None,
        ))

        if fields.node_from and fields.node_to:
            for xy, key in ((a, fields.node_from), (b, fields.node_to)):
                nid = str(p.get(key) or "").strip()
                if nid:
                    label_points.append((xy, nid))

    diag = {"n_mains_in": len(features), "n_no_geom": n_no_geom}
    if label_points:
        diag["label_points"] = label_points
    return pipes, diag
