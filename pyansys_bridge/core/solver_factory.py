"""Solver factory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pyansys_bridge.core.ansys_solver import AnsysSolver
from pyansys_bridge.core.mock_solver import MockSolver
from pyansys_bridge.core.openseespy_inproc_solver import OpenSeesPyInProcSolver
from pyansys_bridge.core.opensees_solver import OpenSeesSolver
from pyansys_bridge.core.solver_interface import SolverInterface

PRODUCTION_SOLVER_TYPES = ("ansys", "openseespy_inproc")


@dataclass(frozen=True)
class SolverConfig:
    type: str
    kwargs: dict[str, Any]
    requires: tuple[str, ...] = ()

    @classmethod
    def from_mapping(
        cls,
        config: dict[str, Any] | str | None,
        *,
        legacy_kwargs: dict[str, Any] | None = None,
    ) -> "SolverConfig":
        if config is None:
            raise ValueError("solver.type is required when solver config is omitted")
        if isinstance(config, str):
            return cls(type=config, kwargs=dict(legacy_kwargs or {}))
        if "solver" in config:
            return cls.from_mapping(config.get("solver"), legacy_kwargs=config.get("solver_kwargs"))
        solver = dict(config)
        raw_type = solver.pop("type", None)
        if raw_type is None:
            raw_type = solver.pop("name", None)
        if raw_type is None:
            raise ValueError("solver.type is required when solver config is a mapping")
        requires = tuple(str(feature) for feature in solver.pop("requires", ()))
        nested_kwargs = solver.pop("kwargs", None)
        if nested_kwargs is not None and not isinstance(nested_kwargs, dict):
            raise TypeError("solver.kwargs must be a mapping")
        kwargs = dict(nested_kwargs or {})
        kwargs.update(solver)
        if legacy_kwargs:
            kwargs.update(legacy_kwargs)
        return cls(type=str(raw_type), kwargs=kwargs, requires=requires)


class SolverFactory:
    """Create solver adapters by name."""

    production_solver_types = PRODUCTION_SOLVER_TYPES

    @staticmethod
    def create(name: str | dict[str, Any] | None, **kwargs) -> SolverInterface:
        if not isinstance(name, str):
            return SolverFactory.create_from_config(name, **kwargs)
        normalized = name.lower()
        if normalized == "mock":
            return MockSolver()
        if normalized == "ansys":
            return AnsysSolver(**kwargs)
        if normalized == "opensees":
            return OpenSeesSolver(**kwargs)
        if normalized == "openseespy_inproc":
            return OpenSeesPyInProcSolver(**kwargs)
        raise ValueError(f"Unsupported solver: {name}")

    @staticmethod
    def create_from_config(config: dict[str, Any] | str | None, **overrides) -> SolverInterface:
        solver_config = SolverConfig.from_mapping(config)
        kwargs = dict(solver_config.kwargs)
        kwargs.update(overrides)
        solver = SolverFactory.create(solver_config.type, **kwargs)
        solver.require_features(solver_config.requires)
        return solver

    @staticmethod
    def capability(name: str) -> dict[str, object]:
        return SolverFactory.create(name).solver_capability()
