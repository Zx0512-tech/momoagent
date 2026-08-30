"""Active learning optimization utilities."""

from .acquisition import expected_improvement, weighted_expected_improvement
from .active_learner import ActiveLearner
from .strategy import allocate_infill_budget_by_uncertainty, select_active_learning_points

__all__ = [
    "ActiveLearner",
    "allocate_infill_budget_by_uncertainty",
    "expected_improvement",
    "select_active_learning_points",
    "weighted_expected_improvement",
]
