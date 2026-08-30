"""共享真实执行能力的契约、执行器与注册表。"""

from app.services.real_execution.contracts import CapabilityCatalog, CapabilityDescriptor, RealJobRequest
from app.services.real_execution.active_learning_executor import (
    ActiveLearningRequest,
    ActiveLearningResult,
    select_infill_designs,
)
from app.services.real_execution.executor import (
    ConfigExecutionRequest,
    ConfigExecutionResult,
    RealExecutionCancelled,
    RealExecutionError,
    RealExecutionTimeout,
    RealSolverExecutor,
    ResultCatalogEntry,
    SolverExecutionRequest,
    SolverExecutionResult,
)
from app.services.real_execution.doe import design_set_sha256, generate_two_factor_doe
from app.services.real_execution.surrogate_executor import (
    SurrogateExecutionError,
    SurrogateTrainingRequest,
    SurrogateTrainingResult,
    train_surrogate,
)
from app.services.real_execution.registry import RealExecutionRegistry, real_execution_registry

__all__ = [
    'CapabilityCatalog',
    'CapabilityDescriptor',
    'ConfigExecutionRequest',
    'ConfigExecutionResult',
    'RealExecutionCancelled',
    'RealExecutionError',
    'RealExecutionTimeout',
    'RealJobRequest',
    'ActiveLearningRequest',
    'ActiveLearningResult',
    'RealExecutionRegistry',
    'RealSolverExecutor',
    'ResultCatalogEntry',
    'SolverExecutionRequest',
    'SolverExecutionResult',
    'SurrogateExecutionError',
    'SurrogateTrainingRequest',
    'SurrogateTrainingResult',
    'design_set_sha256',
    'generate_two_factor_doe',
    'train_surrogate',
    'select_infill_designs',
    'real_execution_registry',
]
