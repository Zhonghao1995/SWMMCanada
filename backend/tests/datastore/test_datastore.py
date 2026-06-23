"""TDD for the model-ready datastore (spec 11 / ADR 0003).

The datastore is the standardized intermediate layer between data-acquisition and
model-build: each source converts to ONE on-disk standard (GeoPackage for the network,
netCDF/CF for forcing, JSON for config+provenance), and the model builder reads from it.

These tests pin three guarantees:
  1. write_datastore lays down the three carrier files.
  2. write → read_datastore round-trips the exact input dataclasses (floats within
     float64 precision; None polygons and name ordering preserved).
  3. build_from_datastore proves the datastore is *sufficient* to build a SWMM model:
     the reconstructed inputs assemble into a runnable, re-parseable .inp whose
     node/link/subcatchment counts match the fixture.
"""
from datetime import date, datetime, timedelta

import pytest

from swmmcanada.build import (
    BuildConfig,
    ConduitIn,
    EvaporationSeries,
    FlowUnits,
    InfiltrationModel,
    JunctionIn,
    NetworkIn,
    OutfallIn,
    RainfallSeries,
    SubcatchmentIn,
    TemperatureSeries,
)
from swmmcanada.datastore import (
    ModelReadyDatastore,
    build_from_datastore,
    read_datastore,
    write_datastore,
)

# All coordinates are EPSG:4326 lon/lat (central Ottawa-ish), matching the rest of the repo.


def _network() -> NetworkIn:
    return NetworkIn(
        junctions=[
            JunctionIn("J1", invert_m=99.0, x=-75.700, y=45.410, max_depth_m=2.5),
            JunctionIn("J2", invert_m=98.5, x=-75.690, y=45.410, max_depth_m=2.0),
            JunctionIn("J3", invert_m=98.2, x=-75.685, y=45.415),
        ],
        outfalls=[OutfallIn("O1", invert_m=98.0, x=-75.680, y=45.410, kind="FREE")],
        conduits=[
            ConduitIn("C1", "J1", "J2", length_m=120.0, diameter_m=0.45, roughness_n=0.012),
            ConduitIn("C2", "J2", "J3", length_m=80.0),
            ConduitIn("C3", "J3", "O1", length_m=140.0, diameter_m=0.60),
        ],
    )


def _subcatchments():
    return [
        # WITH a polygon
        SubcatchmentIn(
            "S1", outlet_node="J1", area_ha=1.5, pct_imperv=42.0, width_m=120.0,
            pct_slope=1.2, cn=82.0, n_imperv=0.011, n_perv=0.12,
            s_imperv_mm=1.6, s_perv_mm=5.5, pct_zero=20.0,
            polygon=[(-75.701, 45.409), (-75.699, 45.409), (-75.699, 45.411), (-75.701, 45.411)],
        ),
        # WITHOUT a polygon (must round-trip back to polygon=None)
        SubcatchmentIn(
            "S2", outlet_node="J2", area_ha=0.8, pct_imperv=30.0, width_m=90.0,
            pct_slope=0.9, polygon=None,
        ),
        # another WITH a polygon (defaults otherwise)
        SubcatchmentIn(
            "S3", outlet_node="J3", area_ha=2.1, pct_imperv=55.0, width_m=150.0,
            pct_slope=1.8,
            polygon=[(-75.686, 45.414), (-75.684, 45.414), (-75.684, 45.416), (-75.686, 45.416)],
        ),
    ]


def _rain() -> RainfallSeries:
    ts = [datetime(2022, 6, 1, h) for h in range(6)]
    return RainfallSeries(
        timestamps=ts, precip_mm=[0.0, 1.2, 3.4, 5.6, 0.8, 0.0],
        gage_name="RG1", ts_name="rain",
    )


def _config(out_dir) -> BuildConfig:
    return BuildConfig(
        out_dir=out_dir,
        start=date(2022, 6, 1),
        end=date(2022, 6, 2),
        title="Datastore fixture model",
        flow_units=FlowUnits.CMS,
        infiltration=InfiltrationModel.CURVE_NUMBER,
        routing_model="DYNWAVE",
        rain_interval=timedelta(hours=1),
        rain_format="VOLUME",
    )


