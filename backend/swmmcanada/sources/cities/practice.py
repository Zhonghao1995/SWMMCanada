"""Municipal-practice registry (spec §G2) — the modelling conventions a municipality has
told us, machine-readable, beside ``registry.DATA_TIERS``.

A city's engineering team sometimes states its own modelling conventions: which platform
its reference models live in, which infiltration method, how the Green-Ampt parameters
are conventionally taken, its surface parameter set, its design imperviousness table,
the time structure of its dry-weather-flow patterns. Until now that knowledge had no
fixed slot — it lived in documents and memory. This table is the slot: every item is a
value + a free-text source + a date, so each convention is traceable, can expire, and
can be replaced by better evidence.

Three rules keep it honest:

  * **Optional per city.** Most cities have told us nothing; they simply have no entry.
    ``municipal_practice`` answers ``None`` for them — never a default dressed up as a
    record, because a fabricated "typical practice" would poison every consumer.
  * **Information and options only — no default moves.** Registration changes nothing by
    itself: the fleet defaults (Horton, the shared parameter tables, the 2.5 m fallback
    depth) stay exactly where they are. Whether a REGISTERED city should switch its own
    defaults is a separate decision for a separate ADR. Guardrail tests pin this.
  * **Source wording.** Sources are free text; public entries use the repo's aggregate
    provenance wording (no model files, no per-asset numbers). Test fixtures use neutral
    fake sources, e.g. ``"example municipal correspondence, 2026-01"``.

Consumers (this version): the build's provenance/ASSUMPTIONS block
(:func:`practice_provenance`), the frontend's one-line hint (:func:`practice_note`), the
pipeline's ``follow_municipal_practice`` option, which consumes the registered method /
GA-convention / surface-parameter / DWF-pattern-structure items through
:func:`practice_build_overrides`, and validation's imperviousness cross-check, which
reads the design-imperviousness table as a comparison yardstick only. The design table
has no BUILD consumer (no land-use classifier yet) and stays information-only in the
per-build block — the cross-check measures, it never applies.
"""
from dataclasses import dataclass, fields
from typing import Any, Dict, List, Optional

#: Human spellings for the note line, keyed by the machine values the build already uses
#: (``InfiltrationModel``); an unknown spelling passes through as-is — the registry does
#: not police what a municipality calls its method.
_METHOD_LABELS = {"HORTON": "Horton", "GREEN_AMPT": "Green-Ampt",
                  "CURVE_NUMBER": "Curve Number"}


@dataclass(frozen=True)
class PracticeItem:
    """One stated convention: the value, where it came from, and when."""

    value: Any
    source: str     # free text, e.g. "example municipal correspondence, 2026-01"
    date: str       # when stated/confirmed, e.g. "2026-01"


@dataclass(frozen=True)
class MunicipalPractice:
    """A city's stated modelling conventions (spec §G2 first-version field set).

    Every field is optional — a city states what it states, nothing is inferred to fill
    the rest.
    """

    #: The platform the city's own reference models live in (e.g. an ICM/MIKE+ product).
    modelling_platform: Optional[PracticeItem] = None
    #: The infiltration method the city models with; values follow
    #: ``build.config.InfiltrationModel`` spellings where they match.
    infiltration_method: Optional[PracticeItem] = None
    #: Green-Ampt convention: whether the city halves the literature Ksat (bool value).
    ga_ksat_halved: Optional[PracticeItem] = None
    #: Green-Ampt convention: how the antecedent moisture behind IMD is taken
    #: (e.g. "dry", "field_capacity").
    ga_imd_antecedent: Optional[PracticeItem] = None
    #: Surface parameter set (dict value with the SurfaceCatchment field names:
    #: n_imperv / n_perv / s_imperv_mm / s_perv_mm / pct_zero).
    surface_parameters: Optional[PracticeItem] = None
    #: Design imperviousness table (dict value: land-use category -> percent).
    design_imperviousness: Optional[PracticeItem] = None
    #: Time structure of the city's DWF patterns (e.g. ["hourly", "weekend", "monthly"]).
    dwf_pattern_structure: Optional[PracticeItem] = None


