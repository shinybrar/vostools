"""Test one workspace package from its built wheel, outside the workspace.

Usage: ``python .github/scripts/wheel_gate.py <package> <python>``

The package is built with ``uv build --no-sources``, its wheel is checked to ship no tests,
and it is installed with its ``dev`` dependency group into a fresh virtual environment.
A copy of ``libs/<package>/tests`` then runs against the installed wheel, so a dependency
that is used but not declared fails here, and every console script must answer ``--help``.
"""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ISOLATION_VARIABLES = ("PYTHONHOME", "PYTHONPATH", "UV_PROJECT", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV")


def run(command: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    """Echo and run a command, failing on a non-zero exit."""
    print("+", " ".join(command), flush=True)  # noqa: T201
    subprocess.run(command, cwd=cwd, env=env, check=True)  # noqa: S603


def isolated_environment(home: Path) -> dict[str, str]:
    """Return the current environment without workspace or user-site leakage."""
    env = {key: value for key, value in os.environ.items() if key not in ISOLATION_VARIABLES}
    env.update({"HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"})
    return env


def only(paths: list[Path], what: str) -> Path:
    """Return the single path in ``paths``."""
    if len(paths) != 1:
        message = f"expected one {what}, found {paths}"
        raise RuntimeError(message)
    return paths[0]


def check_wheel(wheel: Path) -> list[str]:
    """Fail if the wheel ships tests; return its console script names."""
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        shipped_tests = [name for name in names if "/tests/" in name or name.startswith("tests/")]
        if shipped_tests:
            message = f"{wheel.name} ships tests: {shipped_tests}"
            raise RuntimeError(message)
        entry_points = [name for name in names if name.endswith(".dist-info/entry_points.txt")]
        if not entry_points:
            return []
        parser = configparser.ConfigParser()
        parser.read_string(archive.read(entry_points[0]).decode())
    return list(parser["console_scripts"]) if parser.has_section("console_scripts") else []


def main(package: str, python: str) -> None:
    """Build, install and test ``package`` with the given Python version."""
    uv = shutil.which("uv")
    if uv is None:
        message = "uv is not on PATH"
        raise RuntimeError(message)
    package_root = REPOSITORY_ROOT / "libs" / package
    with tempfile.TemporaryDirectory(prefix=f"wheel-gate-{package}-") as scratch:
        root = Path(scratch)
        env = isolated_environment(root / "home")
        (root / "home").mkdir()
        dist = root / "dist"
        run([uv, "build", "--no-sources", "--package", package, "--out-dir", str(dist)], cwd=REPOSITORY_ROOT, env=env)
        wheel = only(sorted(dist.glob("*.whl")), "wheel")
        only(sorted(dist.glob("*.tar.gz")), "sdist")
        scripts = check_wheel(wheel)

        venv = root / "venv"
        run([uv, "venv", "--no-project", "--python", python, str(venv)], cwd=root, env=env)
        venv_python = str(venv / "bin" / "python")
        group = f"{package_root / 'pyproject.toml'}:dev"
        run([uv, "pip", "install", "--python", venv_python, str(wheel), "--group", group], cwd=root, env=env)
        run([uv, "pip", "check", "--python", venv_python], cwd=root, env=env)

        project = root / "project"
        shutil.copytree(package_root / "tests", project / "tests")
        shutil.copy2(package_root / "pyproject.toml", project / "pyproject.toml")
        run([venv_python, "-m", "pytest", "-p", "no:cacheprovider"], cwd=project, env=env)

        for script in scripts:
            run([str(venv / "bin" / script), "--help"], cwd=root, env=env)


if __name__ == "__main__":
    if len(sys.argv) != 3:  # noqa: PLR2004
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
