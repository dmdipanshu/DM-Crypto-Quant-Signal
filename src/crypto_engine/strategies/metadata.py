"""Strategy metadata, versioning, and parameter definitions.

Pure stdlib. Strategies are identified by a deterministic id derived from
their name, version, and parameter hash. No I/O, no network.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StrategyParameter:
    """A single strategy parameter with name, type, default, and bounds."""

    name: str
    param_type: str  # "int", "float", "str", "bool"
    default: Any
    min_value: Any | None = None
    max_value: Any | None = None

    def validate(self, value: Any) -> Any:
        """Validate and coerce a parameter value. Raises ValueError on failure."""
        if self.param_type == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"parameter {self.name!r} must be int, got {type(value).__name__}")
            if self.min_value is not None and value < self.min_value:
                raise ValueError(f"parameter {self.name!r} below min {self.min_value}")
            if self.max_value is not None and value > self.max_value:
                raise ValueError(f"parameter {self.name!r} above max {self.max_value}")
            return value
        if self.param_type == "float":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ValueError(
                    f"parameter {self.name!r} must be numeric, got {type(value).__name__}"
                )
            value = float(value)
            if self.min_value is not None and value < self.min_value:
                raise ValueError(f"parameter {self.name!r} below min {self.min_value}")
            if self.max_value is not None and value > self.max_value:
                raise ValueError(f"parameter {self.name!r} above max {self.max_value}")
            return value
        if self.param_type == "str":
            if not isinstance(value, str):
                raise ValueError(f"parameter {self.name!r} must be str, got {type(value).__name__}")
            return value
        if self.param_type == "bool":
            if not isinstance(value, bool):
                raise ValueError(
                    f"parameter {self.name!r} must be bool, got {type(value).__name__}"
                )
            return value
        raise ValueError(f"parameter {self.name!r} has unknown type {self.param_type!r}")


@dataclass(frozen=True)
class StrategyVersion:
    """Strategy version: major.minor.patch with optional tag."""

    major: int
    minor: int
    patch: int
    tag: str = ""

    def __post_init__(self) -> None:
        if self.major < 0 or self.minor < 0 or self.patch < 0:
            raise ValueError("version components must be non-negative")

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return f"{base}-{self.tag}" if self.tag else base

    def to_tuple(self) -> tuple[int, int, int, str]:
        return (self.major, self.minor, self.patch, self.tag)


@dataclass(frozen=True)
class StrategyMetadata:
    """Strategy identification: deterministic id, name, version, parameters."""

    name: str
    version: StrategyVersion
    parameters: tuple[StrategyParameter, ...] = ()
    description: str = ""

    def strategy_id(self) -> str:
        """Deterministic id: sha256 over name + version + sorted param definitions."""
        h = hashlib.sha256()
        h.update(self.name.encode("utf-8"))
        h.update(str(self.version.to_tuple()).encode("utf-8"))
        for p in sorted(self.parameters, key=lambda p: p.name):
            h.update(p.name.encode("utf-8"))
            h.update(p.param_type.encode("utf-8"))
            h.update(repr(p.default).encode("utf-8"))
        return f"strat-{h.hexdigest()[:16]}"

    def validate_parameters(self, values: dict[str, Any]) -> dict[str, Any]:
        """Validate all parameter values against their definitions.

        Returns a dict of validated values. Raises ValueError on failure.
        """
        validated: dict[str, Any] = {}
        for param in self.parameters:
            if param.name not in values:
                validated[param.name] = param.default
            else:
                validated[param.name] = param.validate(values[param.name])
        return validated


__all__ = ["StrategyMetadata", "StrategyParameter", "StrategyVersion"]
