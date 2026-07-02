"""The result-package contract (ADR 0009): the schema is the single source of truth for
what a shippable package contains; missing_required is the guard api/tasks enforces."""
from swmmcanada import result_package as rp


def _touch_all(root):
    for rel in rp.REQUIRED:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")


def test_complete_package_has_nothing_missing(tmp_path):
    _touch_all(tmp_path)
    assert rp.missing_required(tmp_path) == []


def test_missing_paths_are_named(tmp_path):
    _touch_all(tmp_path)
    (tmp_path / rp.MODEL_INP).unlink()
    (tmp_path / rp.PREVIEW_GEOJSON).unlink()
    assert rp.missing_required(tmp_path) == [rp.MODEL_INP, rp.PREVIEW_GEOJSON]


def test_contract_covers_the_handoff_essentials():
    # The .inp, its manifest, the validation verdict, all three datastore carriers, preview.
    assert rp.MODEL_INP in rp.REQUIRED and rp.MANIFEST_JSON in rp.REQUIRED
    assert rp.VALIDATION_JSON in rp.REQUIRED
    assert sum(1 for r in rp.REQUIRED if r.startswith(f"{rp.DATASTORE_DIR}/")) == 3
    assert rp.PREVIEW_GEOJSON in rp.REQUIRED
    # mikeplus/ is deliberately NOT required (ADR 0008 graceful degradation).
    assert not any(r.startswith(rp.MIKEPLUS_DIR) for r in rp.REQUIRED)
