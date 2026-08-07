"""Feature-focused route registration for the Application Builder blueprint."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

RouteNamespace = dict[str, Any]
RouteRegistrar = Callable[[RouteNamespace], Mapping[str, Any]]


def merge_exports(namespace: RouteNamespace, exports: Mapping[str, Any]) -> None:
    """Expose shared route helpers to subsequently registered feature modules."""

    namespace.update(exports)