#: Aggregate provenance wording (the spec's confidentiality boundary) for entries
#: distilled from municipal correspondence and reference-model statistics: public code
#: carries the aggregate convention and this neutral wording only — no material names,
#: no per-asset numbers. The precise mapping lives in the local analysis documents.
AGGREGATE_RECORDS_SOURCE = "municipal engineering records, 2026-08"


def _aggregate_item(value) -> PracticeItem:
    return PracticeItem(value=value, source=AGGREGATE_RECORDS_SOURCE, date="2026-08")


#: registry city key -> its stated practice. Rows land one city at a time, each with its
#: own evidence — n=1 warning: no row is "what municipalities do", it is what THIS
#: municipality stated, and a second city's row may contradict it.
MUNICIPAL_PRACTICE: Dict[str, MunicipalPractice] = {
    # Vancouver (spec §V4): the registry's first real row. All values are aggregate
    # conventions the city's engineering team stated for its own models, 2026-08.
    "vancouver": MunicipalPractice(
        modelling_platform=_aggregate_item("InfoWorks ICM"),
        infiltration_method=_aggregate_item("GREEN_AMPT"),
        # The city applies the literature Ksat UNHALVED (the fleet table halves it per
        # Rawls' own guidance) and takes the initial deficit from soil drained to field
        # capacity rather than the dry-antecedent maximum.
        ga_ksat_halved=_aggregate_item(False),
        ga_imd_antecedent=_aggregate_item("field_capacity"),
        surface_parameters=_aggregate_item({
            "n_imperv": 0.018, "n_perv": 0.41,
            "s_imperv_mm": 1.25, "s_perv_mm": 2.5, "pct_zero": 0.0}),
        # Design (planning-table) imperviousness by land-use category, percent. No build
        # consumer (no land-use classifier) — validation's imperviousness cross-check
        # reads it as a yardstick; the future design-table mode would be the applier.
        design_imperviousness=_aggregate_item({
            "single_family": 55.0, "townhouse_multiplex": 70.0, "arterial_row": 80.0,
            "commercial_high": 90.0, "park_green": 5.0}),
        # Population-based DWF patterns structured monthly + hourly + weekend.
        dwf_pattern_structure=_aggregate_item(["monthly", "hourly", "weekend"]),
    ),
}


def municipal_practice(city_key: Optional[str]) -> Optional[MunicipalPractice]:
    """The city's stated practice, or ``None`` — an explicit "no practice record".

    ``None`` is a first-class answer, not a lookup miss to paper over: an unregistered
    city (or ``city_key=None``, the synthesis pathway) has told us nothing, and nothing
    is what consumers must see.
    """
    if not city_key:
        return None
    return MUNICIPAL_PRACTICE.get(city_key)


def practice_items(practice: MunicipalPractice) -> List[dict]:
    """The stated items as ``{field, value, source, date}`` dicts, declaration order —
    the shape both the ASSUMPTIONS block and the API carry."""
    out: List[dict] = []
    for f in fields(MunicipalPractice):
        item: Optional[PracticeItem] = getattr(practice, f.name)
        if item is not None:
            out.append({"field": f.name, "value": item.value,
                        "source": item.source, "date": item.date})
    return out


def practice_note(city_key: Optional[str]) -> Optional[str]:
    """One plain-text line for the frontend, or ``None`` when there is nothing to say.

    Leads with the convention a modeller most needs before choosing a method (the city's
    own infiltration method, when stated) and always says the counterpart out loud: the
    build's defaults are unchanged.
    """
    practice = municipal_practice(city_key)
    if practice is None:
        return None
    items = practice_items(practice)
    if not items:
        return None
    if practice.infiltration_method is not None:
        raw = str(practice.infiltration_method.value)
        method = _METHOD_LABELS.get(raw, raw)
        head = f"Municipal practice on record: the city's own models use {method} infiltration."
    else:
        head = (f"Municipal practice on record for this city "
                f"({len(items)} stated convention{'s' if len(items) != 1 else ''}).")
    return head + " Build defaults are unchanged."


