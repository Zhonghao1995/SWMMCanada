"""Service discovery (ADR 0030): every supported city must be reachable or named as a gap."""
import pytest

from swmmcanada.audit.discover import (EXTERNAL_REFERENCE, NON_ARCGIS, PER_SERVICE_PROXY,
                                       fleet, is_enumerable, service_roots)
from swmmcanada.sources.cities.registry import CITIES

SUPPORTED = [c.key for c in CITIES]


def test_fleet_covers_supported_cities_plus_external_references():
    f = fleet()
    assert set(SUPPORTED) <= set(f)
    assert set(EXTERNAL_REFERENCE) <= set(f)


@pytest.mark.parametrize("city", SUPPORTED)
def test_every_city_is_either_discoverable_or_a_named_gap(city):
    """No city may fall out of the audit silently. Either the adapter exposes service URLs
    we can enumerate, or the city is listed in NON_ARCGIS with the reason why not."""
    if city in NON_ARCGIS:
        assert NON_ARCGIS[city], f"{city} listed as non-ArcGIS without a reason"
        return
    roots = service_roots(city)
    assert roots, (f"{city}: no service roots discovered and not declared in NON_ARCGIS — "
                   f"it would vanish from the fleet report")


def test_discovery_reads_urls_from_the_adapter_not_a_copy():
    """Single source of truth: the URLs come back out of the adapter module, so a city
    migrating servers cannot leave a stale duplicate behind."""
    import swmmcanada.sources.cities.victoria as victoria
    roots = service_roots("victoria")
    assert victoria.SEWER_BASE in roots
    assert victoria.BASE in roots


def test_layer_id_suffixes_are_trimmed_to_the_service():
    """Adapters reference `.../FeatureServer/0`; the audit must enumerate the whole service,
    because finding the layers we do *not* already consume is the point.

    Roots are a mix by design: services (enumerable directly) plus the catalogue folders
    they sit in (walked for siblings). Neither may keep a layer-id suffix."""
    roots = service_roots("kingston")
    assert roots
    for root in roots:
        assert not root.rstrip("/").split("/")[-1].isdigit(), root
    assert any(is_enumerable(r) for r in roots), "at least one root must be a service"


def test_per_service_proxy_cities_are_flagged():
    """Kingston's catalogue cannot be walked (one opaque hash per service), so its counts
    are a floor, not a census. That caveat must be declared, not discovered later."""
    assert "kingston" in PER_SERVICE_PROXY and PER_SERVICE_PROXY["kingston"]


def test_hamilton_is_audited_as_an_external_reference():
    """The only known Level 1 candidate anywhere. Excluding it risks concluding no
    instances exist and shipping an authoritative-source branch with no test subject."""
    assert "hamilton" in EXTERNAL_REFERENCE
    assert EXTERNAL_REFERENCE["hamilton"]


def test_adapter_referenced_services_bypass_the_keyword_filter():
    """Municipal catalogues do not name things the way an outsider guesses: London serves
    its whole storm network from `OpenData_Environment`, which matches no drainage keyword.
    Filtering adapter-referenced services by name cost the city 41 of its 43 layers."""
    from swmmcanada.audit.scanner import _relevant
    import swmmcanada.sources.cities.london as london
    assert not _relevant("OpenData_Environment"), "premise: the keyword filter rejects it"
    assert london.BASE in service_roots("london"), "so it must be reached by reference"


def test_sibling_services_in_the_same_folder_are_walked():
    """Adapters name only what they consume, so scanning exactly that finds only what we
    already use. Victoria references 3 services and publishes 16 — its curbs, curb drops
    and sidewalks (26,809 / 54,331 / 68,165 features, the Level 3 raw material) live in a
    service no adapter mentions."""
    import swmmcanada.sources.cities.victoria as victoria
    roots = service_roots("victoria")
    folder = victoria.BASE.rsplit("/", 2)[0]
    assert folder in roots, "the parent catalogue folder must be walked too"
    assert not is_enumerable(folder), "premise: it is a folder, not a service"
