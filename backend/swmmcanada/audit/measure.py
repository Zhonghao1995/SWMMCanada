"""Level judgement from measurements (ADR 0030) — the machine half of `granularity`.

Nobody may hand-write how fine a published polygon layer is. Victoria's layer is *named*
"Sewer SubCatchment Areas" and is 57 polygons at a 30.7 ha median — a pump-station basin.
The name says Level 1; the count says Level 2. Counts decide.

    anchor_ratio = n_polygons / n_anchors        (both inside the same coverage)
        storm/combined anchor = catch basins or inlets   (areas are drawn per inlet)
        sanitary anchor       = pipe segments            ("the drainage area for each
                                                          individual length of sewer")

Calibration points measured 2026-08-12: Victoria storm 64/7,864 = 0.008 · Victoria sanitary
57/4,638 = 0.012 · Hamilton combined ~8,147 per-segment ≈ 1. Two orders of magnitude apart,
which is what makes the thresholds separable at all — and why they are provisional until
the fleet distribution is in (ADR 0030 consequences).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence

from swmmcanada.sources.cities.capability import (ANCHOR_ROLES,
                                                  PROXY_ANCHOR_ROLES, Role)

#: Provisional, calibrated on three points. Must be revisited against the fleet histogram
#: before anything depends on the exact numbers (the ADR 0010 precedent: seven cities first,
#: then pick the gate).
RATIO_LEVEL_2 = 0.05
RATIO_LEVEL_1_CANDIDATE = 0.5

#: Agreement a candidate must reach for its polygons to be adopted as model units. Set high
#: on purpose: at Level 1 the city's polygons ARE the units, so their declared outlet should
#: almost always be the one we resolve. Like the ratio threshold above it has **no positive
#: example** — no source has ever reached candidacy — so it is a stated intent, not a
#: calibrated number, and the first real candidate should re-derive it.
AGREEMENT_TO_CONFIRM = 0.95

#: Below this many comparable units the rate is not evidence either way. Measured on the
#: Victoria fixture the method returned 100% over a single unit; a threshold that accepted
#: it would promote a source on one comparison.
MIN_AGREEMENT_SAMPLE = 30


class Level(Enum):
    LEVEL_1 = "level_1"                      # confirmed authoritative — runtime-granted only
    LEVEL_1_CANDIDATE = "level_1_candidate"  # passed the static gates, awaiting agreement
    LEVEL_2 = "level_2"                      # macro basin: hard boundary + validation only
    LEVEL_2_REVIEW = "level_2_review_required"
    NONE = "none"                            # no official polygon layer at all


@dataclass
class LevelVerdict:
    """A Level is never just a label. ADR 0029 Q11: every choice or demotion must carry the
    reason, the gate results and the evidence values that produced it."""

    level: Level
    reason: str
    anchor_ratio: Optional[float] = None
    n_polygons: Optional[int] = None
    n_anchors: Optional[int] = None
    anchor_role: Optional[str] = None
    gates: Dict[str, bool] = field(default_factory=dict)
    evidence: Dict[str, object] = field(default_factory=dict)

    def as_dict(self) -> Dict:
        return {"level": self.level.value, "reason": self.reason,
                "anchor_ratio": self.anchor_ratio, "n_polygons": self.n_polygons,
                "n_anchors": self.n_anchors, "anchor_role": self.anchor_role,
                "gates": dict(self.gates), "evidence": dict(self.evidence)}


def count_role(rows: Sequence[dict], system: str, roles: Sequence[Role]) -> tuple:
    """(total features, contributing layer names) for the given roles within one system.

    Layers whose system is unknown are **not** borrowed from a neighbouring system: an
    unattributed count would silently move a ratio across a threshold. They surface as
    unclassified instead, which is a request for a human, not a measurement.
    """
    wanted = {r.value for r in roles}
    total, used = 0, []
    for r in rows:
        if r.get("role") in wanted and r.get("system") == system and r.get("n_features"):
            total += int(r["n_features"])
            used.append(r["layer_name"])
    return total, used


def anchor_count(rows: Sequence[dict], system: str) -> tuple:
    """Anchors for a system, preferring the more specific role when a city publishes both
    catch basins and inlets — counting both would double the denominator."""
    for role in ANCHOR_ROLES.get(system, ()):
        total, used = count_role(rows, system, [role])
        if total:
            return total, role, used
    return 0, None, []


def judge(rows: Sequence[dict], system: str, *, polygon_role: Role = Role.SUBCATCHMENT,
          gates: Optional[Dict[str, bool]] = None) -> LevelVerdict:
    """Static Level verdict for one (city, system). Runtime promotion to LEVEL_1 happens
    later, once official-outlet agreement can be computed (ADR 0030: Level 1 is two beats,
    and is revocable)."""
    n_poly, poly_layers = count_role(rows, system, [polygon_role])
    if not n_poly:
        n_poly, poly_layers = count_role(rows, system, [Role.CATCHMENT])
        if not n_poly:
            return LevelVerdict(Level.NONE, "no official catchment or subcatchment layer",
                                evidence={"searched_system": system})
        polygon_role = Role.CATCHMENT

    n_anchor, anchor_role, anchor_layers = anchor_count(rows, system)
    evidence = {"polygon_layers": poly_layers, "anchor_layers": anchor_layers,
                "polygon_role": polygon_role.value}
    if anchor_role in PROXY_ANCHOR_ROLES:
        evidence["anchor_is_proxy"] = (
            f"{anchor_role.value} stands in for the true anchor; the city publishes no "
            f"{'/'.join(r.value for r in ANCHOR_ROLES[system] if r not in PROXY_ANCHOR_ROLES)}")
    if not n_anchor:
        # A polygon layer with nothing to measure it against cannot be graded. Treating it
        # as fine would be the Victoria mistake with no counter-evidence at all.
        return LevelVerdict(Level.LEVEL_2_REVIEW,
                            f"{n_poly} polygons but no {system} anchor layer to scale against",
                            n_polygons=n_poly, evidence=evidence)

    ratio = n_poly / n_anchor
    # Publish the ratio against every other anchor the city offers. A verdict that hinges on
    # an anchor choice must show what the other choice would have said — otherwise the
    # choice is invisible and unarguable.
    alternates = {}
    for role in ANCHOR_ROLES.get(system, ()):
        if role is anchor_role:
            continue
        alt, _ = count_role(rows, system, [role])
        if alt:
            alternates[role.value] = round(n_poly / alt, 5)
    if alternates:
        evidence["anchor_ratio_alternates"] = alternates

    base = dict(anchor_ratio=round(ratio, 5), n_polygons=n_poly, n_anchors=n_anchor,
                anchor_role=anchor_role.value if anchor_role else None,
                gates=dict(gates or {}), evidence=evidence)

    if ratio < RATIO_LEVEL_2:
        return LevelVerdict(Level.LEVEL_2,
                            f"ratio {ratio:.4f} < {RATIO_LEVEL_2}: macro basin, "
                            f"usable as hard boundary and validation reference", **base)
    if ratio < RATIO_LEVEL_1_CANDIDATE:
        return LevelVerdict(Level.LEVEL_2_REVIEW,
                            f"ratio {ratio:.4f} in [{RATIO_LEVEL_2}, "
                            f"{RATIO_LEVEL_1_CANDIDATE}): too coarse to trust, "
                            f"too fine to dismiss — human review", **base)

    failed = [k for k, ok in (gates or {}).items() if not ok]
    if failed:
        return LevelVerdict(Level.LEVEL_2_REVIEW,
                            f"ratio {ratio:.4f} qualifies but gate(s) failed: "
                            f"{', '.join(failed)}", **base)
    return LevelVerdict(Level.LEVEL_1_CANDIDATE,
                        f"ratio {ratio:.4f} >= {RATIO_LEVEL_1_CANDIDATE} and static gates "
                        f"pass; awaiting official-outlet agreement to confirm", **base)


def confirm_level_1(verdict: LevelVerdict, *, agreement_rate: Optional[float],
                    n_comparable: int) -> LevelVerdict:
    """The second beat of the Level 1 decision (ADR 0030).

    Phase 0 can only nominate: it measures how fine a layer is, not whether the outlets it
    declares are the ones we resolve. That needs a build. This applies the result — and the
    same evidence can take candidacy away, which is why Level 1 is described as revocable
    rather than earned once.

    Agreement is not an alternative route in: a layer that is too coarse to be model units
    stays too coarse however well its outlets match. Victoria scores 80.3% and is Level 2.
    """
    from dataclasses import replace as _replace

    if verdict.level is not Level.LEVEL_1_CANDIDATE:
        return verdict

    evidence = {**verdict.evidence, "outlet_agreement": agreement_rate,
                "outlet_agreement_n": n_comparable}

    if agreement_rate is None or n_comparable <= 0:
        return _replace(verdict, evidence=evidence,
                        reason=verdict.reason + "; no outlet agreement measured yet")
    if n_comparable < MIN_AGREEMENT_SAMPLE:
        return _replace(
            verdict, level=Level.LEVEL_2_REVIEW, evidence=evidence,
            gates={**verdict.gates, "outlet_agreement": False},
            reason=(f"agreement {agreement_rate:.2f} but sample of {n_comparable} is below "
                    f"{MIN_AGREEMENT_SAMPLE}: not evidence either way"))
    if agreement_rate < AGREEMENT_TO_CONFIRM:
        return _replace(
            verdict, level=Level.LEVEL_2, evidence=evidence,
            gates={**verdict.gates, "outlet_agreement": False},
            reason=(f"outlet agreement {agreement_rate:.2f} over {n_comparable} units is "
                    f"below {AGREEMENT_TO_CONFIRM}: the polygons stay a boundary and a "
                    f"reference, not model units"))
    return _replace(
        verdict, level=Level.LEVEL_1, evidence=evidence,
        gates={**verdict.gates, "outlet_agreement": True},
        reason=(f"outlet agreement {agreement_rate:.2f} over {n_comparable} units meets "
                f"{AGREEMENT_TO_CONFIRM}: confirmed authoritative"))
