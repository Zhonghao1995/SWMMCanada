"""Municipal-practice registry (spec §G2): a machine-readable slot, beside DATA_TIERS,
for the modelling conventions a municipality tells us — every item value + source + date.

Two behaviours matter more than the table itself:

  * an unregistered city answers "no practice record" (None), never a default dressed up
    as one — a fabricated record would poison every consumer downstream;
  * registration changes NOTHING by itself. The anti-extrapolation guardrails below pin
    the fleet defaults (Horton, the 2.5 m fallback depth, the halved-Ksat GA table, the
    dry-antecedent IMD) and the default build's byte-level output against the table's
    existence and contents.

All example entries here are synthetic — neutral fake values and sources, no real city's
numbers (those arrive with their own evidence in their own ticket).
"""
import dataclasses
from datetime import date, datetime

from swmmcanada.sources.cities.practice import (
    MUNICIPAL_PRACTICE,
    MunicipalPractice,
    PracticeItem,
    municipal_practice,
    practice_items,
    practice_note,
    practice_provenance,
)
from swmmcanada.sources.cities.registry import CITIES

FAKE_SOURCE = "example municipal correspondence, 2026-01"


def _synthetic_entry() -> MunicipalPractice:
    """A fully-populated record with NEUTRAL fake values (deliberately unlike any real
    city's numbers) — exercises every field without smuggling data into the mechanism."""
    def item(value):
        return PracticeItem(value=value, source=FAKE_SOURCE, date="2026-01")

    return MunicipalPractice(
        modelling_platform=item("Example Hydraulic Suite"),
        infiltration_method=item("GREEN_AMPT"),
        ga_ksat_halved=item(False),
        ga_imd_antecedent=item("field_capacity"),
        surface_parameters=item({"n_imperv": 0.015, "n_perv": 0.30,
                                 "s_imperv_mm": 1.5, "s_perv_mm": 3.0, "pct_zero": 10.0}),
        design_imperviousness=item({"residential": 60.0, "commercial": 85.0,
                                    "open_space": 10.0}),
        dwf_pattern_structure=item(["hourly", "weekend", "monthly"]),
    )


class TestTableShape:
    def test_the_field_set_is_the_spec_g2_first_version(self):
        """The registry's vocabulary IS the contract consumers code against: platform,
        infiltration method, the two GA conventions, the surface parameter set, the
        design imperviousness table, and the DWF pattern structure."""
        assert {f.name for f in dataclasses.fields(MunicipalPractice)} == {
            "modelling_platform", "infiltration_method",
            "ga_ksat_halved", "ga_imd_antecedent",
            "surface_parameters", "design_imperviousness", "dwf_pattern_structure",
        }

    def test_every_registered_city_is_a_registry_key(self):
        """Practice is optional per city (unlike DATA_TIERS, which is exhaustive), but a
        key that matches no registry city is a record nothing can ever consume."""
        keys = {s.key for s in CITIES}
        strays = set(MUNICIPAL_PRACTICE) - keys
        assert not strays, f"practice registered for unknown cities: {sorted(strays)}"

    def test_every_registered_item_carries_a_nonempty_source_and_date(self):
        """Value + source + date is the whole point: a convention without provenance is a
        rumour. Vacuously green while the table is empty; load-bearing the day a city
        lands."""
        for key, entry in MUNICIPAL_PRACTICE.items():
            for item in practice_items(entry):
                assert str(item["source"]).strip(), (key, item["field"])
                assert str(item["date"]).strip(), (key, item["field"])


class TestUnregisteredCitiesSayNoRecord:
    def test_an_unregistered_registry_city_returns_none(self):
        """None, not a default: fabricating "typical practice" for a city that told us
        nothing is exactly the failure this accessor exists to prevent."""
        for spec in CITIES:
            if spec.key not in MUNICIPAL_PRACTICE:
                assert municipal_practice(spec.key) is None, spec.key

    def test_an_unknown_key_returns_none(self):
        assert municipal_practice("atlantis") is None

    def test_none_city_returns_none(self):
        """The synthesis pathway has no city at all; same explicit absence."""
        assert municipal_practice(None) is None


class TestPracticeItems:
    def test_items_enumerate_exactly_the_registered_fields(self):
        entry = _synthetic_entry()
        items = practice_items(entry)
        assert {i["field"] for i in items} == {
            f.name for f in dataclasses.fields(MunicipalPractice)}
        by_field = {i["field"]: i for i in items}
        assert by_field["infiltration_method"]["value"] == "GREEN_AMPT"
        assert by_field["ga_ksat_halved"]["value"] is False
        assert all(i["source"] == FAKE_SOURCE and i["date"] == "2026-01" for i in items)

    def test_unset_fields_are_not_listed(self):
        entry = MunicipalPractice(
            infiltration_method=PracticeItem("GREEN_AMPT", FAKE_SOURCE, "2026-01"))
        assert [i["field"] for i in practice_items(entry)] == ["infiltration_method"]


