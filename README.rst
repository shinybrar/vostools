VOS - VOSpace tools


.. image:: https://img.shields.io/pypi/pyversions/vos.svg
    :target: https://pypi.python.org/pypi/vos

.. image:: https://github.com/opencadc/vostools/workflows/CI/badge.svg?branch=master&event=schedule
    :target: https://github.com/opencadc/vostools/actions?query=event%3Aschedule+

.. image:: https://codecov.io/gh/opencadc/vostools/branch/master/graph/badge.svg
  :target: https://codecov.io/gh/opencadc/vostools

.. image:: https://img.shields.io/github/contributors/opencadc/vostools.svg
    :target: https://github.com/opencadc/vostools/graphs/contributors


Tools to work with VOSpace services (primarily the CADC ones)..


Developers Guide
================


Requires `uv <https://docs.astral.sh/uv/>`__. The repository is a uv workspace; each package lives under
``libs/<package>``.

Installing Packages
-------------------

::

    uv sync --locked --all-packages

Testing packages
----------------

Testing vos
~~~~~~~~~~~

::

    uv run --package vos pytest libs/vos/tests

To test with a specific version of Python (uv downloads it if needed):

::

    uv run --package vos -p 3.14 pytest libs/vos/tests

The live integration tests need CADC credentials and are not collected by default:

::

    uv run --package vos pytest libs/vos/tests/integration


Linting, type checking and commit messages
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The root ``pyproject.toml`` holds the shared ruff, ty and commitizen configuration; ``libs/vos`` extends it
with a narrower rule set while it is brought up to the shared policy. Install the git hooks once, then run
them on demand:

::

    uv run pre-commit install
    uv run pre-commit run --all-files

Commit messages and pull request titles follow `Conventional Commits <https://www.conventionalcommits.org/>`__
with the scopes ``vos``, ``vosfs``, ``fss-cli`` or ``repo``, for example
``fix(vos): handle missing node properties``.

Continuous integration
~~~~~~~~~~~~~~~~~~~~~~
``quality.yml`` always runs the hooks, builds every package and checks distribution metadata; its
``Required`` job is the only check branch protection needs. Each package has its own workflow
(``ci-vos.yml``, and later ``ci-vosfs.yml`` / ``ci-fss-cli.yml``) that GitHub starts only when that
package's tree or a shared workspace file changes. Those workflows test on Python 3.10 to 3.14
(and macOS on 3.12).