def _evaporation() -> EvaporationSeries:
    return EvaporationSeries(
        timestamps=[datetime(2022, 6, 1), datetime(2022, 6, 2)], evap_mm_day=[3.7, 3.9]
    )


def _temperature() -> TemperatureSeries:
    return TemperatureSeries(
        timestamps=[datetime(2022, 6, 1), datetime(2022, 6, 2)], tmean_c=[13.0, 14.5]
    )


def _provenance() -> dict:
    return {
        "aoi_bbox": [-75.70, 45.41, -75.68, 45.42],
        "crs": "EPSG:4326",
        "sources": {"streets": "OSM", "dem": "MRDEM"},
        "start": "2022-06-01",
        "end": "2022-06-02",
    }


# --------------------------------------------------------------------------- #
# 1. write_datastore lays down the three carrier files
# --------------------------------------------------------------------------- #
def test_write_datastore_creates_three_files(tmp_path):
    out = tmp_path / "ds"
    returned = write_datastore(
        out,
        network=_network(),
        subcatchments=_subcatchments(),
        rain=_rain(),
        config=_config(tmp_path / "build"),
        provenance=_provenance(),
    )
    assert returned == out
    assert (out / "network.gpkg").exists()
    assert (out / "forcing.nc").exists()
    assert (out / "datastore.json").exists()


def test_datastore_json_records_config_and_provenance(tmp_path):
    import json

    out = tmp_path / "ds"
    write_datastore(
        out, network=_network(), subcatchments=_subcatchments(), rain=_rain(),
        config=_config(tmp_path / "build"), provenance=_provenance(),
    )
    meta = json.loads((out / "datastore.json").read_text())
    assert "datastore_version" in meta
    cfg = meta["config"]
    assert cfg["title"] == "Datastore fixture model"
    assert cfg["start"] == "2022-06-01" and cfg["end"] == "2022-06-02"
    assert cfg["flow_units"] == "CMS"
    assert cfg["infiltration"] == "CURVE_NUMBER"
    assert cfg["routing_model"] == "DYNWAVE"
    assert cfg["rain_interval_s"] == 3600
    assert cfg["rain_format"] == "VOLUME"
    # runtime-only out_dir must NOT leak into the citable artifact
    assert "out_dir" not in cfg
    assert meta["provenance"]["crs"] == "EPSG:4326"
    assert set(meta["files"]) == {"network.gpkg", "forcing.nc"}


# --------------------------------------------------------------------------- #
# 2. Round-trip: write → read_datastore reconstructs the exact dataclasses
# --------------------------------------------------------------------------- #
def _write_and_read(tmp_path) -> ModelReadyDatastore:
    out = tmp_path / "ds"
    write_datastore(
        out, network=_network(), subcatchments=_subcatchments(), rain=_rain(),
        config=_config(tmp_path / "build"), provenance=_provenance(),
    )
    return read_datastore(out)


def test_roundtrip_returns_modelready_datastore(tmp_path):
    ds = _write_and_read(tmp_path)
    assert isinstance(ds, ModelReadyDatastore)
    assert isinstance(ds.network, NetworkIn)
    assert all(isinstance(s, SubcatchmentIn) for s in ds.subcatchments)
    assert isinstance(ds.rain, RainfallSeries)


def test_roundtrip_junctions(tmp_path):
    ds = _write_and_read(tmp_path)
    got = ds.network.junctions
    exp = _network().junctions
    assert [j.name for j in got] == [j.name for j in exp]  # ordering by name preserved
    for g, e in zip(got, exp):
        assert g.name == e.name
        assert g.invert_m == pytest.approx(e.invert_m, abs=1e-6)
        assert g.x == pytest.approx(e.x, abs=1e-9)
        assert g.y == pytest.approx(e.y, abs=1e-9)
        assert g.max_depth_m == pytest.approx(e.max_depth_m, abs=1e-6)


