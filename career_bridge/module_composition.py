from __future__ import annotations

from typing import Any


def module_exports(module_globals: dict[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {name: module_globals[name] for name in names}


def activate_module(module_globals: dict[str, Any], namespace: dict[str, Any]) -> None:
    module_globals.update(namespace)
