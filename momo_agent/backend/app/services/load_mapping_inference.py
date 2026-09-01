"""通用表格荷载文件的列语义推断。

CSV/TXT/XLSX 不自描述：哪列是时间、哪列是数值、单位是什么，文件里没有
可靠声明。本模块从 inspect() 的列画像里推断出一份候选 mapping，连同置信
度和依据一起返回，交给审批环节让用户确认。

推断结果是建议，不是决定。整条链路的安全模型是"审批冻结 + SHA256 校验"
（见 agent_service._execute_standardization），推断只负责把表单预填好，
冻结值仍然来自用户确认后的 mapping。单位猜错会带来 9.8 倍甚至 980 倍的
荷载误差，所以低置信度必须显式暴露，不能静默生效。

自描述格式（PEER .AT2 等）不走这里，见 load_format_readers。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# 时间列判定的相对容差。真实记录的时间列常有 1e-6 量级的十进制舍入
# （0.01 步长写成 0.030000000000000002），过严会把合法时间列判成非等步长。
_TIME_STEP_RTOL = 1.0e-6

# 列名里的单位记号。表头声明比按量级猜可靠得多，优先用它。
# 顺序有意义：长记号在前，避免 "cm/s2" 的尾部被 "s" 之类短记号抢先匹配。
_UNIT_TOKENS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('mm/s2', ('mm/s2', 'mm/s^2', 'mm/sec2', 'mm/sec^2')),
    ('cm/s2', ('cm/s2', 'cm/s^2', 'cm/sec2', 'cm/sec^2', 'gal')),
    ('m/s2', ('m/s2', 'm/s^2', 'm/sec2', 'm/sec^2')),
    ('kN', ('kn',)),
    ('N', ('n',)),
    ('g', ('g',)),
)

_TIME_COLUMN_NAMES = frozenset({
    't', 'time', 'time_s', 'times', 'timestamp', 'sec', 'second', 'seconds',
    '时间', '时间(s)', '时间（s）', '时刻', '秒',
})

_ACCELERATION_NAME_HINTS = ('acc', 'accel', 'a_', 'ag', '加速度', '地震', 'quake', 'eq')

# 允许"猜"出来的加速度单位只有这两个：地震记录实际只用 g 和 m/s² 两种口径
# 交付，cm/s²（gal）与 mm/s² 虽然读得懂，但没有任何量级特征能把它们和另外
# 两个区分开——300 既可能是 gal 也可能是写错量纲的 m/s²。猜不准就别猜。
INFERABLE_ACCELERATION_UNITS = ('g', 'm/s2')
# 文件自己声明时可以采信的单位集合。声明是读出来的事实，不是推断，所以比
# 上面那组宽：PEER 头就会写 gal（见 load_format_readers）。
DECLARABLE_ACCELERATION_UNITS = ('g', 'm/s2', 'cm/s2', 'mm/s2')
# 力单位一个都不许猜。N 与 kN 差 1000 倍，可常见量级完全重叠——一个主梁节点上
# 800 N 与 800 kN 都是物理上讲得通的风荷载，没有任何量级特征能把两者分开。所以
# 只有"读出来的"这一档，没有对应的 INFERABLE_* 集合。
DECLARABLE_FORCE_UNITS = ('N', 'kN')
_FORCE_UNIT_SET = frozenset(DECLARABLE_FORCE_UNITS)

# 逐节点风荷载列名。通道与节点是位置绑定，绑定靠这里解析出的节点号，所以只认
# 明确的写法：宁可不认（退回人工映射），也不能把 "fy_1_of_8" 里的 1 当节点号。
# 节点号后允许一个括号单位记号（fy_node_1(N)），交给 unit_from_name 去读；下划线
# 后缀不认，那和节点号本身分不开。
_WIND_NODE_COLUMN_RE = re.compile(
    r'^(?:fy|f_y)_node_?(\d+)\s*(?:[（(\[][^）)\]]+[）)\]])?$',
    re.IGNORECASE,
)

# 逐节点车流荷载列名（node_1_fy_N、node_1_fy(kN)）。节点号紧跟 node_ 前缀，不存在
# 风那边的歧义；结尾允许一个字母记号（单位或标签），但不允许再出现数字——那和节点号
# 分不开。车流的通道与节点不是位置绑定，靠 mapping 制品逐条声明，这里只解析节点号。
_TRAFFIC_NODE_COLUMN_RE = re.compile(
    r'^node_?(\d+)_f_?y(?:_[A-Za-z/]+)?\s*(?:[（(\[][^）)\]]+[）)\]])?$',
    re.IGNORECASE,
)

# 车流标准矩阵制品的列名口径，与 platform_store._apply_agent_standard_traffic_load
# 的逐对校验（column == f'node_{node}_fy_N'）必须一致。
TRAFFIC_MATRIX_COLUMN_TEMPLATE = 'node_{node}_fy_N'
# 矩阵通道的合成列名：车流是单通道稠密矩阵，没有单独的“数值列”，用它占位。
TRAFFIC_MATRIX_VALUE_COLUMN = 'node_fy_N_matrix'

# 单位来源。区分"读出来的"和"猜出来的"是整个自动标准化决策的依据：
# 读出来的可以直接转，猜出来的必须回到审批界面让人确认。
UNIT_SOURCE_HEADER = 'DECLARED_IN_HEADER'
UNIT_SOURCE_COLUMN_NAME = 'DECLARED_IN_COLUMN_NAME'
UNIT_SOURCE_FILE_NAME = 'DECLARED_IN_FILE_NAME'
UNIT_SOURCE_PREAMBLE = 'DECLARED_IN_PREAMBLE'
UNIT_SOURCE_MAGNITUDE = 'GUESSED_FROM_MAGNITUDE'
UNIT_SOURCE_UNKNOWN = 'UNKNOWN'

# 文件内容里的声明才够格自动标准化。文件名不算——上传时改个名就变了，它
# 描述的是文件的标签而不是文件的内容。
_AUTO_TRUSTED_UNIT_SOURCES = frozenset({UNIT_SOURCE_HEADER, UNIT_SOURCE_COLUMN_NAME})

DECISION_AUTO = 'AUTO'
DECISION_ASK = 'ASK'


@dataclass(frozen=True)
class MappingSuggestion:
    """一份候选 mapping 及其依据。"""

    mapping: dict[str, Any]
    confidence: str
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    alternatives: dict[str, Any] = field(default_factory=dict)
    unit_source: str = UNIT_SOURCE_UNKNOWN
    standardize_decision: str = DECISION_ASK

    def to_dict(self) -> dict[str, Any]:
        return {
            'mapping': self.mapping,
            'confidence': self.confidence,
            'reasons': list(self.reasons),
            'warnings': list(self.warnings),
            'alternatives': dict(self.alternatives),
            'unitSource': self.unit_source,
            'standardizeDecision': self.standardize_decision,
        }


@dataclass(frozen=True)
class TimeColumnDetection:
    """时间列检测结果。column 为 None 表示没有可信的时间列。"""

    column: str | None
    step_s: float | None
    uniform: bool
    reason: str


def detect_time_column(
    column_profiles: list[dict[str, Any]],
    rows: list[dict[str, str]],
) -> TimeColumnDetection:
    """找出时间列，并给出等时间步长。

    不靠列名匹配，也不靠"前两行递增"这种两点判据——一个恰好递增的非时间列
    （节点编号、荷载步序号）会被误判。这里对整列做严格递增与等差检测，列名
    只在多个候选之间做优先级排序。
    """

    if len(rows) < 2:
        return TimeColumnDetection(None, None, False, 'ROW_COUNT_TOO_SMALL')

    candidates: list[tuple[int, str, float]] = []
    for profile in column_profiles:
        name = str(profile['name'])
        values = _numeric_column(rows, name)
        if values is None or len(values) < 2:
            continue
        if any(current <= previous for previous, current in zip(values, values[1:])):
            continue
        step = _uniform_step(values)
        if step is None:
            continue
        # 列名命中已知时间列名的排在前面；否则按列序，最左优先。
        named = 0 if name.strip().lower() in _TIME_COLUMN_NAMES else 1
        candidates.append((named, name, step))

    if not candidates:
        return TimeColumnDetection(None, None, False, 'NO_MONOTONIC_UNIFORM_COLUMN')

    # 文件里有列名明确写着时间、却没通过等步长检测时，不能改挑别的列顶上——
    # 那会拿一个无关列当时间轴，产出错误时程且照样带合法哈希。这里失败关闭，
    # 由用户修文件或显式给 time.stepS。
    has_named = any(
        str(profile['name']).strip().lower() in _TIME_COLUMN_NAMES
        for profile in column_profiles
    )
    if has_named and all(named for named, _, _ in candidates):
        return TimeColumnDetection(None, None, False, 'NAMED_TIME_COLUMN_NOT_UNIFORM')

    candidates.sort(key=lambda item: (item[0], _column_index(column_profiles, item[1])))
    _, name, step = candidates[0]
    return TimeColumnDetection(name, step, True, 'UNIFORM_INCREASING_COLUMN')


def infer_acceleration_unit(peak_abs: float) -> tuple[str | None, str, str]:
    """按峰值量级猜加速度单位，返回 (unit, confidence, reason)。

    只在 g 与 m/s² 之间猜。真实地震记录 PGA 大致在 0.01–2 g，即 0.1–20 m/s²，
    两个区间在 2–5 上重叠，重叠区降置信度。

    峰值超出这两个量级（>50）时返回 unit=None：那更像 cm/s²（gal）或 mm/s²，
    但没有量级特征能把它们互相区分，硬猜一个就是往合法哈希里塞 100 倍误差。
    这里失败关闭，由文件显式声明或用户显式指定。
    """

    if not math.isfinite(peak_abs) or peak_abs <= 0.0:
        return None, 'LOW', 'PEAK_NOT_USABLE'
    if peak_abs <= 2.0:
        return 'g', 'HIGH', f'峰值 {peak_abs:.4g} 落在常见 PGA 的 g 区间'
    if peak_abs <= 5.0:
        # 0.2–0.5 g 的强震写成 m/s² 就是 2–5，写成 g 则是罕见的超强记录。
        return 'm/s2', 'MEDIUM', f'峰值 {peak_abs:.4g} 既可能是 m/s² 也可能是极强的 g 记录'
    if peak_abs <= 50.0:
        return 'm/s2', 'HIGH', f'峰值 {peak_abs:.4g} 落在常见 PGA 的 m/s² 区间'
    return (
        None,
        'LOW',
        f'峰值 {peak_abs:.4g} 同时超出 g 与 m/s² 的常见量级，'
        f'可能是 cm/s²（gal）或 mm/s²，无法凭量级判定',
    )


def unit_from_name(name: str) -> str | None:
    """从列名或文件名里的括号记号提取单位，如 acc(g)、a_m/s2、加速度（gal）。"""

    lowered = name.lower()
    # 只在括号或分隔符界定的记号里找，避免把列名 "range" 的 g 当成单位。
    scopes = re.findall(r'[（(\[]([^）)\]]+)[）)\]]', lowered)
    scopes.extend(re.split(r'[_\s,;:]+', lowered)[1:])
    for scope in scopes:
        for canonical, tokens in _UNIT_TOKENS:
            for token in tokens:
                if re.fullmatch(rf'\s*{re.escape(token)}\s*', scope):
                    return canonical
    return None


def infer_earthquake_mapping(
    inspection: dict[str, Any],
    rows: list[dict[str, str]],
    *,
    file_name: str = '',
    solver: str = 'ANSYS',
) -> MappingSuggestion:
    """为通用表格推断一份单通道地震一致激励 mapping。

    地震门只放行单通道一致激励（analysis._load_kind_gate），所以这里固定
    产出一个 UNIFORM_EXCITATION 通道，多余的数值列作为备选列返回，由用户
    在审批界面改选。
    """

    profiles = list(inspection.get('columns') or [])
    if not profiles:
        return MappingSuggestion(
            mapping={},
            confidence='LOW',
            warnings=('文件没有可识别的列，无法推断映射',),
        )

    reasons: list[str] = []
    warnings: list[str] = []

    time_detection = detect_time_column(profiles, rows)
    if time_detection.column is not None:
        reasons.append(f'时间列取 {time_detection.column}：整列严格递增且等步长 {time_detection.step_s:.6g} s')
    else:
        warnings.append(
            '没找到等步长递增的时间列，必须显式提供采样步长 time.stepS'
        )

    value_column, value_reason = _pick_value_column(profiles, rows, time_detection.column, file_name)
    if value_column is None:
        return MappingSuggestion(
            mapping={},
            confidence='LOW',
            reasons=tuple(reasons),
            warnings=(*warnings, '没有可用作加速度时程的数值列'),
        )
    reasons.append(value_reason)

    unit, unit_confidence, unit_reason, unit_source = _infer_unit_for_column(
        value_column, rows, file_name, inspection.get('selfDescribing'),
    )
    reasons.append(unit_reason)
    if unit is None:
        warnings.append('无法判定加速度单位，必须在审批界面显式指定输入单位')
    elif unit_source == UNIT_SOURCE_MAGNITUDE:
        warnings.append(
            f'加速度单位按峰值量级推断为 {unit}（文件未声明单位），置信度 '
            f'{unit_confidence}，请在审批前确认'
        )
    elif unit_confidence != 'HIGH':
        warnings.append(f'加速度单位推断为 {unit}，置信度 {unit_confidence}，请在审批前确认')

    confidence = _overall_confidence(time_detection, unit_confidence)
    mapping = {
        'version': 2,
        'loadKind': 'EARTHQUAKE',
        'time': {
            'column': time_detection.column,
            'stepS': None if time_detection.column is not None else time_detection.step_s,
            'unit': 's',
        },
        'channels': [{
            'valueColumn': value_column,
            'applicationType': 'UNIFORM_EXCITATION',
            'targetType': None,
            'targetId': None,
            'component': 'UX',
            'quantity': 'ACCELERATION',
            'sourceUnit': unit,
            'scale': 1.0,
        }],
        'solver': solver,
    }
    alternatives = {
        'valueColumns': [
            str(profile['name'])
            for profile in profiles
            if str(profile['name']) != value_column
            and str(profile['name']) != time_detection.column
            and int(profile.get('numericCount') or 0) > 0
        ],
        # 界面里可选的单位收窄到 g / m/s²；文件自己声明了 gal 或 mm/s² 时把那
        # 一个也带上，否则界面会渲染成空选项、看起来像没识别出单位。
        'sourceUnits': _unit_options(unit),
    }
    return MappingSuggestion(
        mapping=mapping,
        confidence=confidence,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        alternatives=alternatives,
        unit_source=unit_source,
        standardize_decision=_standardize_decision(time_detection, unit, unit_source),
    )


def infer_wind_mapping(
    inspection: dict[str, Any],
    rows: list[dict[str, str]],
    *,
    target_set_id: str,
    target_nodes: tuple[int, ...],
    component: str,
    file_name: str = '',
    solver: str = 'ANSYS',
) -> MappingSuggestion:
    """为逐节点风荷载表推断一份多通道 NODAL_FORCE mapping。

    通道与节点是位置绑定：platform_store._apply_agent_standard_wind_load 按
    channel_1..channel_N 的顺序对应目标集的节点顺序，标准荷载文件里并不带节点
    号。所以列名里的节点号必须显式重排到目标集的登记顺序，且列集合与目标集必须
    完全一致——少一列或多一列都无法判定通道与节点的对应关系，只能失败关闭，否则
    就是"求解成功但荷载装错节点"，量纲上看不出来。

    target_set_id / target_nodes / component 由调用方注入，不在本模块里 import：
    本模块目前只依赖标准库，把节点集与方向常量拉进来会带上整个求解栈的导入开销。
    """

    profiles = list(inspection.get('columns') or [])
    if not profiles:
        return MappingSuggestion(
            mapping={},
            confidence='LOW',
            warnings=('文件没有可识别的列，无法推断映射',),
        )

    reasons: list[str] = []
    warnings: list[str] = []

    time_detection = detect_time_column(profiles, rows)
    if time_detection.column is not None:
        reasons.append(f'时间列取 {time_detection.column}：整列严格递增且等步长 {time_detection.step_s:.6g} s')
    else:
        warnings.append('没找到等步长递增的时间列，必须显式提供采样步长 time.stepS')

    by_node = _node_columns(profiles, time_detection.column, _WIND_NODE_COLUMN_RE)
    if not by_node:
        return MappingSuggestion(
            mapping={},
            confidence='LOW',
            reasons=tuple(reasons),
            warnings=(*warnings, '没有 fy_node_<节点号> 形态的逐节点风荷载列，无法推断多通道映射'),
        )

    missing = [node for node in target_nodes if node not in by_node]
    extra = sorted(node for node in by_node if node not in target_nodes)
    if missing or extra:
        detail = []
        if missing:
            detail.append(f'缺少节点 {", ".join(str(node) for node in missing)} 的列')
        if extra:
            detail.append(f'多出未登记节点 {", ".join(str(node) for node in extra)} 的列')
        return MappingSuggestion(
            mapping={},
            confidence='LOW',
            reasons=tuple(reasons),
            warnings=(
                *warnings,
                f'逐节点列与目标集 {target_set_id} 的 {len(target_nodes)} 个节点不一致：'
                f'{"；".join(detail)}。通道与节点是位置绑定，对应关系无法判定，'
                '不做推断以免荷载装错节点',
            ),
        )

    columns = [by_node[node] for node in target_nodes]
    reasons.append(
        f'逐节点通道取 {len(columns)} 列：按列名里的节点号重排到目标集 '
        f'{target_set_id} 的登记顺序，channel_1..channel_{len(columns)} 依次对应'
    )

    unit, unit_confidence, unit_reason, unit_source = _infer_force_unit(
        columns, file_name, inspection.get('selfDescribing'),
    )
    reasons.append(unit_reason)
    if unit is None:
        warnings.append('无法判定力单位，必须在审批界面显式指定输入单位（N 与 kN 差 1000 倍）')
    elif unit_source == UNIT_SOURCE_FILE_NAME:
        warnings.append(f'力单位 {unit} 来自文件名声明而非文件内容，请在审批前确认')

    mapping = {
        'version': 2,
        'loadKind': 'WIND',
        'time': {
            'column': time_detection.column,
            'stepS': None if time_detection.column is not None else time_detection.step_s,
            'unit': 's',
        },
        'channels': [
            {
                'valueColumn': column,
                'applicationType': 'NODAL_FORCE',
                'targetType': 'NODE_GROUP',
                'targetId': target_set_id,
                'component': component,
                'quantity': 'FORCE',
                'sourceUnit': unit,
                'scale': 1.0,
            }
            for column in columns
        ],
        'solver': solver,
    }
    return MappingSuggestion(
        mapping=mapping,
        confidence=_overall_confidence(time_detection, unit_confidence),
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        alternatives={'sourceUnits': list(DECLARABLE_FORCE_UNITS)},
        unit_source=unit_source,
        standardize_decision=_standardize_decision(time_detection, unit, unit_source),
    )


def infer_traffic_mapping(
    inspection: dict[str, Any],
    rows: list[dict[str, str]],
    *,
    target_set_id: str,
    target_nodes: tuple[int, ...],
    component: str,
    file_name: str = '',
    solver: str = 'ANSYS',
) -> MappingSuggestion:
    """为逐节点车流荷载表推断一份单通道 NODAL_FORCE_MATRIX mapping。

    车流是移动荷载：每个节点一条独立时程，制品是稠密矩阵而不是单列总力，所以
    只有一个通道，逐节点信息在矩阵的列里。与风的关键区别是绑定方式——风按
    channel_i 对应目标集第 i 个节点（位置绑定），车流由标准化时一并产出的逐节点
    mapping 制品逐条声明 {fem_node_id, source_column}（显式绑定），所以列序不参与
    语义，不需要重排到登记顺序。

    列集合仍必须与目标集完全一致：执行侧 _apply_agent_standard_traffic_load 按
    sorted(mapped_nodes) == sorted(target_nodes) 失败关闭，少一列或多一列在这里
    就该退回人工映射，而不是产出一份注定被拒的 mapping。

    target_set_id / target_nodes / component 由调用方注入，理由同 infer_wind_mapping：
    本模块只依赖标准库。
    """

    profiles = list(inspection.get('columns') or [])
    if not profiles:
        return MappingSuggestion(
            mapping={},
            confidence='LOW',
            warnings=('文件没有可识别的列，无法推断映射',),
        )

    reasons: list[str] = []
    warnings: list[str] = []

    time_detection = detect_time_column(profiles, rows)
    if time_detection.column is not None:
        reasons.append(f'时间列取 {time_detection.column}：整列严格递增且等步长 {time_detection.step_s:.6g} s')
    else:
        warnings.append('没找到等步长递增的时间列，必须显式提供采样步长 time.stepS')

    by_node = _node_columns(profiles, time_detection.column, _TRAFFIC_NODE_COLUMN_RE)
    if not by_node:
        return MappingSuggestion(
            mapping={},
            confidence='LOW',
            reasons=tuple(reasons),
            warnings=(*warnings, '没有 node_<节点号>_fy 形态的逐节点车流荷载列，无法推断矩阵映射'),
        )

    missing = [node for node in target_nodes if node not in by_node]
    extra = sorted(node for node in by_node if node not in target_nodes)
    if missing or extra:
        detail = []
        if missing:
            detail.append(f'缺少节点 {", ".join(str(node) for node in missing[:8])} 等 {len(missing)} 个节点的列')
        if extra:
            detail.append(f'多出未登记节点 {", ".join(str(node) for node in extra[:8])} 等 {len(extra)} 个节点的列')
        return MappingSuggestion(
            mapping={},
            confidence='LOW',
            reasons=tuple(reasons),
            warnings=(
                *warnings,
                f'逐节点列与目标集 {target_set_id} 的 {len(target_nodes)} 个节点不一致：'
                f'{"；".join(detail)}。矩阵必须覆盖且只覆盖登记节点，'
                '不做推断以免荷载装错节点',
            ),
        )

    # 列序按文件原样，不重排：绑定由 mapping 制品逐条声明，重排只会让标准化产物
    # 与源文件对不上，反而更难核对。
    columns = [str(profile['name']) for profile in profiles if str(profile['name']) in set(by_node.values())]
    reasons.append(
        f'逐节点矩阵取 {len(columns)} 列：列名里的节点号与目标集 {target_set_id} 的 '
        f'{len(target_nodes)} 个登记节点完全一致，列与节点的对应关系由 mapping 制品逐条声明'
    )

    unit, unit_confidence, unit_reason, unit_source = _infer_force_unit(
        columns, file_name, inspection.get('selfDescribing'),
    )
    reasons.append(unit_reason)
    if unit is None:
        warnings.append('无法判定力单位，必须在审批界面显式指定输入单位（N 与 kN 差 1000 倍）')
    elif unit_source == UNIT_SOURCE_FILE_NAME:
        warnings.append(f'力单位 {unit} 来自文件名声明而非文件内容，请在审批前确认')

    mapping = {
        'version': 2,
        'loadKind': 'TRAFFIC',
        'time': {
            'column': time_detection.column,
            'stepS': None if time_detection.column is not None else time_detection.step_s,
            'unit': 's',
        },
        'channels': [
            {
                'valueColumn': TRAFFIC_MATRIX_VALUE_COLUMN,
                'applicationType': 'NODAL_FORCE_MATRIX',
                'targetType': 'NODE_GROUP',
                'targetId': target_set_id,
                'component': component,
                'quantity': 'FORCE',
                'sourceUnit': unit,
                'scale': 1.0,
                'matrixColumnCount': len(columns),
            }
        ],
        'solver': solver,
    }
    return MappingSuggestion(
        mapping=mapping,
        confidence=_overall_confidence(time_detection, unit_confidence),
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        alternatives={'sourceUnits': list(DECLARABLE_FORCE_UNITS)},
        unit_source=unit_source,
        standardize_decision=_standardize_decision(time_detection, unit, unit_source),
    )


def traffic_matrix_columns(
    header: list[str],
    time_column: str | None,
) -> list[tuple[int, str]]:
    """从矩阵表头解析出 [(节点号, 源列名)]，保持表头顺序。

    标准化时要用它重新解析一次源文件：逐节点绑定不经 HTTP 传递，而是从审批冻结的
    同一份字节里按同一条正则重新推导，所以两次 standardize()（算 expectedSha256
    与执行时校验）必然得到同一个绑定，digest 稳定。
    """

    matched: list[tuple[int, str]] = []
    seen: dict[int, int] = {}
    for name in header:
        text = str(name)
        if time_column is not None and text == time_column:
            continue
        hit = _TRAFFIC_NODE_COLUMN_RE.match(text)
        if hit is None:
            continue
        node = int(hit.group(1))
        seen[node] = seen.get(node, 0) + 1
        matched.append((node, text))
    duplicated = sorted(node for node, count in seen.items() if count > 1)
    if duplicated:
        # 同一节点两列时无法判定该用哪一列，交给调用方失败关闭。
        return []
    return matched


def _node_columns(
    profiles: list[dict[str, Any]],
    time_column: str | None,
    pattern: re.Pattern[str],
) -> dict[int, str]:
    """按列名解析出 {节点号: 列名}，只收全数值列。"""

    by_node: dict[int, str] = {}
    for profile in profiles:
        name = str(profile['name'])
        if name == time_column or int(profile.get('numericCount') or 0) <= 0:
            continue
        matched = pattern.match(name)
        if matched is None:
            continue
        node = int(matched.group(1))
        # 同一节点出现两列时无法判定该用哪一列，两列都丢掉，交由上面的
        # 列集合比对失败关闭，而不是静默取其中一个。
        by_node[node] = name if node not in by_node else ''
    return {node: name for node, name in by_node.items() if name}


def _infer_force_unit(
    columns: list[str],
    file_name: str,
    self_describing: dict[str, Any] | None = None,
) -> tuple[str | None, str, str, str]:
    """力单位解析，返回 (unit, confidence, reason, source)。

    只认文件里写明的记号，不按量级猜：N 与 kN 差 1000 倍，但两者的常见量级完全
    重叠——一个主梁节点上 800 N 和 800 kN 都是物理上讲得通的风荷载，没有任何量级
    特征能把它们分开。猜不准就别猜，回审批界面让人填。
    """

    declared = str((self_describing or {}).get('sourceUnit') or '').strip()
    if declared in DECLARABLE_FORCE_UNITS:
        return (
            declared,
            'HIGH',
            f'单位取 {declared}：{(self_describing or {}).get("formatName") or "文件"}头显式声明',
            UNIT_SOURCE_HEADER,
        )

    named = {unit_from_name(column) for column in columns}
    if len(named) == 1:
        only = next(iter(named))
        if only in DECLARABLE_FORCE_UNITS:
            return only, 'HIGH', f'单位取 {only}：逐节点列名一致显式声明', UNIT_SOURCE_COLUMN_NAME
    elif named & _FORCE_UNIT_SET:
        # 一部分列写了单位、另一部分没写，或各列写的不是同一个单位：按哪个都
        # 可能给另一批列套上 1000 倍误差，这里不选。
        return (
            None,
            'LOW',
            '各逐节点列声明的力单位不一致，无法判定统一输入单位',
            UNIT_SOURCE_UNKNOWN,
        )

    stem_named = unit_from_name(Path(file_name).stem) if file_name else None
    if stem_named in DECLARABLE_FORCE_UNITS:
        return (
            stem_named,
            'MEDIUM',
            f'单位取 {stem_named}：文件名声明，列名未声明',
            UNIT_SOURCE_FILE_NAME,
        )

    return (
        None,
        'LOW',
        '文件头、列名与文件名都没有声明力单位，量级无法区分 N 与 kN',
        UNIT_SOURCE_UNKNOWN,
    )


def _unit_options(unit: str | None) -> list[str]:
    options = list(INFERABLE_ACCELERATION_UNITS)
    if unit is not None and unit not in options:
        options.append(unit)
    return options


def _standardize_decision(
    time_detection: TimeColumnDetection,
    unit: str | None,
    unit_source: str,
) -> str:
    """能不能跳过映射确认直接标准化。

    只有两件事都是从文件内容里读出来的才放行：时间轴（实测等步长递增列）和
    单位（文件头或列名的显式声明）。猜出来的单位一律回到审批界面——猜错 g 与
    m/s² 就是 9.8 倍荷载误差，而且会带着一个完全合法的 SHA256 冻结值往下走。
    """

    if time_detection.column is None or unit is None:
        return DECISION_ASK
    if unit_source not in _AUTO_TRUSTED_UNIT_SOURCES:
        return DECISION_ASK
    return DECISION_AUTO


def _pick_value_column(
    profiles: list[dict[str, Any]],
    rows: list[dict[str, str]],
    time_column: str | None,
    file_name: str,
) -> tuple[str | None, str]:
    """选数值列：排除时间列，优先列名带加速度语义的，其次第一列全数值列。"""

    numeric_columns = [
        str(profile['name'])
        for profile in profiles
        if str(profile['name']) != time_column
        and int(profile.get('numericCount') or 0) > 0
    ]
    if not numeric_columns:
        return None, ''
    for name in numeric_columns:
        lowered = name.lower()
        if any(hint in lowered for hint in _ACCELERATION_NAME_HINTS):
            return name, f'数值列取 {name}：列名含加速度语义'
    if len(numeric_columns) == 1:
        return numeric_columns[0], f'数值列取 {numeric_columns[0]}：唯一的数值列'
    return (
        numeric_columns[0],
        f'数值列取 {numeric_columns[0]}：排除时间列后的第一个数值列，'
        f'另有 {len(numeric_columns) - 1} 个候选列',
    )


def _infer_unit_for_column(
    column: str,
    rows: list[dict[str, str]],
    file_name: str,
    self_describing: dict[str, Any] | None = None,
) -> tuple[str | None, str, str, str]:
    """单位解析，返回 (unit, confidence, reason, source)。

    优先级就是可信度的排序：文件头声明 > 列名声明 > 文件名声明 > 峰值量级。
    前两级是从文件内容里读出来的事实，后两级不是——文件名改个名就变，量级
    本身在区间边界上重叠。source 把这个区别显式带给调用方，别让下游只看到
    一个单位字符串就当成等价的结论。
    """

    declared = str((self_describing or {}).get('sourceUnit') or '').strip()
    if declared in DECLARABLE_ACCELERATION_UNITS:
        return (
            declared,
            'HIGH',
            f'单位取 {declared}：{(self_describing or {}).get("formatName") or "文件"}头显式声明',
            UNIT_SOURCE_HEADER,
        )

    named = unit_from_name(column)
    if named in DECLARABLE_ACCELERATION_UNITS:
        return named, 'HIGH', f'单位取 {named}：列名 {column} 显式声明', UNIT_SOURCE_COLUMN_NAME

    stem_named = unit_from_name(Path(file_name).stem) if file_name else None
    if stem_named in DECLARABLE_ACCELERATION_UNITS:
        return (
            stem_named,
            'MEDIUM',
            f'单位取 {stem_named}：文件名声明，列名未声明',
            UNIT_SOURCE_FILE_NAME,
        )

    values = _numeric_column(rows, column) or []
    peak = max((abs(value) for value in values), default=0.0)
    unit, confidence, reason = infer_acceleration_unit(peak)
    source = UNIT_SOURCE_MAGNITUDE if unit is not None else UNIT_SOURCE_UNKNOWN
    return unit, confidence, reason, source


def _overall_confidence(time_detection: TimeColumnDetection, unit_confidence: str) -> str:
    if time_detection.column is None:
        return 'LOW'
    ranking = {'LOW': 0, 'MEDIUM': 1, 'HIGH': 2}
    return unit_confidence if ranking[unit_confidence] < 2 else 'HIGH'


def _numeric_column(rows: list[dict[str, str]], name: str) -> list[float] | None:
    """整列都是有限数值时返回列表，否则 None。"""

    values: list[float] = []
    for row in rows:
        raw = str(row.get(name, '')).strip()
        if not raw:
            return None
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        values.append(number)
    return values


def _uniform_step(values: list[float]) -> float | None:
    """整列等差时返回步长，否则 None。"""

    step = values[1] - values[0]
    if step <= 0.0:
        return None
    tolerance = max(abs(step), 1.0) * _TIME_STEP_RTOL
    for previous, current in zip(values, values[1:]):
        if abs((current - previous) - step) > tolerance:
            return None
    return step


def _column_index(profiles: list[dict[str, Any]], name: str) -> int:
    for index, profile in enumerate(profiles):
        if str(profile['name']) == name:
            return index
    return len(profiles)
