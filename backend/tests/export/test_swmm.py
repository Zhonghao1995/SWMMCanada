"""TDD for the SWMM exporter: the datastore, exported behind the uniform interface
(ADR 0008), yields the same runnable, round-trippable `.inp` as the primary build path.
SWMM is the native format, so the export is lossless (``lossy == []``)."""
from datetime import datetime

from swmm_api import read_inp_file

from swmmcanada.build import (
    ConduitIn,
    JunctionIn,
    NetworkIn,
    OutfallIn,
    RainfallSeries,
    SubcatchmentIn,
)
from swmmcanada.datastore import ModelReadyDatastore
from swmmcanada.export.base import ModelExporter
from swmmcanada.export.swmm import SwmmExporter


def _tiny_datastore() -> ModelReadyDatastore:
    network = NetworkIn(
        junctions=[
            JunctionIn("J1", invert_m=99.0, x=0.0, y=0.0),
            JunctionIn("J2", invert_m=98.5, x=100.0, y=0.0),
        ],
        outfalls=[OutfallIn("O1", invert_m=98.0, x=200.0, y=0.0)],
        conduits=[
            ConduitIn("C1", "J1", "J2", length_m=100.0),
            ConduitIn("C2", "J2", "O1", length_m=100.0),
        ],
    )
    subcatchments = [
        SubcatchmentIn("S1", outlet_node="J1", area_ha=1.0, pct_imperv=40.0, width_m=100.0, pct_slope=1.0)
    ]
    rain = RainfallSeries(
        timestamps=[datetime(2020, 6, 1, h) for h in range(3)], precip_mm=[1.2, 3.4, 0.0]
    )
    return ModelReadyDatastore(
        network=network,
        subcatchments=subcatchments,
        rain=rain,
        config={"start": "2020-06-01", "end": "2020-06-02", "coordinate_crs": None},
        provenance={},
        evaporation=None,
    )


def test_export_produces_roundtrippable_inp(tmp_path):
    res = SwmmExporter().export(_tiny_datastore(), tmp_path)

    assert res.target == "swmm"
    assert res.lossy == []  # SWMM is native — nothing to approximate/drop
    assert res.files[0].exists()  # the .inp

    inp = read_inp_file(str(res.files[0]))
    assert "JUNCTIONS" in inp
    assert "CONDUITS" in inp


def test_exporter_satisfies_protocol():
    assert isinstance(SwmmExporter(), ModelExporter)
