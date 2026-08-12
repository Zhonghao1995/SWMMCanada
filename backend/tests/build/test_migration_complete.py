"""The migration bridge is gone (ADR 0029 Q8).

`SubcatchmentIn` was a temporary alias so ~60 call sites across 16 modules could move in
reviewable steps. Keeping it would leave the ambiguous word as the thing a developer reaches
for by default — and the ambiguity of "subcatchment" meaning two different things is what
started this work.

What must NOT disappear is the word itself where it is correct: `[SUBCATCHMENTS]` is SWMM's
own section name and the datastore layer is named for it. For a storm system, subcatchment
is the right term. Only the internal type name changed.
"""
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "swmmcanada"


def python_files():
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in str(p)]


def test_the_alias_no_longer_exists():
    import swmmcanada.build.models as models

    assert not hasattr(models, "SubcatchmentIn"), (
        "the migration bridge is still exported; ADR 0029 Q8 gives it a deadline")


def test_no_module_still_imports_the_old_name():
    offenders = [str(p.relative_to(SRC)) for p in python_files()
                 if "SubcatchmentIn" in p.read_text()]
    assert not offenders, f"still on the old type name: {offenders}"


def test_the_new_name_is_the_one_in_use():
    from swmmcanada.build.models import SurfaceCatchment

    s = SurfaceCatchment("S1", "J1", 1.0, 50.0, 100.0, 1.0)
    assert s.name == "S1"


class TestTheWordSurvivesWhereItIsCorrect:
    """A storm subcatchment IS a subcatchment. Only the ambiguous *type* name went."""

    def test_the_swmm_section_name_is_untouched(self):
        from swmm_api.input_file import SEC

        assert SEC.SUBCATCHMENTS == "SUBCATCHMENTS"

    def test_the_datastore_layer_name_is_untouched(self):
        from swmmcanada.datastore import schema

        assert schema.LAYER_SUBCATCHMENTS == "subcatchments"
        assert schema.SUBCATCHMENT_FIELDS[0] == "name"

    def test_written_models_still_have_a_subcatchments_section(self):
        """The rename must be invisible in the product."""
        from datetime import date, datetime, timedelta

        from swmmcanada.build import BuildConfig
        from swmmcanada.build.assemble import assemble_inp
        from swmmcanada.build.models import (ConduitIn, JunctionIn, NetworkIn, OutfallIn,
                                             RainfallSeries, SurfaceCatchment)

        net = NetworkIn(junctions=[JunctionIn("J1", 10.0, 3.0, 0.0)],
                        outfalls=[OutfallIn("O1", 8.0, 0.0, 0.0)],
                        conduits=[ConduitIn("C1", "J1", "O1", 50.0)])
        t0 = datetime(2024, 6, 1)
        rain = RainfallSeries([t0 + timedelta(hours=i) for i in range(6)], [0.0] * 6)
        cfg = BuildConfig(out_dir="/tmp", start=date(2024, 6, 1), end=date(2024, 6, 2))
        txt = assemble_inp(net, [SurfaceCatchment("S1", "J1", 1.0, 50.0, 100.0, 1.0)],
                           rain, cfg).to_string()
        assert "[SUBCATCHMENTS]" in txt