#: The stated fields a follow build actually consumes (this version): the infiltration
#: method switch, the two Green-Ampt conventions, the surface parameter set, and the
#: DWF pattern structure (ticket 10 — it configures the loading layer's pattern group).
#: The design-imperviousness table stays information-only for the BUILD (no land-use
#: classifier yet); validation reads it for the imperviousness cross-check, which
#: measures and never changes parameters. ONE tuple feeds both the application
#: (:func:`practice_build_overrides`) and the record (:func:`practice_provenance`),
#: so what a build does and what it says cannot drift.
_CONSUMED_FIELDS = ("infiltration_method", "ga_ksat_halved", "ga_imd_antecedent",
                    "surface_parameters", "dwf_pattern_structure")


def practice_build_overrides(practice: Optional[MunicipalPractice]) -> dict:
    """Concrete build inputs for a FOLLOWED practice — the single interpreter of the
    consumable fields (``_CONSUMED_FIELDS``).

    Returns ``infiltration`` / ``ga_antecedent`` (stated values to switch to, or None),
    ``ga_ksat_scale`` (multiplier on the derived Ksat), ``surface_parameters``
    (``SurfaceCatchment`` field overrides, or None) and ``dwf_pattern_structure`` (the
    loading layer's DWF pattern group, or None for the fleet default). Callers pass the
    practice only when following was selected; ``None`` (no record, or not following)
    overrides nothing.
    """
    out: dict = {"infiltration": None, "ga_antecedent": None, "ga_ksat_scale": 1.0,
                 "surface_parameters": None, "dwf_pattern_structure": None}
    if practice is None:
        return out
    if practice.infiltration_method is not None:
        out["infiltration"] = str(practice.infiltration_method.value)
    if practice.ga_imd_antecedent is not None:
        out["ga_antecedent"] = str(practice.ga_imd_antecedent.value)
    # Stated convention "Ksat not halved": GA_BY_TEXTURE stores the Rawls values HALVED
    # (the paper's own guidance — the fleet default), so following an unhalved
    # convention means x2 back to the source-table value. A build-time transform, never
    # an edit to the shared table (the guardrail tests pin the table verbatim).
    if practice.ga_ksat_halved is not None and practice.ga_ksat_halved.value is False:
        out["ga_ksat_scale"] = 2.0
    if practice.surface_parameters is not None:
        out["surface_parameters"] = dict(practice.surface_parameters.value)
    if practice.dwf_pattern_structure is not None:
        out["dwf_pattern_structure"] = list(practice.dwf_pattern_structure.value)
    return out


def practice_provenance(city_key: Optional[str], *, follow: bool = False) -> dict:
    """The per-build ASSUMPTIONS block: the practice items on record for this build,
    whether "follow municipal practice" was selected, and — when it was — which items
    the build consumed versus which are information-only (stated, but with no build
    consumer yet).

    Always answers — absence is recorded explicitly, because a build record that stays
    silent is indistinguishable from one that never looked; and consumed vs
    information-only stay two separate lists, because a block that let an unconsumed
    item read as applied would repeat the warning-severity blindspot.
    """
    practice = municipal_practice(city_key)
    items = practice_items(practice) if practice is not None else []
    stated = [i["field"] for i in items]
    consumed = [f for f in stated if f in _CONSUMED_FIELDS] if follow else []
    information_only = [f for f in stated if f not in consumed]
    if not items:
        note = "no municipal practice on record; fleet defaults used"
    elif not follow:
        note = ("municipal practice on record is informational; this build used the "
                "fleet defaults")
    elif consumed:
        note = ("follow municipal practice applied: the consumed items generated this "
                "build's parameters; information-only items have no build consumer yet "
                "and are listed for traceability")
    else:
        note = ("follow municipal practice selected, but none of the stated items has "
                "a build consumer; fleet defaults used")
    return {"recorded": bool(items), "items": items,
            "follow_municipal_practice": follow,
            "consumed": consumed, "information_only": information_only, "note": note}
