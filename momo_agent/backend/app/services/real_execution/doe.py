"""确定性的初始 DOE 设计集生成器。"""

from __future__ import annotations

import json
from hashlib import sha256
from random import Random
from typing import Mapping


def generate_two_factor_doe(
    bounds: Mapping[str, tuple[float, float]],
    *,
    count: int,
    seed: int,
    minimum: int = 5,
    maximum: int = 24,
) -> list[dict[str, float]]:
    """生成中心点、四角点和固定种子分层 LHS。

    两因子模板的前五点保持历史顺序，因而 15 点结果与旧 Agent 兼容；其余
    数量只改变追加的 LHS 点，不依赖全局随机状态。
    """

    if not minimum <= count <= maximum:
        raise ValueError(f"DOE 初始设计数必须在 {minimum}–{maximum} 之间")
    names = tuple(bounds)
    if len(names) != 2:
        raise ValueError("当前确定性 DOE 模板要求恰好两个设计因子")
    first, second = names
    first_low, first_high = (float(value) for value in bounds[first])
    second_low, second_high = (float(value) for value in bounds[second])
    if not first_low < first_high or not second_low < second_high:
        raise ValueError("DOE 每个因子的上下界必须严格递增")

    designs = [
        {first: (first_low + first_high) / 2.0, second: (second_low + second_high) / 2.0},
        {first: first_low, second: second_low},
        {first: first_low, second: second_high},
        {first: first_high, second: second_low},
        {first: first_high, second: second_high},
    ]
    lhs_count = count - len(designs)
    if lhs_count == 0:
        return designs
    rng = Random(seed)
    first_points = [
        first_low + ((index + rng.random()) / lhs_count) * (first_high - first_low)
        for index in range(lhs_count)
    ]
    second_points = [
        second_low + ((index + rng.random()) / lhs_count) * (second_high - second_low)
        for index in range(lhs_count)
    ]
    rng.shuffle(first_points)
    rng.shuffle(second_points)
    designs.extend(
        {first: float(first_value), second: float(second_value)}
        for first_value, second_value in zip(first_points, second_points)
    )
    return designs


def design_set_sha256(designs: list[Mapping[str, float]]) -> str:
    """计算与报告中一致的设计集哈希。"""

    payload = json.dumps(
        [{str(key): float(value) for key, value in design.items()} for design in designs],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()