class TestFrontendNote:
    def test_no_record_means_no_line(self):
        assert practice_note("atlantis") is None
        for spec in CITIES:
            if spec.key not in MUNICIPAL_PRACTICE:
                assert practice_note(spec.key) is None, spec.key

    def test_a_registered_city_gets_one_plain_line(self, monkeypatch):
        monkeypatch.setitem(MUNICIPAL_PRACTICE, "synthville", _synthetic_entry())
        note = practice_note("synthville")
        assert isinstance(note, str) and note
        assert "\n" not in note
        # The headline convention a modeller needs to know before switching methods —
        # and the honest counterpart: nothing about the build changed.
        assert "Green-Ampt" in note
        assert "default" in note.lower()

    def test_a_record_without_the_headline_item_still_gets_a_line(self, monkeypatch):
        entry = MunicipalPractice(
            modelling_platform=PracticeItem("Example Hydraulic Suite", FAKE_SOURCE, "2026-01"))
        monkeypatch.setitem(MUNICIPAL_PRACTICE, "synthville", entry)
        note = practice_note("synthville")
        assert isinstance(note, str) and "default" in note.lower()


class TestBuildAssumptionsBlock:
    def test_no_record_is_recorded_explicitly(self):
        block = practice_provenance("atlantis", follow=False)
        assert block["recorded"] is False
        assert block["items"] == []
        assert block["follow_municipal_practice"] is False
        assert "no municipal practice on record" in block["note"]

    def test_the_items_list_is_the_practice_inventory(self, monkeypatch):
        monkeypatch.setitem(MUNICIPAL_PRACTICE, "synthville", _synthetic_entry())
        block = practice_provenance("synthville", follow=False)
        assert block["recorded"] is True
        assert {i["field"] for i in block["items"]} >= {"infiltration_method",
                                                        "surface_parameters"}
        assert all(i["source"] == FAKE_SOURCE for i in block["items"])
        assert "default" in block["note"].lower()

    def test_selecting_follow_is_recorded_and_admits_it_did_nothing_yet(self, monkeypatch):
        """The option is a wiring stub: parameter consumption lands in later tickets. An
        ASSUMPTIONS block that let a no-op look like it worked would repeat the
        warning-severity blindspot."""
        monkeypatch.setitem(MUNICIPAL_PRACTICE, "synthville", _synthetic_entry())
        block = practice_provenance("synthville", follow=True)
        assert block["follow_municipal_practice"] is True
        assert "not implemented" in block["note"]

    def test_the_synthesis_pathway_records_the_absence(self):
        block = practice_provenance(None, follow=False)
        assert block["recorded"] is False and block["items"] == []


class TestBuildCityWiresTheBlockIntoProvenance:
    """The block must reach the citable artifact, not just exist as a function. The build
    spine is stubbed out and captured — everything upstream of it runs for real, offline,
    on a synthetic city spec."""

    def _spec(self):
        from swmmcanada.build.models import ConduitIn, JunctionIn, NetworkIn, OutfallIn
        from swmmcanada.sources.cities import base
        from swmmcanada.sources.cities.registry import CitySpec

        net = NetworkIn(
            junctions=[JunctionIn("J1", 9.0, -123.3675, 48.4225)],
            outfalls=[OutfallIn("OUT", 6.0, -123.3660, 48.4235)],
            conduits=[ConduitIn("C1", "J1", "OUT", 120.0)])
        return CitySpec(
            key="synthville", label="Synthville, XX",
            coverage=(-123.37, 48.42, -123.36, 48.43), sub_crs="EPSG:32610",
            network_source="synthetic fixture",
            storm=lambda bbox, client: base.NetworkResult(network=net),
            land=lambda bbox, client: {})

    def _aoi(self):
        from swmmcanada.geo import aoi_from_geojson

        return aoi_from_geojson({"type": "Polygon", "coordinates": [[
            [-123.370, 48.420], [-123.365, 48.420], [-123.365, 48.425],
            [-123.370, 48.425], [-123.370, 48.420]]]})

    def test_the_practice_block_reaches_extra_provenance(self, monkeypatch, tmp_path):
        from swmmcanada import pipeline

        captured = {}

        def fake_finish(ws, aoi, network, subcatchments, **kw):
            captured.update(kw["extra_provenance"])
            return "BUILT"

        monkeypatch.setattr(pipeline, "_finish_build", fake_finish)
        monkeypatch.setattr(pipeline, "fetch_street_graph",
                            lambda bbox: (_ for _ in ()).throw(RuntimeError("offline")))
        monkeypatch.setitem(MUNICIPAL_PRACTICE, "synthville", _synthetic_entry())

        result = pipeline.build_city(
            self._spec(), self._aoi(), date(2020, 6, 1), date(2020, 6, 2), tmp_path,
            derive=False, subcatchment_method="junction", follow_municipal_practice=True)

        assert result == "BUILT"
        block = captured["municipal_practice"]
        assert block["recorded"] is True
        assert block["follow_municipal_practice"] is True
        assert any(i["field"] == "infiltration_method" for i in block["items"])

    def test_default_is_not_following(self, monkeypatch, tmp_path):
        from swmmcanada import pipeline

        captured = {}

        def fake_finish(ws, aoi, network, subcatchments, **kw):
            captured.update(kw["extra_provenance"])
            return "BUILT"

        monkeypatch.setattr(pipeline, "_finish_build", fake_finish)
        monkeypatch.setattr(pipeline, "fetch_street_graph",
                            lambda bbox: (_ for _ in ()).throw(RuntimeError("offline")))

        pipeline.build_city(
            self._spec(), self._aoi(), date(2020, 6, 1), date(2020, 6, 2), tmp_path,
            derive=False, subcatchment_method="junction")

        block = captured["municipal_practice"]
        assert block["follow_municipal_practice"] is False
        assert block["recorded"] is False   # synthville not registered in this test