def test_roundtrip_outfalls(tmp_path):
    ds = _write_and_read(tmp_path)
    got = ds.network.outfalls
    exp = _network().outfalls
    assert [o.name for o in got] == [o.name for o in exp]
    for g, e in zip(got, exp):
        assert g.name == e.name
        assert g.invert_m == pytest.approx(e.invert_m, abs=1e-6)
        assert g.x == pytest.approx(e.x, abs=1e-9)
        assert g.y == pytest.approx(e.y, abs=1e-9)
        assert g.kind == e.kind


def test_roundtrip_conduits(tmp_path):
    ds = _write_and_read(tmp_path)
    got = ds.network.conduits
    exp = _network().conduits
    assert [c.name for c in got] == [c.name for c in exp]
    for g, e in zip(got, exp):
        assert g.name == e.name
        assert g.from_node == e.from_node
        assert g.to_node == e.to_node
        assert g.length_m == pytest.approx(e.length_m, abs=1e-6)
        assert g.diameter_m == pytest.approx(e.diameter_m, abs=1e-6)
        assert g.roughness_n == pytest.approx(e.roughness_n, abs=1e-6)


def test_roundtrip_subcatchments_scalars(tmp_path):
    ds = _write_and_read(tmp_path)
    got = ds.subcatchments
    exp = _subcatchments()
    assert [s.name for s in got] == [s.name for s in exp]  # ordering by name preserved
    scalar_fields = (
        "outlet_node", "area_ha", "pct_imperv", "width_m", "pct_slope", "cn",
        "n_imperv", "n_perv", "s_imperv_mm", "s_perv_mm", "pct_zero",
    )
    for g, e in zip(got, exp):
        assert g.name == e.name
        assert g.outlet_node == e.outlet_node
        for f in scalar_fields:
            gv, ev = getattr(g, f), getattr(e, f)
            if isinstance(ev, str):
                assert gv == ev
            else:
                assert gv == pytest.approx(ev, abs=1e-6)


def test_roundtrip_subcatchment_polygons(tmp_path):
    ds = _write_and_read(tmp_path)
    by_name = {s.name: s for s in ds.subcatchments}
    exp = {s.name: s for s in _subcatchments()}

    # None polygon preserved as None (not [] or a degenerate ring)
    assert by_name["S2"].polygon is None

    for name in ("S1", "S3"):
        gp = by_name[name].polygon
        ep = exp[name].polygon
        assert gp is not None
        assert len(gp) == len(ep)
        for (gx, gy), (ex, ey) in zip(gp, ep):
            assert gx == pytest.approx(ex, abs=1e-9)
            assert gy == pytest.approx(ey, abs=1e-9)


def test_roundtrip_rainfall(tmp_path):
    ds = _write_and_read(tmp_path)
    got, exp = ds.rain, _rain()
    assert got.gage_name == exp.gage_name
    assert got.ts_name == exp.ts_name
    assert got.timestamps == exp.timestamps  # exact datetimes
    assert len(got.precip_mm) == len(exp.precip_mm)
    for g, e in zip(got.precip_mm, exp.precip_mm):
        assert g == pytest.approx(e, abs=1e-6)


def test_roundtrip_config_and_provenance(tmp_path):
    ds = _write_and_read(tmp_path)
    assert ds.config["title"] == "Datastore fixture model"
    assert ds.config["flow_units"] == "CMS"
    assert ds.config["rain_interval_s"] == 3600
    assert ds.provenance["crs"] == "EPSG:4326"
    assert ds.provenance["sources"]["streets"] == "OSM"


