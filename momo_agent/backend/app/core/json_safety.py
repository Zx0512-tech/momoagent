from __future__ import annotations

import json
import math
from typing import Any


def coerce_non_finite_floats(value: Any) -> Any:
    """递归将非有限浮点数转换为可移植 JSON 的 null。"""

    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: coerce_non_finite_floats(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [coerce_non_finite_floats(item) for item in value]
    return value


def strict_json_dumps(value: Any, *, indent: int | None = None, sort_keys: bool = False) -> str:
    return json.dumps(
        coerce_non_finite_floats(value),
        ensure_ascii=False,
        indent=indent,
        sort_keys=sort_keys,
        allow_nan=False,
    )
