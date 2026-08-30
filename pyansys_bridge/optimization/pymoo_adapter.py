"""pymoo-backed NSGA-II optimizer adapter."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import csv

import numpy as np
from pymoo.algorithms.moo.nsga2 import NSGA2
from pymoo.core.problem import ElementwiseProblem
from pymoo.operators.crossover.sbx import SBX
from pymoo.operators.mutation.pm import PM
from pymoo.operators.sampling.rnd import FloatRandomSampling
from pymoo.optimize import minimize
from pymoo.termination import get_termination

from pyansys_bridge.optimization.objectives import objective_value


Evaluator = Callable[[np.ndarray], Mapping[str, Any]]
PRODUCTION_NSGA2_OPTIMIZER = "pymoo_nsga2"


@dataclass(frozen=True)
class PymooOptimizationResult:
    """Result returned by the pymoo NSGA-II adapter."""

    parameter_names: tuple[str, ...]
    objective_names: tuple[str, ...]
    candidates: np.ndarray
    objectives: np.ndarray
    pareto_designs: np.ndarray
    pareto_objectives: np.ndarray
    pareto_constraint_violations: np.ndarray
    csv_paths: dict[str, Path] = field(default_factory=dict)

    def to_legacy_dict(self) -> dict[str, np.ndarray]:
        return {
            "designs": self.pareto_designs,
            "objectives": self.pareto_objectives,
            "parameter_names": np.array(self.parameter_names),
        }


class _PymooEvaluatorProblem(ElementwiseProblem):
    def __init__(
        self,
        *,
        bounds: dict[str, tuple[float, float]],
        evaluator: Evaluator,
        objective_names: tuple[str, ...],
        constraints: dict[str, float],
    ) -> None:
        self.parameter_names = tuple(bounds)
        self.objective_names = tuple(objective_names)
        self.constraint_names = tuple(constraints)
        self.constraint_limits = dict(constraints)
        self.evaluator = evaluator
        self.evaluations: list[dict[str, Any]] = []
        xl = np.array([bounds[name][0] for name in self.parameter_names], dtype=float)
        xu = np.array([bounds[name][1] for name in self.parameter_names], dtype=float)
        super().__init__(
            n_var=len(self.parameter_names),
            n_obj=len(self.objective_names),
            n_ieq_constr=len(self.constraint_names),
            xl=xl,
            xu=xu,
        )

    def _evaluate(self, x, out, *args, **kwargs) -> None:
        design = np.asarray(x, dtype=float)
        payload = self.evaluator(design)
        objectives = np.array([objective_value(payload, name) for name in self.objective_names], dtype=float)
        out["F"] = objectives
        constraints = np.array(
            [
                _extract_constraint_value(payload, name) - float(self.constraint_limits[name])
                for name in self.constraint_names
            ],
            dtype=float,
        )
        if self.constraint_names:
            out["G"] = constraints
        self.evaluations.append(
            {
                "design": design.copy(),
                "objectives": objectives.copy(),
                "constraints": constraints.copy(),
            }
        )


def optimize_with_pymoo_nsga2(
    *,
    bounds: dict[str, tuple[float, float]],
    evaluator: Evaluator,
    objective_names: tuple[str, ...],
    constraints: dict[str, float] | None = None,
    population_size: int = 100,
    generations: int = 100,
    seed: int | None = None,
    crossover: float | dict[str, float] | Any | None = None,
    mutation: float | dict[str, float] | Any | None = None,
    output_dir: str | Path | None = None,
) -> PymooOptimizationResult:
    """Run pymoo NSGA-II and return Pareto designs/objectives.

    The evaluator receives one design vector and may return either a flat
    objective mapping or a mapping with nested ``objectives`` and
    ``constraints`` dictionaries.
    """

    if not bounds:
        raise ValueError("At least one design variable bound is required")
    if not objective_names:
        raise ValueError("At least one objective name is required")
    if population_size <= 0:
        raise ValueError("population_size must be positive")
    if generations <= 0:
        raise ValueError("generations must be positive")
    _validate_bounds(bounds)

    problem = _PymooEvaluatorProblem(
        bounds=bounds,
        evaluator=evaluator,
        objective_names=tuple(objective_names),
        constraints=dict(constraints or {}),
    )
    algorithm = NSGA2(
        pop_size=int(population_size),
        sampling=FloatRandomSampling(),
        crossover=_crossover_operator(crossover),
        mutation=_mutation_operator(mutation),
        eliminate_duplicates=True,
    )
    result = minimize(
        problem,
        algorithm,
        get_termination("n_gen", int(generations)),
        seed=seed,
        verbose=False,
    )
    pareto_designs = _as_2d(result.X, len(problem.parameter_names))
    pareto_objectives = _as_2d(result.F, len(problem.objective_names))
    pareto_constraints = _constraint_violations(result.G, pareto_designs.shape[0])
    candidates = np.array([record["design"] for record in problem.evaluations], dtype=float)
    objectives = np.array([record["objectives"] for record in problem.evaluations], dtype=float)

    csv_paths = (
        _write_result_csvs(
            output_dir=Path(output_dir),
            parameter_names=problem.parameter_names,
            objective_names=problem.objective_names,
            pareto_designs=pareto_designs,
            pareto_objectives=pareto_objectives,
        )
        if output_dir is not None
        else {}
    )
    return PymooOptimizationResult(
        parameter_names=problem.parameter_names,
        objective_names=problem.objective_names,
        candidates=candidates,
        objectives=objectives,
        pareto_designs=pareto_designs,
        pareto_objectives=pareto_objectives,
        pareto_constraint_violations=pareto_constraints,
        csv_paths=csv_paths,
    )


def optimize_with_production_nsga2(**kwargs) -> PymooOptimizationResult:
    """Run the production NSGA-II optimizer.

    This stable entrypoint keeps pymoo as the production path while older
    helpers in ``nsga2.py`` remain lightweight utilities or compatibility
    wrappers.
    """

    return optimize_with_pymoo_nsga2(**kwargs)


def _validate_bounds(bounds: dict[str, tuple[float, float]]) -> None:
    for name, (lower, upper) in bounds.items():
        if float(lower) > float(upper):
            raise ValueError(f"Lower bound exceeds upper bound for {name}")


def _extract_constraint_value(payload: Mapping[str, Any], name: str) -> float:
    nested = payload.get("constraints")
    if isinstance(nested, Mapping) and name in nested:
        return float(nested[name])
    return objective_value(payload, name)


def _crossover_operator(crossover):
    if crossover is None:
        return SBX(prob=0.9, eta=15)
    if isinstance(crossover, (int, float)):
        return SBX(prob=float(crossover), eta=15)
    if isinstance(crossover, Mapping):
        return SBX(prob=float(crossover.get("prob", 0.9)), eta=float(crossover.get("eta", 15)))
    return crossover


def _mutation_operator(mutation):
    if mutation is None:
        return PM(eta=20)
    if isinstance(mutation, (int, float)):
        return PM(prob=float(mutation), eta=20)
    if isinstance(mutation, Mapping):
        prob = mutation.get("prob")
        kwargs = {"eta": float(mutation.get("eta", 20))}
        if prob is not None:
            kwargs["prob"] = float(prob)
        return PM(**kwargs)
    return mutation


def _as_2d(values, column_count: int) -> np.ndarray:
    if values is None:
        return np.empty((0, column_count), dtype=float)
    array = np.asarray(values, dtype=float)
    if array.size == 0:
        return np.empty((0, column_count), dtype=float)
    if array.ndim == 1:
        return array.reshape(1, -1)
    return array


def _constraint_violations(values, row_count: int) -> np.ndarray:
    if values is None:
        return np.zeros(row_count, dtype=float)
    matrix = np.asarray(values, dtype=float)
    if matrix.size == 0:
        return np.zeros(row_count, dtype=float)
    if matrix.ndim == 1:
        matrix = matrix.reshape(-1, 1)
    return np.maximum(matrix, 0.0).sum(axis=1)


def _write_result_csvs(
    *,
    output_dir: Path,
    parameter_names: tuple[str, ...],
    objective_names: tuple[str, ...],
    pareto_designs: np.ndarray,
    pareto_objectives: np.ndarray,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "pareto_front": output_dir / "pareto_front.csv",
        "design_variables": output_dir / "design_variables.csv",
        "objective_values": output_dir / "objective_values.csv",
    }
    _write_table(paths["pareto_front"], parameter_names + objective_names, np.column_stack([pareto_designs, pareto_objectives]))
    _write_table(paths["design_variables"], parameter_names, pareto_designs)
    _write_table(paths["objective_values"], objective_names, pareto_objectives)
    return paths


def _write_table(path: Path, headers: tuple[str, ...], rows: np.ndarray) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows.tolist())
