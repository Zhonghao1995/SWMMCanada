"""TDD for the SoilGrids texture→HSG mapping (the offline, ownable logic). The live WCS
fetch is exercised by the integration test."""
import numpy as np

from swmmcanada.sources.soil_soilgrids import texture_to_hsg


def test_texture_to_hsg_classes():
    # g/kg → %:  (5% clay, 80% sand)=A, (15,40)=B, (30,30)=C, (45,20)=D, (0,0)=NoData
    clay = np.array([50, 150, 300, 450, 0])
    sand = np.array([800, 400, 300, 200, 0])
    assert list(texture_to_hsg(clay, sand)) == [1, 2, 3, 4, 255]
