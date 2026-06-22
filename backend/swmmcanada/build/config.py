"""Build configuration (spec 09 §2). The whole surface is `build_model(...) + BuildConfig`."""
import os
from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum


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
    infiltration: InfiltrationModel = InfiltrationModel.CURVE_NUMBER
    routing_model: str = "DYNWAVE"          # FLOW_ROUTING
    rain_interval: timedelta = timedelta(hours=1)
    rain_format: str = "VOLUME"             # depth (mm) per interval
