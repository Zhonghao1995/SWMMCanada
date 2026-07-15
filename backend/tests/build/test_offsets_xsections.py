"""#130: conduit inlet/outlet offsets + non-circular cross-sections reach the .inp and
round-trip the datastore."""
from datetime import date, datetime

from swmm_api import read_inp_file

from swmmcanada.build.assemble import build_model
from swmmcanada.build.config import BuildConfig
from swmmcanada.build.models import (
    ConduitIn, JunctionIn, NetworkIn, OutfallIn, RainfallSeries, SubcatchmentIn,
)

NET = NetworkIn(
    junctions=[JunctionIn("J1", 10.0, -123.371, 48.420), JunctionIn("J2", 9.0, -123.369, 48.420)],
    outfalls=[OutfallIn("OF1", 8.5, -123.367, 48.420)],
    conduits=[
        ConduitIn("C_DROP", "J1", "J2", length_m=80.0, diameter_m=0.45,
                  inlet_offset_m=0.0, outlet_offset_m=2.5),
        ConduitIn("C_BOX", "J2", "OF1", length_m=60.0, diameter_m=0.45,
                  shape="RECT_CLOSED", height_m=1.2, width_m=2.4),
    ],
)
SUB = SubcatchmentIn(name="S1", outlet_node="J1", area_ha=1.0, pct_imperv=50.0,
                     width_m=100.0, pct_slope=1.0)
RAIN = RainfallSeries(timestamps=[datetime(2022, 6, 1, 0), datetime(2022, 6, 1, 1)],
                      precip_mm=[5.0, 0.0])


def _build(tmp_path):
    return build_model(network=NET, subcatchments=[SUB], rain=RAIN,
                       config=BuildConfig(out_dir=tmp_path, start=date(2022, 6, 1),
                                          end=date(2022, 6, 2)))


def test_offsets_and_shapes_reach_the_inp(tmp_path):
    res = _build(tmp_path)
    inp = read_inp_file(str(res.inp_path))
    cond = inp.CONDUITS["C_DROP"]
    assert float(cond.offset_downstream) == 2.5 and float(cond.offset_upstream) == 0.0
    xs = inp.XSECTIONS["C_BOX"]
    assert str(xs.shape) == "RECT_CLOSED"
    assert float(xs.height) == 1.2 and float(xs.parameter_2) == 2.4
    assert str(inp.XSECTIONS["C_DROP"].shape) == "CIRCULAR"


def test_datastore_roundtrips_offsets_and_shapes(tmp_path):
    from swmmcanada.datastore import read_datastore, write_datastore

    ws = tmp_path / "ds"
    write_datastore(ws, network=NET, subcatchments=[SUB], rain=RAIN,
                    config=BuildConfig(out_dir=tmp_path, start=date(2022, 6, 1),
                                       end=date(2022, 6, 2)))
    data = read_datastore(ws)
    by = {c.name: c for c in data.network.conduits}
    assert by["C_DROP"].outlet_offset_m == 2.5
    assert by["C_BOX"].shape == "RECT_CLOSED"
    assert by["C_BOX"].height_m == 1.2 and by["C_BOX"].width_m == 2.4
    assert by["C_DROP"].shape == "CIRCULAR" and by["C_DROP"].height_m is None
