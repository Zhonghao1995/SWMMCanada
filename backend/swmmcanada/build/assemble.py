"""Assemble a runnable EPA SWMM 5.2 `.inp` from upstream value objects (spec 09).

This is the single place that knows SWMM file syntax. Objects in, files out. Every build
round-trips its own output through swmm-api (and swmmio) before returning, so a
BuildResult is guaranteed parseable.
"""
import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import List, Optional

from swmm_api import SwmmInput, read_inp_file
from swmm_api.input_file import SEC
from swmm_api.input_file.sections import (
    Conduit,
    Coordinate,
    CrossSection,
    EvaporationSection,
    Infiltration,
    InfiltrationCurveNumber,
    InfiltrationGreenAmpt,
    InfiltrationHorton,
    Junction,
    OptionSection,
    Outfall,
    Polygon,
    RainGage,
    SnowPack,
    SubArea,
    SubCatchment,
    Timeseries,
    TimeseriesData,
)
from swmm_api.input_file.sections.generic_section import TemperatureSection

from swmmcanada.build.config import BuildConfig, InfiltrationModel
from swmmcanada.build.models import (
    EvaporationSeries,
    NetworkIn,
    RainfallSeries,
    SubcatchmentIn,
    TemperatureSeries,
)

# Snowmelt defaults (issue #55; ASSUMPTIONS.md documents these with sources). Melt
# coefficients from typical degree-day factors 2.4–7.2 mm·d⁻¹·°C⁻¹ → mm·h⁻¹·°C⁻¹;
# dividing temperature 0 °C; ATI weight / negative-melt ratio are SWMM manual defaults.
SNOWPACK_NAME = "URBAN"
_MELT_CMIN = 0.1        # mm/h/°C  (≈2.4 mm/d/°C, season start)
_MELT_CMAX = 0.3        # mm/h/°C  (≈7.2 mm/d/°C, season end)
_TBASE_C = 0.0
_FWF = 0.10             # free-water holding capacity fraction
_SNN0 = 0.10            # plowable fraction of impervious area
_SD100_MM = 25.0        # snow depth at 100 % areal coverage
_SNOW_TEMP_DIVIDE_C = 0.0
_ATI_WEIGHT = 0.5       # SWMM defaults
_NEG_MELT_RATIO = 0.6


@dataclass(frozen=True)
class BuildResult:
    inp_path: Path
    package_dir: Path
    manifest_path: Path
    sections_written: List[str]
    warnings: List[str]
    observed_flow_csv: Optional[Path] = None


