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
(:func:`practice_provenance`), the frontend's one-line hint (:func:`practice_note`), and
the ``follow_municipal_practice`` build-option stub the pipeline records. Parameter
generation that actually FOLLOWS a registered practice arrives in later tickets.
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


#: registry city key -> its stated practice. DELIBERATELY EMPTY in this version: this
#: module is the mechanism; each city's row lands in its own ticket with its own sources
#: (n=1 warning — the first row must not quietly become "what municipalities do").
MUNICIPAL_PRACTICE: Dict[str, MunicipalPractice] = {}


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


def practice_provenance(city_key: Optional[str], *, follow: bool = False) -> dict:
    """The per-build ASSUMPTIONS block: which practice items were on record for this
    build, and whether "follow municipal practice" was selected.

    Always answers — absence is recorded explicitly, because a build record that stays
    silent is indistinguishable from one that never looked. While the option is a wiring
    stub, selecting it says so: an ASSUMPTIONS block that let a no-op read as applied
    would repeat the warning-severity blindspot.
    """
    practice = municipal_practice(city_key)
    items = practice_items(practice) if practice is not None else []
    if not items:
        note = "no municipal practice on record; fleet defaults used"
    elif follow:
        note = ("follow municipal practice selected, but parameter consumption is "
                "not implemented yet — fleet defaults used; the record is listed for "
                "traceability")
    else:
        note = ("municipal practice on record is informational; this build used the "
                "fleet defaults")
    return {"recorded": bool(items), "items": items,
            "follow_municipal_practice": follow, "note": note}
