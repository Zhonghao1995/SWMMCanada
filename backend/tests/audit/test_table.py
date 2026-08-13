"""Capability table assembly (ADR 0030), especially coverage scoping."""
import json

from swmmcanada.audit.table import build, scope_rows


class FakeCatalogue:
    """Counts anchors 'within an extent' by a fixed factor, so scoping is observable
    without touching the network."""

    def __init__(self, factor=0.4):
        self.factor = factor
        self.calls = []

    def count_within(self, service, layer_id, extent):
        self.calls.append((service, layer_id, extent))
        return int(self._city_wide * self.factor)

    _city_wide = 0


EXTENT = {"xmin": 0, "ymin": 0, "xmax": 100, "ymax": 100,
          "spatialReference": {"wkid": 26917}}


def rows_for(city, system, n_poly, n_anchor, anchor_role="gravity_main"):
    return [
        {"city": city, "role": "subcatchment", "system": system, "n_features": n_poly,
         "layer_name": "Areas", "service_name": "S", "service": "svc", "layer_id": 1,
         "extent": EXTENT},
        {"city": city, "role": anchor_role, "system": system, "n_features": n_anchor,
         "layer_name": "Mains", "service_name": "S", "service": "svc", "layer_id": 2,
         "extent": EXTENT},
    ]


class TestCoverageScoping:
    def test_anchor_counts_are_restricted_to_the_area_layer_extent(self):
        """ADR 0030 makes this correctness, not refinement: Hamilton's combined catchments
        cover the old combined district only, and a city-wide denominator dilutes a
        per-segment layer by 2.3x."""
        rows = rows_for("x", "sanitary", 1000, 10000)
        cat = FakeCatalogue(factor=0.4)
        cat._city_wide = 10000
        scoped = scope_rows(cat, rows, "sanitary")
        anchor = next(r for r in scoped if r["role"] == "gravity_main")
        assert anchor["n_features"] == 4000
        assert anchor["n_features_city_wide"] == 10000
        assert anchor["coverage_scoped"] is True

    def test_area_layer_counts_are_untouched(self):
        rows = rows_for("x", "sanitary", 1000, 10000)
        cat = FakeCatalogue()
        cat._city_wide = 10000
        area = next(r for r in scope_rows(cat, rows, "sanitary")
                    if r["role"] == "subcatchment")
        assert area["n_features"] == 1000 and "n_features_city_wide" not in area

    def test_missing_extent_degrades_loudly_not_silently(self):
        """A denominator that could not be bounded must not pass as if it had been."""
        rows = rows_for("x", "sanitary", 1000, 10000)
        for r in rows:
            r["extent"] = None
        scoped = scope_rows(FakeCatalogue(), rows, "sanitary")
        assert all(not r.get("coverage_scoped") for r in scoped)

    def test_no_catalogue_means_no_scoping(self):
        rows = rows_for("x", "sanitary", 1000, 10000)
        assert scope_rows(None, rows, "sanitary") == list(rows)


class TestBuild:
    def test_systems_without_area_layers_are_omitted(self):
        table = build(rows_for("x", "sanitary", 100, 10000), cat=None)
        systems = {c["system"] for c in table["capabilities"]}
        assert systems == {"sanitary"}

    def test_every_capability_row_carries_reason_and_evidence(self):
        table = build(rows_for("x", "sanitary", 100, 10000), cat=None)
        cap = table["capabilities"][0]
        assert cap["reason"] and cap["evidence"]["polygon_layers"]

    def test_table_is_stamped_and_counts_unclassified(self):
        rows = rows_for("x", "sanitary", 100, 10000)
        rows.append({"city": "x", "role": None, "system": None, "n_features": None,
                     "layer_name": "Mystery", "service_name": "S", "service": "svc",
                     "layer_id": 9, "skip_reason": "unclassified: no role suggestion"})
        table = build(rows, cat=None)
        assert table["generated_at"] and table["unclassified_layers"] == 1

    def test_external_reference_cities_are_marked(self):
        rows = rows_for("hamilton", "combined", 8147, 9000)
        for r in rows:
            r["external_reference"] = True
        table = build(rows, cat=None)
        assert all(c["external_reference"] for c in table["capabilities"])


def test_real_table_has_no_level_1_and_is_wellformed():
    """Pins the Phase 0 headline. If a future scan finds an authoritative layer this fails,
    which is exactly when the official-source branch becomes worth building."""
    from pathlib import Path
    p = Path(__file__).resolve().parents[3] / "docs/reports/capability/table.json"
    if not p.exists():
        return  # generated artefact; absent on a fresh clone (docs/ is local-only)
    t = json.loads(p.read_text())
    assert t["capabilities"], "table must not be empty"
    for c in t["capabilities"]:
        assert c["reason"], f"{c['city']}/{c['system']} has no reason"
        assert c["level"] != "level_1", "level_1 is runtime-granted only, never static"
