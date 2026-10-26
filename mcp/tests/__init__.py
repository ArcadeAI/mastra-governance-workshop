"""The tests for the one server, one package per tool file.

`tools_of` is how a file's tests see only their own tools: `app._catalog` holds
every tool the server registers, and a test about the four loan tools has to
be a test about those four, not about whatever else is catalogued.
"""

from __future__ import annotations

from types import ModuleType

from loan_desk import app


def tools_of(module: ModuleType) -> list:
    """The materialized tools `module` defines, in catalogue order."""
    return [tool for tool in app._catalog if tool.tool.__module__ == module.__name__]