# --------------------------------------------------------------------------- #
# 2b. Evaporation forcing round-trips through forcing.nc (issue #7)
# --------------------------------------------------------------------------- #
def test_evaporation_roundtrips_and_records_provenance(tmp_path):
    import json

    out = tmp_path / "ds"
    write_datastore(
        out, network=_network(), subcatchments=_subcatchments(), rain=_rain(),
        config=_config(tmp_path / "build"), provenance=_provenance(),
        evaporation=_evaporation(), temperature=_temperature(),
    )
    # No new carrier file — evaporation lives inside forcing.nc.
    meta = json.loads((out / "datastore.json").read_text())
    assert set(meta["files"]) == {"network.gpkg", "forcing.nc"}
    assert schema_evap_in(meta)                                  # provenance self-describes forcing

    ds = read_datastore(out)
    assert ds.evaporation is not None
    assert ds.evaporation.timestamps == _evaporation().timestamps
    for g, e in zip(ds.evaporation.evap_mm_day, _evaporation().evap_mm_day):
        assert g == pytest.approx(e, abs=1e-6)


def schema_evap_in(meta: dict) -> bool:
    forcing = meta["provenance"].get("forcing", {})
    return "evaporation" in forcing.get("variables", []) and "evaporation_method" in forcing


def test_no_evaporation_by_default_roundtrips_to_none(tmp_path):
    out = tmp_path / "ds"
    write_datastore(
        out, network=_network(), subcatchments=_subcatchments(), rain=_rain(),
        config=_config(tmp_path / "build"), provenance=_provenance(),
    )
    assert read_datastore(out).evaporation is None              # rain-only datastore


def test_build_from_datastore_includes_evaporation(tmp_path):
    ds_dir = tmp_path / "ds"
    write_datastore(
        ds_dir, network=_network(), subcatchments=_subcatchments(), rain=_rain(),
        config=_config(tmp_path / "ignored"), provenance=_provenance(),
        evaporation=_evaporation(), temperature=_temperature(),
    )
    result = build_from_datastore(ds_dir, tmp_path / "model")
    assert "EVAPORATION" in result.sections_written            # datastore evap → SWMM model


# --------------------------------------------------------------------------- #
# 3. Datastore → build: the datastore is sufficient to build a SWMM model
# --------------------------------------------------------------------------- #
def test_build_from_datastore_produces_runnable_inp(tmp_path):
    ds_dir = tmp_path / "ds"
    write_datastore(
        ds_dir, network=_network(), subcatchments=_subcatchments(), rain=_rain(),
        config=_config(tmp_path / "ignored_build_dir"), provenance=_provenance(),
    )

    build_dir = tmp_path / "model"
    result = build_from_datastore(ds_dir, build_dir)

    assert result.inp_path.exists()
    assert result.inp_path.parent == build_dir

    # Re-parse the produced .inp and assert counts match the fixture.
    from swmm_api import read_inp_file
    from swmm_api.input_file import SEC

    inp = read_inp_file(str(result.inp_path))
    net, subs = _network(), _subcatchments()
    assert len(inp[SEC.JUNCTIONS]) == len(net.junctions)
    assert len(inp[SEC.OUTFALLS]) == len(net.outfalls)
    assert len(inp[SEC.CONDUITS]) == len(net.conduits)
    assert len(inp[SEC.SUBCATCHMENTS]) == len(subs)

    for sec in ("JUNCTIONS", "OUTFALLS", "CONDUITS", "SUBCATCHMENTS", "RAINGAGES", "TIMESERIES"):
        assert sec in result.sections_written


def test_build_from_datastore_uses_stored_config(tmp_path):
    ds_dir = tmp_path / "ds"
    write_datastore(
        ds_dir, network=_network(), subcatchments=_subcatchments(), rain=_rain(),
        config=_config(tmp_path / "ignored"), provenance=_provenance(),
    )
    result = build_from_datastore(ds_dir, tmp_path / "model")

    # The build's manifest should reflect the stored config (title/flow_units/dates),
    # proving build_from_datastore reconstructed BuildConfig from the datastore.
    import json

    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["title"] == "Datastore fixture model"
    assert manifest["flow_units"] == "CMS"
    assert manifest["start_date"] == "2022-06-01"
    assert manifest["end_date"] == "2022-06-02"
