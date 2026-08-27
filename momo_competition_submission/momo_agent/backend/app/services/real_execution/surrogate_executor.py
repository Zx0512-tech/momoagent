"""真实 surrogate 训练与可重放证据。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import numpy as np

from pyansys_bridge.surrogate.dataset import dataset_from_result_store
from pyansys_bridge.surrogate.selector import select_best_surrogate
from pyansys_bridge.surrogate.base import load_surrogate_model


class SurrogateExecutionError(RuntimeError):
    """真实训练或重放校验失败。"""


@dataclass(frozen=True)
class SurrogateTrainingRequest:
    result_root: Path
    target_name: str
    output_dir: Path
    feature_names: tuple[str, ...] = ("c", "alpha")
    model_names: tuple[str, ...] = ()
    case_ids: tuple[str, ...] | None = None
    cv: int | str = "auto"


@dataclass(frozen=True)
class SurrogateTrainingResult:
    model_path: Path
    model_sha256: str
    dataset_sha256: str
    model_name: str
    feature_names: tuple[str, ...]
    target_name: str
    sample_count: int
    cv_metrics: dict[str, dict[str, float]]
    reload_reproducible: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "modelPath": str(self.model_path),
            "modelSha256": self.model_sha256,
            "datasetSha256": self.dataset_sha256,
            "modelName": self.model_name,
            "featureNames": list(self.feature_names),
            "targetName": self.target_name,
            "sampleCount": self.sample_count,
            "cvMetrics": self.cv_metrics,
            "reloadReproducible": self.reload_reproducible,
        }


def train_surrogate(request: SurrogateTrainingRequest) -> SurrogateTrainingResult:
    """从真实结果目录训练并保存可重放 surrogate。"""

    dataset = dataset_from_result_store(
        request.result_root,
        request.target_name,
        feature_names=request.feature_names,
        case_ids=request.case_ids,
    )
    if not np.isfinite(dataset.x).all() or not np.isfinite(dataset.y).all():
        raise SurrogateExecutionError("训练数据包含非有限值")
    names = request.model_names or ("gpr",)
    try:
        selection = select_best_surrogate(
            dataset,
            candidate_names=tuple(names),
            cv=request.cv,
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise SurrogateExecutionError(f"surrogate 训练失败: {exc}") from exc
    for model_name, metrics in (selection.cv_metrics or {}).items():
        if not all(math.isfinite(float(value)) for value in metrics.values()):
            raise SurrogateExecutionError(f"{model_name} 的 CV 指标包含非有限值")

    request.output_dir.mkdir(parents=True, exist_ok=True)
    model_path = request.output_dir / f"{selection.name}.pkl"
    selection.model.save(model_path)
    raw = model_path.read_bytes()
    reloaded = load_surrogate_model(model_path)
    original_prediction = np.asarray(selection.model.predict(dataset.x), dtype=float)
    reloaded_prediction = np.asarray(reloaded.predict(dataset.x), dtype=float)
    reproducible = bool(np.allclose(original_prediction, reloaded_prediction, rtol=1e-10, atol=1e-12))
    if not reproducible:
        raise SurrogateExecutionError("模型 reload 后预测不一致")
    return SurrogateTrainingResult(
        model_path=model_path,
        model_sha256=sha256(raw).hexdigest(),
        dataset_sha256=_dataset_sha256(dataset),
        model_name=selection.name,
        feature_names=tuple(dataset.feature_names),
        target_name=dataset.target_name,
        sample_count=int(dataset.x.shape[0]),
        cv_metrics={
            str(name): {str(key): float(value) for key, value in metrics.items()}
            for name, metrics in (selection.cv_metrics or {}).items()
        },
        reload_reproducible=reproducible,
    )


def _dataset_sha256(dataset) -> str:
    payload = {
        "featureNames": list(dataset.feature_names),
        "targetName": dataset.target_name,
        "x": np.asarray(dataset.x, dtype=float).tolist(),
        "y": np.asarray(dataset.y, dtype=float).tolist(),
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
