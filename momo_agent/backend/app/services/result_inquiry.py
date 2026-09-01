from __future__ import annotations

import csv
import io
import json
import math
from collections import OrderedDict
from hashlib import sha256
from threading import Lock
from typing import Any, Protocol


MAX_ROWS = 500_000
MAX_BYTES = 64 * 1024 * 1024
ALLOWED_KINDS = {'CSV_TIMESERIES', 'CSV_TABLE'}
TOPSIS_KINDS = {'JSON_SUMMARY', 'OPTIMIZATION_REPORT'}

# 解析缓存：制品内容不可变且 sha256 是现成的完整性键。持久循环一条消息
# 里模型可连发多次 result.* 查询同一制品，没有缓存时每次都要重算
# 最大 64MB 的 SHA256 并重新解析最多 50 万行 CSV。
# 缓存值（header, rows）是共享只读结构，消费方只做读取和新建列表。
_PARSE_CACHE_MAX_ENTRIES = 4
_PARSE_CACHE: OrderedDict[tuple[str, str], tuple[list[str], list[list[float | None]]]] = OrderedDict()
_PARSE_CACHE_LOCK = Lock()


class InquiryArtifactStore(Protocol):
    artifacts: list[Any]

    def get_artifact(self, artifact_id: str) -> Any: ...


class ResultInquiryError(ValueError):
    """追问查询层的结构化错误。"""


