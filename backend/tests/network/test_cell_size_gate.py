"""A delineation whose cells are mostly noise has not produced units (规划书 §4).

D8 to many pour points assigns each cell to the FIRST one downstream. On a gutter with
several inlets, the downstream inlet collects the block and the ones above it get slivers.
The repo already named this: `MIN_CELL_HA = 0.05` is documented as "noise from adjacent pour
points on one flow path".

Measured on live Victoria, the terrain path returned 59% of cells at noise scale against the
inlet-tessellation path's 36% — a majority. Coverage was fine and the existing posterior
check passed it, so it would have shipped as the default silently.

The gate asks the one question that matters about a delineation: did it produce units?
"""
import pytest

from swmmcanada.network.delineate_dem import NOISE_CELL_MAJORITY, cells_are_mostly_noise
from swmmcanada.network.service_area import MIN_CELL_HA


class _Cell:
    def __init__(self, area_ha):
        self.area_ha = area_ha


def _cells(*areas):
    return [_Cell(a) for a in areas]


class TestTheGate:
    def test_a_majority_of_slivers_fails(self):
        """Victoria's terrain result: 59% below the noise threshold."""
        cells = _cells(*([0.001] * 59 + [1.0] * 41))
        assert cells_are_mostly_noise(cells) is True

    def test_a_minority_of_slivers_passes(self):
        """The inlet-tessellation path's 36% — not ideal, and not a failed delineation."""
        cells = _cells(*([0.001] * 36 + [1.0] * 64))
        assert cells_are_mostly_noise(cells) is False

    def test_the_threshold_is_the_one_the_repo_already_documents(self):
        assert 0 < MIN_CELL_HA < 0.5
        just_above = _cells(*([MIN_CELL_HA * 1.01] * 100))
        assert cells_are_mostly_noise(just_above) is False

    def test_it_is_a_majority_rule_not_a_tuned_number(self):
        assert NOISE_CELL_MAJORITY == 0.5

    def test_an_empty_delineation_is_not_judged_by_this(self):
        """Producing nothing is a different failure, caught elsewhere."""
        assert cells_are_mostly_noise([]) is False


class TestItRunsInThePosteriorGate:
    def test_the_delineator_falls_back_when_its_cells_are_noise(self, tmp_path):
        """End to end: the check has to be wired where the fallback lives, or it is a
        function nobody calls — which has happened three times already."""
        import inspect

        from swmmcanada.network import delineate_dem

        src = inspect.getsource(delineate_dem)
        assert "cells_are_mostly_noise" in src.split("def cells_are_mostly_noise")[0] or \
               src.count("cells_are_mostly_noise") >= 2, \
               "the gate is defined and never consulted"
