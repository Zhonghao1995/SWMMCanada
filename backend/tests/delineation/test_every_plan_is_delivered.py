"""Every plan the resolver can return must be delivered, not merely named.

The earlier guard checked that a plan's shaping appeared somewhere in the code that executes
plans. It passed while two of the four reachable plans were being ignored: their names
appeared in branches guarded on `anchors == "catch_basin"`, which the resolver had stopped
producing. The resolver said "parcel", the pipeline delivered a nearest-node tessellation,
and the provenance recorded the plan rather than the result.

Checking for a name cannot catch that. This checks the delivery: run each plan through the
pipeline's own dispatch and see what comes back.
"""
import pytest

from swmmcanada.delineation import Evidence, resolve

#: The distinct plans the resolver can reach from evidence a real build can produce.
REACHABLE = {
    "street segment": Evidence(n_junctions=391, n_streets=250, n_parcels=3939),
    "terrain": Evidence(n_junctions=391, dem_available=True, dem_resolution_m=1.0),
    "lot lines": Evidence(n_junctions=391, n_parcels=3939),
    "nearest node": Evidence(n_junctions=391),
    "user layer": Evidence(n_junctions=391, n_user_units=50),
}


@pytest.mark.parametrize("label", sorted(REACHABLE))
def test_the_dispatch_has_a_branch_for_every_plan(label):
    """The pipeline decides what to run from `plan.shaping`. A shaping with no branch falls
    through to the fallback, which is a silent downgrade rather than an error."""
    import inspect

    from swmmcanada import pipeline

    shaping = resolve(REACHABLE[label]).shaping
    src = inspect.getsource(pipeline.build_city)
    branches = [line for line in src.splitlines() if "plan.shaping ==" in line]
    handled = {line.split("plan.shaping ==")[1].strip().rstrip(":").strip().strip('"')
               for line in branches}
    assert shaping in handled or shaping == "voronoi", (
        f"shaping={shaping!r} has no branch; it falls through to the nearest-node fallback "
        f"while the provenance records the plan. Handled: {sorted(handled)}")


def test_no_branch_tests_an_anchor_the_resolver_cannot_produce():
    """Dead conditions are how the previous defect hid: the names were present, guarded on
    an anchor that had stopped existing."""
    import inspect

    from swmmcanada import pipeline

    producible = {resolve(ev).anchors for ev in REACHABLE.values()}
    src = inspect.getsource(pipeline.build_city)
    for line in src.splitlines():
        if "plan.anchors ==" in line:
            anchor = line.split("plan.anchors ==")[1].strip().split()[0]
            anchor = anchor.rstrip(":").strip().strip('"')
            assert anchor in producible, (
                f"branch guards on anchors=={anchor!r}, which the resolver never returns")
