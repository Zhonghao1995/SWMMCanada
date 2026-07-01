"""The ICM exporter is a scaffold (issue #6): it conforms to the export interface, but its
field mapping is deliberately not implemented yet."""
import pytest

from swmmcanada.export.base import ModelExporter
from swmmcanada.export.icm import IcmExporter


def test_icm_exporter_conforms_to_interface():
    exp = IcmExporter()
    assert isinstance(exp, ModelExporter)   # satisfies the ModelExporter protocol
    assert exp.target == "icm"


def test_icm_export_not_implemented_yet(tmp_path):
    with pytest.raises(NotImplementedError):
        IcmExporter().export(ds=None, out_dir=tmp_path)
