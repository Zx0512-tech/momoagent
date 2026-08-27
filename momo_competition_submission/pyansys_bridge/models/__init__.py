"""Shared data contracts for bridge analysis workflows."""

from .analysis_result import AnalysisResult
from .bridge_model import BridgeModel
from .damper_layout import (
    DamperPlacement,
    RealizableDamper,
    common_physical_count,
    physical_count_as_int,
    split_total_damper_params,
    tower_girder_layout,
    with_physical_count,
)
from .damper_params import DamperParams
from .load_case import (
    LoadCase,
    LoadCaseTemplateContext,
    LoadPointMapping,
    TimeHistoryLoad,
    load_point_mappings_from_csv,
    write_load_point_mappings_csv,
)

__all__ = [
    "AnalysisResult",
    "BridgeModel",
    "DamperParams",
    "DamperPlacement",
    "LoadCase",
    "LoadCaseTemplateContext",
    "LoadPointMapping",
    "RealizableDamper",
    "TimeHistoryLoad",
    "common_physical_count",
    "load_point_mappings_from_csv",
    "physical_count_as_int",
    "split_total_damper_params",
    "tower_girder_layout",
    "with_physical_count",
    "write_load_point_mappings_csv",
]
