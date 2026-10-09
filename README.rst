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


Checkstyle
~~~~~~~~~~
flake8 style checking is enforced on pull requests. Following commands should
not report errors

::

    uvx flake8 libs/vos/src libs/vos/tests --max-line-length 120 --extend-exclude libs/vos/tests/integration
