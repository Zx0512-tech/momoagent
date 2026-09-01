"""Decision explainability and robustness reporting."""

from __future__ import annotations

from pathlib import Path
import json

import numpy as np

from pyansys_bridge.optimization.decision import topsis_details
from pyansys_bridge.optimization.workflow import SurrogateOptimizationResult


def explain_topsis_decision(
    objectives: np.ndarray,
    *,
    objective_names: tuple[str, ...] | list[str] | None = None,
    weights: np.ndarray | None = None,
) -> dict[str, object]:
    """Return a JSON-friendly TOPSIS audit summary for minimization objectives."""

    matrix = np.asarray(objectives, dtype=float)
    result = topsis_details(matrix, weights=weights)
    names = _objective_names(objective_names, matrix.shape[1])
    return {
        "method": "topsis_minimization",
        "objective_names": list(names),
        "best_index": int(result.best_index),
        "ranking": [int(index) for index in result.ranking],
        "weights": _array_to_list(result.weights),
        "normalized_objectives": _matrix_to_named_rows(result.normalized, names),
        "normalization": {
            "method": result.normalization_method,
            "objective_min": _array_to_named_dict(result.normalization_min, names),
            "objective_span": _array_to_named_dict(result.normalization_span, names),
        },
        "weighted_objectives": _matrix_to_named_rows(result.weighted, names),
        "distance_to_ideal": _array_to_list(result.distance_to_ideal),
        "distance_to_nadir": _array_to_list(result.distance_to_nadir),
        "closeness": _array_to_list(result.closeness),
        "component_contributions": _component_contributions(result.weighted, names),
    }


def evaluate_decision_robustness(
    result: SurrogateOptimizationResult,
    surrogates: list[object] | tuple[object, ...],
    *,
    top_n: int = 3,
    relative_delta: float = 0.1,
    output_dir: str | Path = "outputs/decision",
) -> dict[str, object]:
    """Evaluate top TOPSIS candidates under plus/minus parameter perturbations."""

    if top_n <= 0:
        raise ValueError("top_n must be positive")
    if relative_delta < 0:
        raise ValueError("relative_delta cannot be negative")

    pareto_designs = np.asarray(result.pareto_designs, dtype=float)
    pareto_objectives = np.asarray(result.pareto_objectives, dtype=float)
    if pareto_designs.ndim != 2 or pareto_objectives.ndim != 2:
        raise ValueError("Pareto designs and objectives must be 2D arrays")
    if pareto_designs.shape[0] != pareto_objectives.shape[0]:
        raise ValueError("Pareto designs and objectives must have the same row count")
    if len(surrogates) != pareto_objectives.shape[1]:
        raise ValueError("surrogate count must match objective count")

    topsis = topsis_details(pareto_objectives, weights=result.decision_weights)
    candidate_reports = []
    for pareto_index in topsis.ranking[: min(top_n, pareto_designs.shape[0])]:
        design = pareto_designs[int(pareto_index)]
        perturbations = perturb_design(
            design,
            result.parameter_names,
            relative_delta=relative_delta,
        )
        predictions = _predict_objectives(perturbations, surrogates)
        base = _predict_objectives(design.reshape(1, -1), surrogates)[0]
        statistics = _objective_statistics(predictions, result.objective_names)
        candidate_reports.append(
            {
                "pareto_index": int(pareto_index),
                "design_parameters": {
                    name: float(value)
                    for name, value in zip(result.parameter_names, design)
                },
                "base_objectives": {
                    name: float(value)
                    for name, value in zip(result.objective_names, base)
                },
                "perturbation_count": int(perturbations.shape[0]),
                "perturbed_designs": [
                    {
                        name: float(value)
                        for name, value in zip(result.parameter_names, row)
                    }
                    for row in perturbations
                ],
                "statistics": statistics,
                "max_worsening_rate": _max_worsening_rate(predictions, base),
            }
        )

    recommended = min(
        candidate_reports,
        key=lambda item: (
            float(item["max_worsening_rate"]),
            sum(float(stats["mean"]) for stats in item["statistics"].values()),
        ),
    )
    report = {
        "method": "surrogate_parameter_perturbation",
        "relative_delta": float(relative_delta),
        "topsis": explain_topsis_decision(
            pareto_objectives,
            objective_names=result.objective_names,
            weights=result.decision_weights,
        ),
        "candidate_reports": candidate_reports,
        "recommended": {
            "pareto_index": int(recommended["pareto_index"]),
            "design_parameters": dict(recommended["design_parameters"]),
            "max_worsening_rate": float(recommended["max_worsening_rate"]),
        },
    }
    target = Path(output_dir) / "decision_explainability.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    report["output_path"] = str(target)
    return report


