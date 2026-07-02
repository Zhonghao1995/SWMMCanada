"""Offline tests for sources.idf_eccc (issue #56): parsing recorded ECCC v3.20 station
txt fixtures, design-intensity evaluation, nearest-station lookup, and the ranged-zip
fetch path via an injected fake client. No network."""
import io
import zipfile
from pathlib import Path

import pytest

from swmmcanada.sources.idf_eccc import (
    IDF_RETURN_PERIODS,
    IdfStation,
    IdfTable,
    IdfUnavailableError,
    design_intensity_mm_h,
    fetch_idf_table,
    nearest_idf_station,
    parse_idf_txt,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "idf"
OTTAWA_TXT = FIXTURES / "idf_v-3.20_2021_03_26_610_ON_6106000_OTTAWA_MACDONALD-CARTIER_INT_L_A.txt"
REGINA_TXT = FIXTURES / "idf_v-3.20_2021_03_26_401_SK_4016699_REGINA_RCS.txt"

OTTAWA = IdfStation(
    station_id="6106000",
    name="OTTAWA MACDONALD-CARTIER INT'L A",
    province="ON",
    lat=45.3225,
    lon=-75.66917,
    url="https://example.invalid/IDF_v-3.20_2021_03_26_ON.zip",
    zip_member="IDF_v-3.20_2021_03_26_ON/"
    "idf_v-3.20_2021_03_26_610_ON_6106000_OTTAWA_MACDONALD-CARTIER_INT_L_A.txt",
)
REGINA = IdfStation(
    station_id="4016699",
    name="REGINA RCS",
    province="SK",
    lat=50.43333,
    lon=-104.66667,
    url="https://example.invalid/IDF_v-3.20_2021_03_26_SK.zip",
    zip_member="IDF_v-3.20_2021_03_26_SK/idf_v-3.20_2021_03_26_401_SK_4016699_REGINA_RCS.txt",
)

ALL_DURATIONS_MIN = {5, 10, 15, 30, 60, 120, 360, 720, 1440}


def _ottawa_table() -> IdfTable:
    return parse_idf_txt(OTTAWA_TXT.read_text(encoding="latin-1"))


def _regina_table() -> IdfTable:
    return parse_idf_txt(REGINA_TXT.read_text(encoding="latin-1"))


# --- parsing -----------------------------------------------------------------


@pytest.mark.parametrize("load", [_ottawa_table, _regina_table])
def test_parse_intensity_table_shape(load):
    table = load()
    assert tuple(sorted(table.intensities_mm_h)) == IDF_RETURN_PERIODS
    for rp in IDF_RETURN_PERIODS:
        assert set(table.intensities_mm_h[rp]) == ALL_DURATIONS_MIN


def test_parse_known_values_from_table_2b():
    ottawa = _ottawa_table()
    assert ottawa.station_id == "6106000"
    assert ottawa.intensities_mm_h[2][5] == 103.3
    assert ottawa.intensities_mm_h[5][10] == 100.1
    assert ottawa.intensities_mm_h[100][1440] == 4.7
    regina = _regina_table()
    assert regina.station_id == "4016699"
    assert regina.intensities_mm_h[2][5] == 78.1
    assert regina.intensities_mm_h[100][5] == 249.5


@pytest.mark.parametrize("load", [_ottawa_table, _regina_table])
def test_intensities_physically_plausible(load):
    table = load()
    for rp in IDF_RETURN_PERIODS:
        curve = table.intensities_mm_h[rp]
        assert curve[5] > curve[1440], "5-min intensity must exceed 24-h intensity"
    for duration in ALL_DURATIONS_MIN:
        assert table.intensities_mm_h[100][duration] > table.intensities_mm_h[2][duration]


def test_parse_power_law_coefficients():
    ottawa = _ottawa_table()
    assert ottawa.coefficients[5] == (28.9, -0.698)
    regina = _regina_table()
    assert regina.coefficients[2] == (17.4, -0.693)
    assert set(ottawa.coefficients) == set(IDF_RETURN_PERIODS)


def test_parse_rejects_text_without_intensity_table():
    with pytest.raises(IdfUnavailableError):
        parse_idf_txt("not an IDF file\nTable 2a : something else\n")


# --- design intensity ----------------------------------------------------------


def test_design_intensity_matches_tabulated_value():
    table = _ottawa_table()
    # Power-law fit at tc=10 min, T=5 yr vs the tabulated 100.1 mm/h (fit error ~8%).
    assert design_intensity_mm_h(table, 10, return_period=5) == pytest.approx(100.1, rel=0.10)


def test_design_intensity_interpolates_monotonically_between_durations():
    table = _ottawa_table()
    bare = IdfTable(table.station_id, table.intensities_mm_h, {})  # force log-log path
    at_5, at_10 = bare.intensities_mm_h[5][5], bare.intensities_mm_h[5][10]
    assert design_intensity_mm_h(bare, 5, 5) == at_5
    assert design_intensity_mm_h(bare, 10, 5) == at_10
    between = design_intensity_mm_h(bare, 7.5, 5)
    assert at_10 < between < at_5


def test_design_intensity_clamps_tc_into_tabulated_range():
    table = _ottawa_table()
    assert design_intensity_mm_h(table, 2, 5) == design_intensity_mm_h(table, 5, 5)
    assert design_intensity_mm_h(table, 9999, 5) == design_intensity_mm_h(table, 1440, 5)


def test_design_intensity_rejects_unknown_return_period():
    with pytest.raises(ValueError):
        design_intensity_mm_h(_ottawa_table(), 10, return_period=7)


# --- nearest station ------------------------------------------------------------


def test_nearest_station_with_injected_index():
    index = [OTTAWA, REGINA]
    assert nearest_idf_station(45.42, -75.70, index=index).station_id == "6106000"
    assert nearest_idf_station(50.45, -104.62, index=index).station_id == "4016699"


def test_nearest_station_rejects_empty_index():
    with pytest.raises(ValueError):
        nearest_idf_station(45.0, -75.0, index=[])


def test_bundled_index_covers_canada():
    ottawa = nearest_idf_station(45.42, -75.70)
    assert ottawa.province == "ON"
    assert ottawa.url.startswith("https://collaboration.cmc.ec.gc.ca/")
    assert ottawa.zip_member.endswith(".txt")
    regina = nearest_idf_station(50.45, -104.62)
    assert regina.station_id == "4016699"


# --- fetch (fake ranged client over an in-memory province zip) -------------------


class FakeRangeClient:
    def __init__(self, blob: bytes):
        self.blob = blob
        self.calls = 0

    def get_bytes(self, url, start, end):
        self.calls += 1
        if start is None:
            return self.blob[-end:]
        return self.blob[start : end + 1]


class RaisingClient:
    def get_bytes(self, url, start, end):
        raise RuntimeError("network down")


def _province_zip(member: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("some/other_station.txt", b"decoy")
        z.writestr(member, payload)
    return buf.getvalue()


def test_fetch_extracts_member_via_range_requests():
    client = FakeRangeClient(_province_zip(OTTAWA.zip_member, OTTAWA_TXT.read_bytes()))
    table = fetch_idf_table(OTTAWA, client=client)
    assert table.station_id == "6106000"
    assert table.intensities_mm_h[5][10] == 100.1
    assert client.calls == 4  # EOCD tail + central directory + local header + member bytes


def test_fetch_uses_cache_and_skips_network(tmp_path):
    client = FakeRangeClient(_province_zip(OTTAWA.zip_member, OTTAWA_TXT.read_bytes()))
    first = fetch_idf_table(OTTAWA, cache_dir=tmp_path, client=client)
    assert (tmp_path / "6106000.txt").exists()
    # Second call must be served from cache: a client that would explode is never used.
    second = fetch_idf_table(OTTAWA, cache_dir=tmp_path, client=RaisingClient())
    assert second == first


def test_fetch_network_failure_raises_typed_error():
    with pytest.raises(IdfUnavailableError):
        fetch_idf_table(OTTAWA, client=RaisingClient())


def test_fetch_missing_member_raises_typed_error():
    client = FakeRangeClient(_province_zip("some/wrong_member.txt", b"x"))
    with pytest.raises(IdfUnavailableError):
        fetch_idf_table(OTTAWA, client=client)