class TestNoExtrapolationGuardrails:
    """The registry is information and options — it moves NO fleet default. These pin the
    defaults a future "one city's evidence" change would be tempted to move; each may only
    change through its own ADR with its own evidence."""

    def test_default_infiltration_is_still_horton(self):
        from swmmcanada.build.config import BuildConfig, InfiltrationModel

        assert dataclasses.fields(BuildConfig)
        assert BuildConfig(out_dir=".", start=date(2020, 1, 1), end=date(2020, 1, 2)
                           ).infiltration is InfiltrationModel.HORTON

    def test_fleet_fallback_manhole_depth_is_still_2_5_m(self):
        from swmmcanada.sources.cities.base import AssembleConfig

        assert AssembleConfig().fallback_node_depth_m == 2.5

    def test_green_ampt_table_is_the_halved_ksat_rawls_table_verbatim(self):
        """Exact snapshot of GA_BY_TEXTURE (Rawls 1983, Ksat halved per the paper's own
        guidance; IMD = effective porosity, the dry-antecedent maximum deficit). A city
        whose practice differs registers that fact — it does not edit this table."""
        from swmmcanada.derive.infiltration import GA_BY_TEXTURE

        assert GA_BY_TEXTURE == {
            "sand": (49.5, 117.8, 0.417),
            "loamy sand": (61.3, 29.9, 0.401),
            "sandy loam": (110.1, 10.9, 0.412),
            "loam": (88.9, 6.6, 0.434),
            "silt loam": (166.8, 3.4, 0.486),
            "silt": (166.8, 3.4, 0.486),
            "sandy clay loam": (218.5, 1.5, 0.330),
            "clay loam": (208.8, 1.0, 0.390),
            "silty clay loam": (273.0, 1.0, 0.432),
            "sandy clay": (239.0, 0.6, 0.321),
            "silty clay": (292.2, 0.5, 0.423),
            "clay": (316.3, 0.3, 0.385),
        }

    def test_ga_imd_is_still_the_tables_dry_antecedent_deficit(self):
        """The derive path hands out the table's IMD (θe) untransformed — no
        field-capacity subtraction sneaks in as a side effect of the registry."""
        from swmmcanada.derive.infiltration import GA_BY_TEXTURE, green_ampt_for_texture

        for texture, row in GA_BY_TEXTURE.items():
            assert green_ampt_for_texture(texture)[2] == row[2], texture

    def test_default_build_inp_is_byte_identical_registered_or_not(self, monkeypatch, tmp_path):
        """A default build (follow not selected) must not change because the table exists
        or holds entries — asserted at the byte level, the same standard the deterministic
        package work set."""
        from swmmcanada.build import (
            BuildConfig, ConduitIn, JunctionIn, NetworkIn, OutfallIn, RainfallSeries,
            SurfaceCatchment, build_model,
        )

        def _build(out_dir):
            network = NetworkIn(
                junctions=[JunctionIn("J1", 99.0, -104.62, 50.44),
                           JunctionIn("J2", 98.5, -104.615, 50.44)],
                outfalls=[OutfallIn("O1", 98.0, -104.61, 50.44)],
                conduits=[ConduitIn("C1", "J1", "J2", 100.0),
                          ConduitIn("C2", "J2", "O1", 100.0)])
            subs = [SurfaceCatchment("S1", "J1", 1.0, 40.0, 100.0, 1.0)]
            rain = RainfallSeries([datetime(2020, 6, 1, h) for h in range(3)],
                                  [1.0, 2.0, 0.0])
            cfg = BuildConfig(out_dir=out_dir, start=date(2020, 6, 1), end=date(2020, 6, 2))
            res = build_model(network=network, subcatchments=subs, rain=rain, config=cfg)
            return res.inp_path.read_bytes()

        before = _build(tmp_path / "clean")
        monkeypatch.setitem(MUNICIPAL_PRACTICE, "victoria", _synthetic_entry())
        after = _build(tmp_path / "registered")
        assert before == after

    def test_the_live_table_ships_empty_until_a_city_brings_evidence(self):
        """This ticket builds the mechanism only. City rows land in their own tickets with
        their own sources; delete this test in the ticket that adds the first row."""
        assert MUNICIPAL_PRACTICE == {}
