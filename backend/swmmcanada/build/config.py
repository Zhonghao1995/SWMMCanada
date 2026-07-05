"""Build configuration (spec 09 §2). The whole surface is `build_model(...) + BuildConfig`."""
import os
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum
from typing import Optional


class FlowUnits(str, Enum):
    CMS = "CMS"   # m³/s — SI, Canada default
    LPS = "LPS"
    CFS = "CFS"


class InfiltrationModel(str, Enum):
    CURVE_NUMBER = "CURVE_NUMBER"   # SCS-CN — the HSG→CN path from derive (v1 default)
    HORTON = "HORTON"
    GREEN_AMPT = "GREEN_AMPT"


@dataclass(frozen=True)
class BuildConfig:
    out_dir: "os.PathLike[str]"
    start: date
    end: date
    title: str = "SWMMCanada model"
    flow_units: FlowUnits = FlowUnits.CMS
    # ADR 0013: default = Horton (municipal engineering practice; MIKE+ native).
    # CN / Green-Ampt selectable; derive stores all three parameter sets regardless.
    infiltration: InfiltrationModel = InfiltrationModel.HORTON
    routing_model: str = "DYNWAVE"          # FLOW_ROUTING
    rain_interval: timedelta = timedelta(hours=1)
    rain_format: str = "VOLUME"             # depth (mm) per interval
    # CRS for the .inp's display coordinates ([COORDINATES]/[POLYGONS]). Node x/y and
    # polygons are EPSG:4326 (lon/lat); SWMM/PCSWMM plot them as planar X,Y, which distorts
    # geographic degrees. Set a projected metric CRS (e.g. UTM "EPSG:32610") so the model
    # displays undistorted. None = write lon/lat as-is. Hydraulics use conduit Length, not
    # these coordinates, so this is display-only.
    coordinate_crs: Optional[str] = None
