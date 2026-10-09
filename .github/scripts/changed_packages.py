"""Select the workspace packages CI must test and write the job matrices.

Usage: ``python3 .github/scripts/changed_packages.py [base-ref]`` (Python 3.11 or later)

Without a base ref (pushes, the nightly run, dispatch, release) every package is selected.
With one, a package is selected when a file under ``libs/<package>/`` changed, when a shared
file changed, or when a workspace package it depends on is selected. The result goes to
``$GITHUB_OUTPUT`` when set and to stdout either way.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SHARED = re.compile(r"^(pyproject\.toml|uv\.lock|\.pre-commit-config\.yaml|\.github/|tests/repo/)")
TEST_CELLS = [("ubuntu-latest", python) for python in ("3.10", "3.11", "3.12", "3.13", "3.14")] + [
    ("macos-latest", "3.12"),
]
GATE_PYTHONS = ("3.10", "3.14")


def normalize(name: str) -> str:
    """Return the PEP 503 normalized form of a distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def workspace_packages() -> dict[str, set[str]]:
    """Map each package directory under ``libs/`` to the workspace packages it depends on."""
    projects = {}
    for pyproject in sorted(REPOSITORY_ROOT.glob("libs/*/pyproject.toml")):
        project = tomllib.loads(pyproject.read_text())["project"]
        requirements = {normalize(re.split(r"[\s<>=!~;\[(]", spec, maxsplit=1)[0]) for spec in project["dependencies"]}
        projects[pyproject.parent.name] = (normalize(project["name"]), requirements)
    names = {name: directory for directory, (name, _) in projects.items()}
    return {
        directory: {names[requirement] for requirement in requirements if requirement in names}
        for directory, (_, requirements) in projects.items()
    }


def changed_files(base: str) -> list[str]:
    """Return the paths changed between the merge base of ``base`` and HEAD."""
    output = subprocess.run(  # noqa: S603
        ["git", "diff", "--name-only", f"{base}...HEAD"],  # noqa: S607
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return output.splitlines()


def select(packages: dict[str, set[str]], files: list[str] | None) -> list[str]:
    """Return the sorted package directories to test for ``files`` (``None`` means everything)."""
    if files is None or any(SHARED.match(path) for path in files):
        return sorted(packages)
    selected = {path.split("/")[1] for path in files if path.startswith("libs/") and path.count("/") > 1}
    selected &= set(packages)
    grown = True
    while grown:
        dependents = {package for package, needs in packages.items() if needs & selected}
        grown = not dependents <= selected
        selected |= dependents
    return sorted(selected)


def main() -> None:
    """Compute the selection and write the job outputs."""
    packages = workspace_packages()
    files = changed_files(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] else None
    selected = select(packages, files)
    outputs = {
        "packages": selected,
        "test-matrix": {
            "include": [
                {"package": package, "os": os_name, "python": python}
                for package in selected
                for os_name, python in TEST_CELLS
            ],
        },
        "gate-matrix": {
            "include": [{"package": package, "python": python} for package in selected for python in GATE_PYTHONS],
        },
    }
    lines = [f"{key}={json.dumps(value, separators=(',', ':'))}" for key, value in outputs.items()]
    print("\n".join(lines))  # noqa: T201
    if "GITHUB_OUTPUT" in os.environ:
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as handle:
            handle.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