def perturb_design(
    design: np.ndarray,
    parameter_names: tuple[str, ...] | list[str],
    *,
    relative_delta: float = 0.1,
) -> np.ndarray:
    """Return baseline plus plus/minus perturbations for each design parameter."""

    values = np.asarray(design, dtype=float).reshape(-1)
    if values.shape[0] != len(parameter_names):
        raise ValueError("design length must match parameter_names")
    rows = [values.copy()]
    for index, value in enumerate(values):
        for factor in (1.0 - relative_delta, 1.0 + relative_delta):
            perturbed = values.copy()
            perturbed[index] = value * factor
            rows.append(perturbed)
    return np.asarray(rows, dtype=float)


def _predict_objectives(designs: np.ndarray, surrogates: list[object] | tuple[object, ...]) -> np.ndarray:
    columns = []
    for surrogate in surrogates:
        prediction = surrogate.predict(designs)
        if isinstance(prediction, tuple):
            prediction = prediction[0]
        values = np.asarray(prediction, dtype=float).reshape(-1)
        if values.shape[0] != designs.shape[0]:
            raise ValueError("surrogate prediction length must match design count")
        columns.append(values)
    return np.column_stack(columns)


def _objective_statistics(predictions: np.ndarray, names: tuple[str, ...]) -> dict[str, dict[str, float]]:
    return {
        name: {
            "mean": float(np.mean(values)),
            "std": float(np.std(values)),
            "max": float(np.max(values)),
            "min": float(np.min(values)),
        }
        for name, values in zip(names, predictions.T)
    }


def _max_worsening_rate(predictions: np.ndarray, base: np.ndarray) -> float:
    denominator = np.maximum(np.abs(base), 1.0e-12)
    worsening = (predictions - base) / denominator
    return float(np.max(worsening))


def _component_contributions(weighted: np.ndarray, names: tuple[str, ...]) -> list[dict[str, float]]:
    distance_from_nadir = np.max(weighted, axis=0) - weighted
    totals = np.maximum(np.sum(distance_from_nadir, axis=1), 1.0e-12)
    shares = distance_from_nadir / totals[:, None]
    return _matrix_to_named_rows(shares, names)


def _matrix_to_named_rows(matrix: np.ndarray, names: tuple[str, ...]) -> list[dict[str, float]]:
    return [
        {
            name: float(value)
            for name, value in zip(names, row)
        }
        for row in np.asarray(matrix, dtype=float)
    ]


def _objective_names(names: tuple[str, ...] | list[str] | None, column_count: int) -> tuple[str, ...]:
    if names is None:
        return tuple(f"objective_{index + 1}" for index in range(column_count))
    result = tuple(str(name) for name in names)
    if len(result) != column_count:
        raise ValueError("objective_names length must match objective columns")
    return result


def _array_to_list(values: np.ndarray) -> list[float]:
    return np.asarray(values, dtype=float).reshape(-1).tolist()


def _array_to_named_dict(values: np.ndarray, names: tuple[str, ...]) -> dict[str, float]:
    return {
        name: float(value)
        for name, value in zip(names, np.asarray(values, dtype=float).reshape(-1))
    }
