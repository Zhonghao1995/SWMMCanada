"""One version, four declarations — keep them from drifting (maturity review point 10).

The release flow bumps CITATION.cff; this test makes the other three declarations
(backend pyproject, frontend package.json, README's software-citation line) fail CI
loudly whenever a bump forgets one of them. CITATION.cff is the source of truth
because the release PR edits it by convention.
"""
import json
import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _citation_version() -> str:
    m = re.search(r"(?m)^version:\s*(\S+)", (REPO / "CITATION.cff").read_text())
    assert m, "CITATION.cff has no version: line"
    return m.group(1)


def test_backend_pyproject_matches_citation():
    pyproject = tomllib.loads((REPO / "backend" / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == _citation_version()


def test_frontend_package_json_matches_citation():
    pkg = json.loads((REPO / "frontend" / "package.json").read_text())
    assert pkg["version"] == _citation_version()


def test_readme_software_citation_matches_citation():
    m = re.search(r"\(Version (\d+\.\d+\.\d+)\)", (REPO / "README.md").read_text())
    assert m, "README has no '(Version x.y.z)' software-citation line"
    assert m.group(1) == _citation_version()