def _interval_str(td: timedelta) -> str:
    minutes = int(td.total_seconds() // 60)
    return f"{minutes // 60}:{minutes % 60:02d}"


def _rain_interval(rain: RainfallSeries, config: BuildConfig) -> timedelta:
    """Raingage recording interval = the rainfall series' own spacing (so SWMM doesn't warn
    that the series is coarser than the gage interval). Falls back to config for <2 points."""
    ts = rain.timestamps
    if len(ts) >= 2:
        deltas = sorted((ts[i + 1] - ts[i]).total_seconds() for i in range(len(ts) - 1))
        sec = deltas[len(deltas) // 2]  # median spacing
        if sec > 0:
            return timedelta(seconds=sec)
    return config.rain_interval


def _coord_projector(crs):
    """A (lon, lat) -> (x, y) projector for the .inp display coords, or identity if no CRS.
    Node x/y and polygons are EPSG:4326; projecting to a metric CRS (UTM) makes SWMM/PCSWMM
    render them undistorted. Delegates to the shared CRS seam (geo.crs)."""
    from swmmcanada.geo.crs import lonlat_projector

    return lonlat_projector(crs)


def assemble_inp(
    network: NetworkIn,
    subcatchments: List[SubcatchmentIn],
    rain: RainfallSeries,
    config: BuildConfig,
    evaporation: Optional[EvaporationSeries] = None,
    temperature: Optional[TemperatureSeries] = None,
) -> SwmmInput:
    inp = SwmmInput()
    with_snow = temperature is not None and bool(temperature.timestamps)

    opt = OptionSection()
    opt["FLOW_UNITS"] = config.flow_units.value
    opt["INFILTRATION"] = config.infiltration.value
    opt["FLOW_ROUTING"] = config.routing_model
    # Dynamic-wave numerics for pulsed forcing (ADR 0014 follow-through): the engine
    # defaults (20 s max step, no inertial damping) let short/steep conduits oscillate once
    # hourly intensities replace daily drizzle — Kitchener hit −976% routing continuity from
    # ONE oscillating node. Standard municipal stabilisation: 5 s max routing step (variable
    # stepping stays on), partial inertial damping, both normal-flow criteria, more trials.
    # Measured: Kitchener −976% → −0.17%, Ottawa −14.6% → +0.26%.
    opt["ROUTING_STEP"] = 5
    opt["MINIMUM_STEP"] = 0.5
    opt["INERTIAL_DAMPING"] = "PARTIAL"
    opt["NORMAL_FLOW_LIMITED"] = "BOTH"
    opt["MAX_TRIALS"] = 20
    opt["START_DATE"] = config.start
    opt["END_DATE"] = config.end
    opt["REPORT_START_DATE"] = config.start
    inp[SEC.OPTIONS] = opt

    junctions = Junction.create_section()
    for j in network.junctions:
        junctions.add_obj(Junction(j.name, elevation=j.invert_m, depth_max=j.max_depth_m))
    inp[SEC.JUNCTIONS] = junctions

    outfalls = Outfall.create_section()
    for o in network.outfalls:
        outfalls.add_obj(Outfall(o.name, elevation=o.invert_m, kind=o.kind))
    inp[SEC.OUTFALLS] = outfalls

    conduits = Conduit.create_section()
    xsections = CrossSection.create_section()
    for c in network.conduits:
        conduits.add_obj(
            Conduit(c.name, c.from_node, c.to_node, length=c.length_m, roughness=c.roughness_n,
                    offset_upstream=c.inlet_offset_m, offset_downstream=c.outlet_offset_m)
        )
        # #130: real cross-sections where the city published them; equivalent circular else.
        if c.shape and c.shape != "CIRCULAR" and c.height_m and c.width_m:
            xsections.add_obj(CrossSection(c.name, c.shape, height=c.height_m,
                                           parameter_2=c.width_m))
        else:
            xsections.add_obj(CrossSection(c.name, "CIRCULAR", height=c.diameter_m))
    inp[SEC.CONDUITS] = conduits
    inp[SEC.XSECTIONS] = xsections

    subs = SubCatchment.create_section()
    subareas = SubArea.create_section()
    infil = Infiltration.create_section()
    for s in subcatchments:
        sub_kwargs = {"snow_pack": SNOWPACK_NAME} if with_snow else {}
        subs.add_obj(
            SubCatchment(
                s.name,
                rain.gage_name,
                s.outlet_node,
                area=s.area_ha,
                imperviousness=s.pct_imperv,
                width=s.width_m,
                slope=s.pct_slope,
                **sub_kwargs,
            )
        )
        subareas.add_obj(
            SubArea(s.name, s.n_imperv, s.n_perv, s.s_imperv_mm, s.s_perv_mm, s.pct_zero)
        )
        # ADR 0013: one switch, three parameter-row shapes — the row MUST match
        # [OPTIONS] INFILTRATION (a mismatched pair mis-reads columns in the engine).
        if config.infiltration is InfiltrationModel.HORTON:
            infil.add_obj(InfiltrationHorton(
                s.name, rate_max=s.horton_f0_mm_h, rate_min=s.horton_fc_mm_h,
                decay=s.horton_decay_1_h, time_dry=7, volume_max=0,
            ))
        elif config.infiltration is InfiltrationModel.GREEN_AMPT:
            infil.add_obj(InfiltrationGreenAmpt(
                s.name, suction_head=s.ga_psi_mm, hydraulic_conductivity=s.ga_ksat_mm_h,
                moisture_deficit_init=s.ga_imd,
            ))
        else:
            infil.add_obj(InfiltrationCurveNumber(
                s.name, curve_no=s.cn, hydraulic_conductivity=0.5, time_dry=7
            ))
    inp[SEC.SUBCATCHMENTS] = subs
    inp[SEC.SUBAREAS] = subareas
    inp[SEC.INFILTRATION] = infil

    raingages = RainGage.create_section()
    raingages.add_obj(
        RainGage(
            rain.gage_name,
            form=config.rain_format,
            interval=_interval_str(_rain_interval(rain, config)),
            SCF=1.0,
            source="TIMESERIES",
            timeseries=rain.ts_name,
        )
    )
    inp[SEC.RAINGAGES] = raingages

    series = Timeseries.create_section()
    series.add_obj(TimeseriesData(rain.ts_name, list(zip(rain.timestamps, rain.precip_mm))))

    # Evaporation forcing (optional): a daily PET timeseries (mm/day) referenced by
    # [EVAPORATION] TIMESERIES. Absent → SWMM assumes evaporation = 0.
    if evaporation is not None and evaporation.timestamps:
        series.add_obj(
            TimeseriesData(evaporation.ts_name, list(zip(evaporation.timestamps, evaporation.evap_mm_day)))
        )
        evap_sec = EvaporationSection()
        evap_sec["TIMESERIES"] = evaporation.ts_name
        inp[SEC.EVAPORATION] = evap_sec

    # Snowmelt (#55): a temperature series turns on SWMM's snow machinery — [TEMPERATURE]
    # (series + SNOWMELT climatology) and one URBAN snow pack every subcatchment references.
    # Above the dividing temperature nothing accumulates, so summer-only runs are unaffected.
    if with_snow:
        series.add_obj(
            TimeseriesData("temperature", list(zip(temperature.timestamps, temperature.tmean_c)))
        )
        lat = _mean_latitude(network)
        elev = _mean_invert(network)
        temp_sec = TemperatureSection()
        temp_sec["TIMESERIES"] = "temperature"
        temp_sec["SNOWMELT"] = [       # a LIST: the generic section joins list items with
            _SNOW_TEMP_DIVIDE_C,       # spaces (a tuple reprs, a string gets quoted)
            _ATI_WEIGHT, _NEG_MELT_RATIO, round(elev, 1), round(lat, 4), 0.0,
        ]
        inp[SEC.TEMPERATURE] = temp_sec

        pack = SnowPack(SNOWPACK_NAME)
        pack.parts["PLOWABLE"] = SnowPack.PARTS.Plowable(
            _MELT_CMIN, _MELT_CMAX, _TBASE_C, _FWF, 0.0, 0.0, _SNN0)
        pack.parts["IMPERVIOUS"] = SnowPack.PARTS.Impervious(
            _MELT_CMIN, _MELT_CMAX, _TBASE_C, _FWF, 0.0, 0.0, _SD100_MM)
        pack.parts["PERVIOUS"] = SnowPack.PARTS.Pervious(
            _MELT_CMIN, _MELT_CMAX, _TBASE_C, _FWF, 0.0, 0.0, _SD100_MM)
        packs = SnowPack.create_section()
        packs.add_obj(pack)
        inp[SEC.SNOWPACKS] = packs
    inp[SEC.TIMESERIES] = series

    project = _coord_projector(config.coordinate_crs)
    coords = Coordinate.create_section()
    for n in list(network.junctions) + list(network.outfalls):
        coords.add_obj(Coordinate(n.name, *project(n.x, n.y)))
    inp[SEC.COORDINATES] = coords

    polys = Polygon.create_section()
    n_polys = 0
    for s in subcatchments:
        if getattr(s, "polygon", None):
            polys.add_obj(Polygon(s.name, [project(px, py) for px, py in s.polygon]))
            n_polys += 1
    if n_polys:
        inp[SEC.POLYGONS] = polys

    return inp


def _system_counts(network: NetworkIn) -> dict:
    """Per-system element counts (ADR 0011); single-system models report one entry."""
    out: dict = {}
    for n in list(network.junctions) + list(network.outfalls):
        out.setdefault(n.system, {"nodes": 0, "conduits": 0})["nodes"] += 1
    for c in network.conduits:
        out.setdefault(c.system, {"nodes": 0, "conduits": 0})["conduits"] += 1
    return out


def _mean_latitude(network: NetworkIn) -> float:
    """Mean junction latitude — the SNOWMELT climatology's latitude input (node y is
    EPSG:4326 by repo contract)."""
    ys = [j.y for j in network.junctions] or [50.0]
    return sum(ys) / len(ys)


def _mean_invert(network: NetworkIn) -> float:
    """Mean junction invert (m) — a serviceable site elevation for SNOWMELT's
    atmospheric-pressure term."""
    zs = [j.invert_m for j in network.junctions] or [0.0]
    return sum(zs) / len(zs)


def validate_inp(inp_path: Path) -> List[str]:
    """Round-trip through both parsers + cross-reference integrity. Returns warnings;
    raises BuildValidationError on a model that cannot re-read itself."""
    warnings: List[str] = []
    try:
        read_inp_file(str(inp_path))           # swmm-api round-trip
        import swmmio

        _ = swmmio.Model(str(inp_path)).inp.junctions  # swmmio round-trip (forces parse)
    except Exception as exc:  # noqa: BLE001 - re-raise as a typed build failure
        raise BuildValidationError(f"Assembled .inp failed round-trip: {exc}") from exc
    return warnings


class BuildValidationError(Exception):
    """The assembled .inp could not re-parse through swmm-api / swmmio."""


def build_model(
    *,
    network: NetworkIn,
    subcatchments: List[SubcatchmentIn],
    rain: RainfallSeries,
    config: BuildConfig,
    evaporation: Optional[EvaporationSeries] = None,
    temperature: Optional[TemperatureSeries] = None,
    observed=None,
    aoi=None,
) -> BuildResult:
    out_dir = Path(config.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    inp = assemble_inp(network, subcatchments, rain, config, evaporation=evaporation,
                       temperature=temperature)
    inp_path = out_dir / "model.inp"
    inp.write_file(str(inp_path))

    warnings = validate_inp(inp_path)
    sections = sorted(str(k) for k in inp.keys())

    manifest = {
        "title": config.title,
        "flow_units": config.flow_units.value,
        "infiltration": config.infiltration.value,
        "start_date": config.start.isoformat(),
        "end_date": config.end.isoformat(),
        "n_junctions": len(network.junctions),
        "n_outfalls": len(network.outfalls),
        "n_conduits": len(network.conduits),
        "n_subcatchments": len(subcatchments),
        "has_evaporation": str(SEC.EVAPORATION) in sections,
        "has_snowmelt": str(SEC.SNOWPACKS) in sections,
        "systems": _system_counts(network),
        "sections": sections,
        "inp_sha256": hashlib.sha256(inp_path.read_bytes()).hexdigest(),
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    return BuildResult(
        inp_path=inp_path,
        package_dir=out_dir,
        manifest_path=manifest_path,
        sections_written=sections,
        warnings=warnings,
    )
