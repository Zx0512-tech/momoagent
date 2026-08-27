"""Registry for unified surrogate models."""

from __future__ import annotations

from collections.abc import Callable

from .base import SurrogateModel, load_surrogate_model
from .gpr_model import GaussianProcessRegressor
from .power_law_model import PowerLawRegressor
from .rbf_model import RBFRegressor
from .sklearn_models import (
    create_kriging_model,
    create_log_c_cubic_ridge_model,
    create_mars_model,
    create_pce_model,
    create_response_surface_model,
    create_svr_model,
)

ModelFactory = Callable[..., SurrogateModel]

PRODUCTION_SURROGATE_MODEL_NAMES: tuple[str, ...] = (
    "gpr",
    "kriging",
    "rbf",
    "response_surface",
    "svr",
    "pce",
    "mars",
    "power_law",
    "log_c_cubic_ridge",
)


MODEL_ZOO: dict[str, ModelFactory] = {
    "gpr": GaussianProcessRegressor,
    "kriging": create_kriging_model,
    "log_c_cubic_ridge": create_log_c_cubic_ridge_model,
    "mars": create_mars_model,
    "pce": create_pce_model,
    "power_law": PowerLawRegressor,
    "rbf": RBFRegressor,
    "response_surface": create_response_surface_model,
    "svr": create_svr_model,
}


def register_surrogate_model(name: str, factory: ModelFactory) -> None:
    key = _normalize_name(name)
    if not key:
        raise ValueError("model name cannot be empty")
    MODEL_ZOO[key] = factory


def create_surrogate_model(name: str, **kwargs) -> SurrogateModel:
    key = _normalize_name(name)
    try:
        factory = MODEL_ZOO[key]
    except KeyError as exc:
        raise ValueError(f"Unsupported surrogate model: {name}. Available: {', '.join(surrogate_model_names())}") from exc
    return factory(**kwargs)


def create_response_surrogate_models(config: dict[str, str | dict]) -> dict[str, SurrogateModel]:
    models: dict[str, SurrogateModel] = {}
    for response_name, model_config in config.items():
        if isinstance(model_config, str):
            models[response_name] = create_surrogate_model(model_config)
            continue
        if not isinstance(model_config, dict):
            raise TypeError(f"Model config for {response_name} must be a string or mapping")
        payload = dict(model_config)
        model_type = payload.pop("type", payload.pop("model", None))
        if model_type is None:
            raise ValueError(f"Model config for {response_name} requires type")
        models[response_name] = create_surrogate_model(str(model_type), **payload)
    return models


def surrogate_model_names() -> list[str]:
    return sorted(MODEL_ZOO)


def baseline_surrogate_model_names() -> tuple[str, ...]:
    """Return interpretable and flexible baseline candidates for small DOE studies."""

    return PRODUCTION_SURROGATE_MODEL_NAMES


def _normalize_name(name: str) -> str:
    return str(name).strip().lower().replace("-", "_")


__all__ = [
    "MODEL_ZOO",
    "PRODUCTION_SURROGATE_MODEL_NAMES",
    "baseline_surrogate_model_names",
    "create_response_surrogate_models",
    "create_surrogate_model",
    "load_surrogate_model",
    "register_surrogate_model",
    "surrogate_model_names",
]
