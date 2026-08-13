"""Every choice the resolver can make must be acted on somewhere.

This failure has now happened three times: the DEM posting was never supplied so the terrain
path was unreachable; the official-basin level was never set so the boundary branch could not
fire; and the clip itself was written, tested and committed while nothing called it.

Each time the code looked complete. The resolver recorded a decision, the provenance carried
a reason, the unit tests were green — and the path had never once run. Reviewing for it does
not work, because the missing half is an absence.

So it is a test. For every value the resolver can emit, something downstream has to name it.
"""
import inspect

import pytest

from swmmcanada import pipeline
from swmmcanada.delineation import Evidence, resolve

#: Every distinct plan a resolver call can return, and the evidence that produces it.
PLANS = {
    "user layer": Evidence(n_user_units=50),
    "kerb-conditioned terrain": Evidence(n_catchbasins=700, n_kerbs=2000,
                                         dem_available=True, dem_resolution_m=1.0),
    "terrain without kerbs": Evidence(n_catchbasins=700, dem_available=True,
                                      dem_resolution_m=1.0),
    "inlets and land": Evidence(n_catchbasins=700, n_parcels=4000),
    "inlets only": Evidence(n_catchbasins=700),
    "junction terrain": Evidence(n_junctions=100, dem_available=True),
    "nothing": Evidence(n_junctions=100),
    "official boundary": Evidence(n_catchbasins=700, n_parcels=4000,
                                  official_basin_level="level_2"),
}


def _executor_source() -> str:
    """Everything that acts on a plan. Kept narrow on purpose — a value named only in the
    resolver's own module proves nothing."""
    import swmmcanada.delineation.boundary as boundary

    return inspect.getsource(pipeline) + inspect.getsource(boundary)


@pytest.mark.parametrize("label", sorted(PLANS))
def test_the_shaping_a_plan_asks_for_is_named_by_something_that_executes(label):
    shaping = resolve(PLANS[label]).shaping
    assert f'"{shaping}"' in _executor_source() or f"'{shaping}'" in _executor_source(), (
        f"the resolver can ask for shaping={shaping!r} and nothing acts on it")


@pytest.mark.parametrize("label", sorted(PLANS))
def test_the_anchors_a_plan_asks_for_are_named_by_something_that_executes(label):
    anchors = resolve(PLANS[label]).anchors
    src = _executor_source()
    assert f'"{anchors}"' in src or f"'{anchors}'" in src, (
        f"the resolver can ask for anchors={anchors!r} and nothing acts on it")


def test_the_official_boundary_is_acted_on_not_merely_recorded():
    """The specific case that got through: a hard edge the plan asked for and nobody cut."""
    plan = resolve(PLANS["official boundary"])
    assert plan.boundary == "official_basin"
    src = inspect.getsource(pipeline)
    assert "clip_to_official_basins" in src, (
        "the plan asks for an official boundary and the pipeline never clips to one")