class ResultInquiryService:
    """只读已登记 CSV 与优化摘要；不写入任何数据。"""

    def __init__(self, store: InquiryArtifactStore) -> None:
        self.store = store

    def _load(self, artifact_id: str) -> tuple[list[str], list[list[float | None]]]:
        try:
            record = self.store.get_artifact(artifact_id)
        except Exception as exc:
            raise ResultInquiryError(f'结果制品不存在: {artifact_id}') from exc
        artifact = record.artifact
        if artifact.kind not in ALLOWED_KINDS:
            raise ResultInquiryError(f'追问只接受已登记 CSV 结果，收到 {artifact.kind}')
        if len(record.content) > MAX_BYTES:
            raise ResultInquiryError('结果文件超出追问上限')
        cache_key = (str(artifact_id), str(artifact.sha256))
        with _PARSE_CACHE_LOCK:
            cached = _PARSE_CACHE.get(cache_key)
            if cached is not None:
                _PARSE_CACHE.move_to_end(cache_key)
                return cached
        if sha256(record.content).hexdigest() != artifact.sha256:
            raise ResultInquiryError('结果内容 SHA256 与登记元数据不一致')
        try:
            text = record.content.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise ResultInquiryError('结果文件不是有效 UTF-8 CSV') from exc
        reader = csv.reader(io.StringIO(text, newline=''))
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ResultInquiryError('结果文件为空') from exc
        header = [name.strip() for name in header]
        if not header or not any(header):
            raise ResultInquiryError('结果文件缺少表头')
        rows: list[list[float | None]] = []
        for index, raw in enumerate(reader):
            if index >= MAX_ROWS:
                break
            rows.append([self._to_float(cell) for cell in raw])
        if not rows:
            raise ResultInquiryError('结果文件没有数据行')
        with _PARSE_CACHE_LOCK:
            _PARSE_CACHE[cache_key] = (header, rows)
            _PARSE_CACHE.move_to_end(cache_key)
            while len(_PARSE_CACHE) > _PARSE_CACHE_MAX_ENTRIES:
                _PARSE_CACHE.popitem(last=False)
        return header, rows

    @staticmethod
    def _to_float(cell: str) -> float | None:
        try:
            value = float(cell)
        except (TypeError, ValueError):
            return None
        return value if math.isfinite(value) else None

    def columns(self, artifact_id: str) -> list[str]:
        header, _ = self._load(artifact_id)
        return header

    def topsis(self, artifact_id: str, *, limit: int = 10) -> dict[str, Any]:
        """读取已登记优化摘要中的确定性 TOPSIS 排名，不触发重新计算。"""
        if limit <= 0 or limit > 50:
            raise ResultInquiryError('TOPSIS 查询条数必须在 1 到 50 之间')
        try:
            record = self.store.get_artifact(artifact_id)
        except Exception as exc:
            raise ResultInquiryError(f'结果制品不存在: {artifact_id}') from exc
        artifact = record.artifact
        if artifact.kind not in TOPSIS_KINDS:
            raise ResultInquiryError(f'制品不是优化摘要 JSON，收到 {artifact.kind}')
        if len(record.content) > MAX_BYTES:
            raise ResultInquiryError('优化摘要超出追问上限')
        if sha256(record.content).hexdigest() != artifact.sha256:
            raise ResultInquiryError('优化摘要 SHA256 与登记元数据不一致')
        try:
            payload = json.loads(record.content.decode('utf-8-sig'))
            optimization = payload['optimization']
            topsis = optimization['topsis']
            objective_names = [str(item) for item in optimization['objective_names']]
            solutions = list(optimization['pareto_solutions'])
            ranking = [int(item) for item in topsis['ranking']]
            closeness = [float(item) for item in topsis['closeness']]
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResultInquiryError('优化摘要缺少可查询的 TOPSIS 排名') from exc
        if not ranking or len(ranking) != len(closeness):
            raise ResultInquiryError('TOPSIS 排名数据不完整')
        rows: list[dict[str, Any]] = []
        for rank, pareto_index in enumerate(ranking[:limit], start=1):
            if pareto_index < 0 or pareto_index >= len(solutions):
                raise ResultInquiryError('TOPSIS 排名引用了不存在的 Pareto 候选')
            solution = solutions[pareto_index]
            if not isinstance(solution, dict):
                raise ResultInquiryError('TOPSIS 候选格式无效')
            rows.append({
                'rank': rank,
                'paretoIndex': pareto_index,
                'score': closeness[pareto_index],
                'parameters': dict(
                    solution.get('design')
                    or solution.get('parameters')
                    or solution.get('design_parameters')
                    or {}
                ),
                'objectives': dict(
                    solution.get('objectives')
                    or solution.get('objective_values')
                    or {}
                ),
            })
        return {
            'objectiveNames': objective_names,
            'rows': rows,
            'availableCount': len(ranking),
            'weights': list(topsis.get('weights') or optimization.get('decision_weights') or []),
        }

    def sweep_cases(
        self,
        artifact_id: str,
        *,
        metric: str | None = None,
        order: str = 'asc',
        limit: int = 64,
    ) -> dict[str, Any]:
        """跨算例聚合查询：按指标排序批量/对比算例，并给出参数敏感性。

        只读取已登记的批量计算或对比汇总 JSON（caseResults），不触发重算。
        敏感性使用同一 damperType 分组内参数与指标的 Pearson 相关，
        只描述单调同步趋势，不构成因果或最优结论。
        """
        if limit <= 0 or limit > 64:
            raise ResultInquiryError('跨算例查询条数必须在 1 到 64 之间')
        if order not in {'asc', 'desc'}:
            raise ResultInquiryError("排序方向必须是 'asc' 或 'desc'")
        payload = self._load_json_summary(artifact_id)
        raw_cases = payload.get('caseResults')
        if not isinstance(raw_cases, list) or not raw_cases:
            raise ResultInquiryError('汇总制品不包含 caseResults，无法跨算例聚合')
        cases: list[dict[str, Any]] = [case for case in raw_cases if isinstance(case, dict)]
        available_metrics = sorted({
            str(name)
            for case in cases
            for name, value in (case.get('objectives') or {}).items()
            if isinstance(value, (int, float)) and math.isfinite(float(value))
        })
        if not available_metrics:
            raise ResultInquiryError('caseResults 不包含可聚合的数值指标')
        selected_metric = metric or available_metrics[0]
        if selected_metric not in available_metrics:
            raise ResultInquiryError(
                f'指标 {selected_metric} 不存在，可选: {", ".join(available_metrics)}'
            )

        scored: list[dict[str, Any]] = []
        for case in cases:
            objectives = case.get('objectives') or {}
            value = objectives.get(selected_metric)
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                continue
            scored.append({
                'caseId': str(case.get('caseId') or ''),
                'damperType': case.get('damperType'),
                'parameters': dict(case.get('parameters') or {}),
                'value': float(value),
                'isVerifiedSolverOutput': bool(case.get('isVerifiedSolverOutput')),
            })
        if not scored:
            raise ResultInquiryError(f'没有算例包含指标 {selected_metric} 的有效数值')
        scored.sort(key=lambda item: item['value'], reverse=(order == 'desc'))
        rows = [{'rank': rank, **item} for rank, item in enumerate(scored[:limit], start=1)]

        sensitivity: list[dict[str, Any]] = []
        by_type: dict[str, list[dict[str, Any]]] = {}
        for item in scored:
            by_type.setdefault(str(item['damperType'] or ''), []).append(item)
        for damper_type, group in sorted(by_type.items()):
            parameter_names = sorted({
                str(name)
                for item in group
                for name, value in item['parameters'].items()
                if isinstance(value, (int, float)) and math.isfinite(float(value))
            })
            for name in parameter_names:
                pairs = [
                    (float(item['parameters'][name]), item['value'])
                    for item in group
                    if isinstance(item['parameters'].get(name), (int, float))
                    and math.isfinite(float(item['parameters'][name]))
                ]
                pearson = self._pearson(pairs)
                if pearson is None:
                    continue
                sensitivity.append({
                    'damperType': damper_type,
                    'parameter': name,
                    'pearson': pearson,
                    'sampleCount': len(pairs),
                })
        return {
            'metric': selected_metric,
            'order': order,
            'rows': rows,
            'caseCount': len(scored),
            'availableMetrics': available_metrics,
            'sensitivity': sensitivity,
            'interpretationLimit': '排序与敏感性只反映已计算算例内的同步趋势，不构成因果或全局最优结论。',
        }

    def _load_json_summary(self, artifact_id: str) -> dict[str, Any]:
        try:
            record = self.store.get_artifact(artifact_id)
        except Exception as exc:
            raise ResultInquiryError(f'结果制品不存在: {artifact_id}') from exc
        artifact = record.artifact
        if artifact.kind not in TOPSIS_KINDS:
            raise ResultInquiryError(f'制品不是结果汇总 JSON，收到 {artifact.kind}')
        if len(record.content) > MAX_BYTES:
            raise ResultInquiryError('结果汇总超出追问上限')
        if sha256(record.content).hexdigest() != artifact.sha256:
            raise ResultInquiryError('结果汇总 SHA256 与登记元数据不一致')
        try:
            payload = json.loads(record.content.decode('utf-8-sig'))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ResultInquiryError('结果汇总不是有效 JSON') from exc
        if not isinstance(payload, dict):
            raise ResultInquiryError('结果汇总必须是 JSON 对象')
        return payload

    @staticmethod
    def _pearson(pairs: list[tuple[float, float]]) -> float | None:
        if len(pairs) < 3:
            return None
        n = len(pairs)
        mean_a = sum(a for a, _ in pairs) / n
        mean_b = sum(b for _, b in pairs) / n
        cov = sum((a - mean_a) * (b - mean_b) for a, b in pairs)
        var_a = sum((a - mean_a) ** 2 for a, _ in pairs)
        var_b = sum((b - mean_b) ** 2 for _, b in pairs)
        if var_a <= 0 or var_b <= 0:
            return None
        return round(cov / math.sqrt(var_a * var_b), 4)

    def downsample(
        self,
        artifact_id: str,
        *,
        columns: list[str],
        max_points: int,
    ) -> dict[str, list[float | None]]:
        """按时间桶保留幅值极值，压缩曲线时不丢失峰值。"""
        if max_points <= 0:
            raise ResultInquiryError('max_points 必须为正数')
        header, rows = self._load(artifact_id)
        selected = list(dict.fromkeys(columns))
        if not selected:
            raise ResultInquiryError('至少需要选择一列结果')
        series = {name: self._series(header, rows, name) for name in selected}
        total = len(rows)
        if total <= max_points:
            return series

        bucket = max(1, math.ceil(total / max_points))
        value_columns = [name for name in selected if name != 'time'] or [selected[0]]
        output = {name: [] for name in selected}
        picks: list[int] = []
        for start in range(0, total, bucket):
            end = min(start + bucket, total)
            candidates: list[tuple[int, float]] = []
            for name in value_columns:
                values = series[name]
                window = [
                    (index, values[index])
                    for index in range(start, end)
                    if values[index] is not None
                ]
                if window:
                    candidates.append(max(window, key=lambda item: abs(item[1])))
            pick = max(candidates, key=lambda item: abs(item[1]))[0] if candidates else start
            picks.append(pick)

        # 额外锁定每一列的全局峰值；替换非峰值桶代表点时仍保持 max_points 上限。
        mandatory: list[int] = []
        for name in value_columns:
            values = series[name]
            valid = [
                (index, value)
                for index, value in enumerate(values)
                if value is not None
            ]
            if valid:
                mandatory.append(max(valid, key=lambda item: abs(item[1]))[0])
        picks = list(dict.fromkeys([*picks, *mandatory]))
        if len(picks) > max_points:
            mandatory_set = set(mandatory)
            kept = [index for index in picks if index in mandatory_set]
            kept.extend(index for index in picks if index not in mandatory_set)
            picks = kept[:max_points]
        picks.sort()
        for pick in picks:
            for name in selected:
                output[name].append(series[name][pick])
        return output

    def _series(
        self,
        header: list[str],
        rows: list[list[float | None]],
        column: str,
    ) -> list[float | None]:
        try:
            index = header.index(column)
        except ValueError as exc:
            raise ResultInquiryError(f'结果中不存在列 {column}') from exc
        return [row[index] if index < len(row) else None for row in rows]

    def peak(
        self,
        artifact_id: str,
        *,
        column: str,
        time_column: str = 'time',
    ) -> dict[str, Any]:
        """返回绝对值峰值、发生时刻、以及有符号的原值。"""
        header, rows = self._load(artifact_id)
        values = self._series(header, rows, column)
        times = self._series(header, rows, time_column) if time_column in header else None
        best_index = None
        for index, value in enumerate(values):
            if value is None:
                continue
            if best_index is None or abs(value) > abs(values[best_index]):
                best_index = index
        if best_index is None:
            raise ResultInquiryError(f'列 {column} 没有有效数值')
        return {
            'column': column,
            'peakAbsolute': abs(values[best_index]),
            'peakSigned': values[best_index],
            'peakTime': times[best_index] if times else None,
            'sampleCount': len(values),
        }

    def at_time(
        self,
        artifact_id: str,
        *,
        columns: list[str],
        target_time: float,
        time_column: str = 'time',
    ) -> dict[str, Any]:
        """取最接近 target_time 那一行的指定列值。"""
        if not math.isfinite(target_time):
            raise ResultInquiryError('目标时刻必须是有限数值')
        header, rows = self._load(artifact_id)
        times = self._series(header, rows, time_column)
        best_index = None
        for index, value in enumerate(times):
            if value is None:
                continue
            if best_index is None or abs(value - target_time) < abs(times[best_index] - target_time):
                best_index = index
        if best_index is None:
            raise ResultInquiryError(f'列 {time_column} 没有有效数值')
        series = {name: self._series(header, rows, name) for name in columns}
        return {
            'requestedTime': target_time,
            'matchedTime': times[best_index],
            'values': {name: values[best_index] for name, values in series.items()},
        }

    def correlate(
        self,
        artifact_id: str,
        *,
        column_a: str,
        column_b: str,
    ) -> dict[str, Any]:
        """Pearson 相关系数；只描述同步关系，不代表因果。"""
        header, rows = self._load(artifact_id)
        left = self._series(header, rows, column_a)
        right = self._series(header, rows, column_b)
        pairs = [
            (a, b) for a, b in zip(left, right)
            if a is not None and b is not None
        ]
        if len(pairs) < 3:
            raise ResultInquiryError('有效样本不足，无法计算相关性')
        n = len(pairs)
        mean_a = sum(a for a, _ in pairs) / n
        mean_b = sum(b for _, b in pairs) / n
        cov = sum((a - mean_a) * (b - mean_b) for a, b in pairs)
        var_a = sum((a - mean_a) ** 2 for a, _ in pairs)
        var_b = sum((b - mean_b) ** 2 for _, b in pairs)
        if var_a <= 0 or var_b <= 0:
            raise ResultInquiryError('存在常量序列，无法计算相关性')
        return {
            'columnA': column_a,
            'columnB': column_b,
            'pearson': round(cov / math.sqrt(var_a * var_b), 4),
            'sampleCount': n,
            'interpretationLimit': '相关性只描述同步变化，不构成因果结论。',
        }

    def compare(
        self,
        artifact_id: str,
        *,
        column_a: str,
        column_b: str,
    ) -> dict[str, Any]:
        """比较两列绝对峰值；百分比由查询层确定性计算，不交给 LLM。"""
        left = self.peak(artifact_id, column=column_a)
        right = self.peak(artifact_id, column=column_b)
        left_value = float(left['peakAbsolute'])
        right_value = float(right['peakAbsolute'])
        difference = right_value - left_value
        relative = None if left_value == 0 else difference / abs(left_value)
        return {
            'columnA': column_a,
            'columnB': column_b,
            'leftPeakAbsolute': left_value,
            'rightPeakAbsolute': right_value,
            'difference': round(difference, 6),
            'relativeChange': None if relative is None else round(relative, 6),
            'relativeChangePercent': None if relative is None else round(relative * 100.0, 6),
            'ratio': None if left_value == 0 else round(right_value / left_value, 6),
            'sampleCount': min(int(left['sampleCount']), int(right['sampleCount'])),
            'interpretationLimit': '差值和相对变化只描述两列峰值差异，不构成因果结论。',
        }

    def delta(self, artifact_id: str, *, column_a: str, column_b: str) -> dict[str, Any]:
        """返回两列绝对峰值差值及相对变化。"""
        return self.compare(artifact_id, column_a=column_a, column_b=column_b)

    def ratio(self, artifact_id: str, *, column_a: str, column_b: str) -> dict[str, Any]:
        """返回两列绝对峰值比值及相对变化。"""
        return self.compare(artifact_id, column_a=column_a, column_b=column_b)
