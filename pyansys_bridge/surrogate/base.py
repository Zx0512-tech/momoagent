"""Common surrogate model interface."""

from __future__ import annotations

from pathlib import Path
import pickle

import numpy as np


class SurrogateModel:
    """Minimal interface shared by surrogate regressors."""

    model_name = "surrogate"

    def fit(self, x: np.ndarray, y: np.ndarray, **kwargs):
        raise NotImplementedError

    def predict(self, x: np.ndarray, **kwargs):
        raise NotImplementedError

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as handle:
            pickle.dump(self, handle)
        return target

    @classmethod
    def load(cls, path: str | Path):
        with Path(path).open("rb") as handle:
            model = pickle.load(handle)
        if not isinstance(model, SurrogateModel):
            raise TypeError(f"Serialized object is not a SurrogateModel: {type(model).__name__}")
        return model


def load_surrogate_model(path: str | Path) -> SurrogateModel:
    return SurrogateModel.load(path)
