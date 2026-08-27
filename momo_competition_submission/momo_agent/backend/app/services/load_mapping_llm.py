"""用 LLM 补齐荷载文件声明的阅读能力，再逐项回验。

确定性推断（load_mapping_inference）只认记号化的单位声明：``Accel[g]``、
``acc(gal)``，选数值列也只认列名里的 ``acc`` / ``加速度`` 之类关键词。真实台网
导出的说明行往往是散文——"Units: acceleration in g"、"第 3 列为竖向地震加速度"
——正则读不了，于是本来写在文件里的单位被降级成按峰值量级猜、数值列退化成
"排除时间列后的第一列"，落到 ASK。本模块补的就是这段阅读能力。

安全模型：LLM 只负责读文本，不负责下结论。

说明文字是上传上来的文件内容，属于不可信数据。一个写着"Units: mm/s2"的
恶意（或仅仅是写错的）文件如果能直接决定换算系数，就是 1000 倍荷载误差，
而且会带着一个完全合法的 SHA256 冻结值走完审批链。选错数值列同理：拿一列
无关数据当地震时程，产出照样有合法哈希。所以模型的每一项读数都要过回验：

1. 原文凭据：单位声明与"哪列是数值列"各自都必须能在说明文字或列名里逐字
   找到（归一化空白后）。这是 agent_llm.numbers_are_grounded 的同一思路
   ——不允许凭空生成。
2. 量级自洽：声称 g 则峰值不得超过 2；声称 m/s² 不得超过 50。声明与数据
   互相矛盾时，说明至少有一个是错的，不能采信。量级用最终选定的数值列实测，
   不是用模型转述的数字。
3. 列名存在：时间列/数值列必须是 inspect 实际给出的列。
4. 单位白名单：只接受 g / m/s2 / cm/s2 / mm/s2。
5. 数值列可用：提名的数值列必须整列都是数值，且不能就是时间列本身。

任何一项不过，就退回确定性结果并标 ASK。模型不可用、超时、返回垃圾也一样
——这条链路的默认状态是"回到审批界面让人确认"，LLM 只能把状态从 ASK 提升
到 AUTO，不能反过来放松任何既有约束。

时间列是个例外中的例外：模型可以*提名*一列，但等步长与严格递增仍由
detect_time_column 独立实测，模型说了不算。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.logging_config import get_platform_logger
from app.services.agent_llm import LoadDeclarationReading, llm_planner
from app.services.load_mapping_inference import (
    DECISION_ASK,
    DECISION_AUTO,
    DECLARABLE_ACCELERATION_UNITS,
    UNIT_SOURCE_PREAMBLE,
    detect_time_column,
)


logger = get_platform_logger('load_mapping_llm')

# 声称某单位时，峰值应当落进的区间。真实地震记录 PGA 大致 0.01–2 g，换算过去
# 就是每个单位各自的一段。区间取得比常见量级宽松得多（两端各放一到两个数量
# 级），只拦真正说不通的组合。
#
# 上下界都要有。只设上限时，"单位是 mm/s²"配一列峰值 0.31 的数据能过关——那
# 是 0.0003 m/s²，比环境振动还小，实际是把一条 g 记录当成 mm/s² 读，荷载缩小
# 近一万倍。恶意注入正好长这样：声明一个极小的单位，数据看着毫无异常。
_UNIT_PEAK_BANDS = {
    'g': (5.0e-4, 2.0),
    'm/s2': (5.0e-3, 50.0),
    'cm/s2': (0.5, 5000.0),
    'mm/s2': (5.0, 50000.0),
}

_WHITESPACE = re.compile(r'\s+')


@dataclass(frozen=True)
class RefinedSuggestion:
    """确定性建议 + LLM 声明读数回验后的结果。"""

    mapping: dict[str, Any]
    confidence: str
    unit_source: str
    standardize_decision: str
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    alternatives: dict[str, Any] = field(default_factory=dict)
    llm_mode: str = 'SKIPPED'
    rejected_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            'mapping': self.mapping,
            'confidence': self.confidence,
            'unitSource': self.unit_source,
            'standardizeDecision': self.standardize_decision,
            'reasons': list(self.reasons),
            'warnings': list(self.warnings),
            'alternatives': dict(self.alternatives),
            'llmMode': self.llm_mode,
        }
        if self.rejected_reason:
            payload['rejectedReason'] = self.rejected_reason
        return payload


def refine_suggestion(
    inspection: dict[str, Any],
    rows: list[dict[str, str]],
    *,
    planner: Any = None,
) -> RefinedSuggestion:
    """在确定性建议之上叠加 LLM 读出的文件声明。

    确定性建议已经能自动放行时（文件头或列名里有记号化声明）直接返回，不花
    一次 LLM 调用；只有落到 ASK 的才值得让模型读一遍说明文字。
    """

    suggestion = dict(inspection.get('suggestedMapping') or {})
    base = _from_suggestion(suggestion)
    if not suggestion.get('mapping'):
        return base
    if base.standardize_decision == DECISION_AUTO:
        return base

    channels = list(base.mapping.get('channels') or [])
    if len(channels) != 1 or channels[0].get('quantity') != 'ACCELERATION':
        # 模型只会读加速度单位（LoadDeclarationReading.source_unit），而
        # _apply_reading 也只改 channels[0]。拿它去读逐节点力通道，等于用一个
        # 加速度单位覆盖第一个通道、其余通道原样不动，产出自相矛盾的 mapping。
        return base

    preamble_lines = [str(line) for line in (inspection.get('preambleLines') or [])]
    column_names = [str(profile['name']) for profile in (inspection.get('columns') or [])]
    if not preamble_lines:
        # 没有说明文字，模型无从读起——列名已经由正则看过了。
        return base

    engine = planner if planner is not None else llm_planner
    reading = engine.read_load_declarations(
        preamble_lines=preamble_lines,
        column_names=column_names,
        file_name=str(inspection.get('fileName') or ''),
        # 列画像与样本行是模型判断列语义的唯一依据：只给列名时，一份 C1/C2/C3
        # 的表头没有任何可读信息。给画像不给全量数据——数值判断仍归确定性代码。
        column_profiles=list(inspection.get('columns') or []),
        sample_rows=list(inspection.get('sampleRows') or []),
    )
    return _apply_reading(
        base,
        reading,
        rows=rows,
        preamble_lines=preamble_lines,
        column_names=column_names,
        inspection=inspection,
    )


def _from_suggestion(suggestion: dict[str, Any]) -> RefinedSuggestion:
    return RefinedSuggestion(
        mapping=dict(suggestion.get('mapping') or {}),
        confidence=str(suggestion.get('confidence') or 'LOW'),
        unit_source=str(suggestion.get('unitSource') or 'UNKNOWN'),
        standardize_decision=str(suggestion.get('standardizeDecision') or DECISION_ASK),
        reasons=tuple(str(item) for item in (suggestion.get('reasons') or [])),
        warnings=tuple(str(item) for item in (suggestion.get('warnings') or [])),
        alternatives=dict(suggestion.get('alternatives') or {}),
    )


def _apply_reading(
    base: RefinedSuggestion,
    reading: LoadDeclarationReading,
    *,
    rows: list[dict[str, str]],
    preamble_lines: list[str],
    column_names: list[str],
    inspection: dict[str, Any],
) -> RefinedSuggestion:
    """回验模型读数，只有全部通过才把状态提升到 AUTO。"""

    unit = reading.source_unit
    if unit is None:
        return _rejected(base, 'LLM_NO_DECLARATION', '说明文字里没有单位声明，仍需人工确认输入单位')
    if unit not in DECLARABLE_ACCELERATION_UNITS:
        return _rejected(base, 'LLM_UNIT_NOT_ALLOWED', f'读出的单位 {unit} 不在允许集合内')

    quote = str(reading.declaration_quote or '').strip()
    if not quote:
        return _rejected(base, 'LLM_QUOTE_MISSING', '单位声明缺少原文凭据，不予采信')
    if not _quote_is_grounded(quote, preamble_lines, column_names):
        logger.warning('load declaration quote not grounded: %s', quote[:120])
        return _rejected(base, 'LLM_QUOTE_NOT_GROUNDED', '单位声明的原文凭据在文件里找不到，不予采信')

    channels = list(base.mapping.get('channels') or [])
    if not channels:
        return _rejected(base, 'LLM_NO_CHANNEL', '建议映射没有通道，无法应用声明')

    if reading.value_column and reading.value_column not in column_names:
        return _rejected(base, 'LLM_COLUMN_NOT_FOUND', f'读出的数值列 {reading.value_column} 不在文件列里')
    if reading.time_column and reading.time_column not in column_names:
        return _rejected(base, 'LLM_COLUMN_NOT_FOUND', f'读出的时间列 {reading.time_column} 不在文件列里')

    # 数值列必须在量级门之前定下来：峰值要在真正会被取用的那一列上测，否则
    # 用 A 列的峰值给 B 列的单位背书，量级门就形同虚设。
    value_column, value_reason, rejection = _resolve_value_column(
        reading,
        base,
        preamble_lines=preamble_lines,
        column_names=column_names,
        inspection=inspection,
    )
    if rejection is not None:
        return _rejected(base, *rejection)

    peak = _peak_abs(rows, value_column)
    floor, ceiling = _UNIT_PEAK_BANDS[unit]
    if not floor <= peak <= ceiling:
        logger.warning('declared unit %s contradicts peak %.6g', unit, peak)
        return _rejected(
            base,
            'LLM_MAGNITUDE_INCONSISTENT',
            f'声明单位 {unit} 与 {value_column} 列实测峰值 {peak:.4g} 矛盾'
            f'（该单位下峰值应在 {floor:.4g}–{ceiling:.4g} 之间）',
        )

    mapping = _mapping_with_channel(base.mapping, unit=unit, value_column=value_column)
    reasons = (
        # 换列时确定性层给出的"数值列取 X"依据已经不成立，留着会与新依据打架。
        *(base.reasons if value_reason is None else _without_value_column_reason(base.reasons)),
        f'单位取 {unit}：文件说明文字声明「{quote[:80]}」（LLM 读出，已回验原文与量级）',
        *((value_reason,) if value_reason is not None else ()),
    )
    # 时间列仍以确定性检测为准。模型提名的列只在"确定性没找到、而模型提名的
    # 列自己通过了实测"时才有意义——等步长与严格递增不接受转述。
    time_column = (base.mapping.get('time') or {}).get('column')
    warnings = tuple(
        warning for warning in base.warnings
        if '单位' not in warning
    )
    if time_column is None:
        confirmed = _confirm_time_column(reading.time_column, inspection, rows)
        if confirmed is None:
            return RefinedSuggestion(
                mapping=mapping,
                confidence=base.confidence,
                unit_source=UNIT_SOURCE_PREAMBLE,
                standardize_decision=DECISION_ASK,
                reasons=reasons,
                warnings=warnings,
                alternatives=_alternatives_for(
                    base.alternatives,
                    inspection,
                    value_column=None if value_reason is None else value_column,
                    time_column=None,
                ),
                llm_mode='LLM',
                rejected_reason='TIME_COLUMN_UNCONFIRMED',
            )
        mapping = dict(mapping)
        mapping['time'] = {**(mapping.get('time') or {}), 'column': confirmed, 'stepS': None}
        reasons = (*reasons, f'时间列取 {confirmed}：LLM 提名后由等步长检测实测确认')
        time_column = confirmed

    return RefinedSuggestion(
        mapping=mapping,
        confidence='HIGH',
        unit_source=UNIT_SOURCE_PREAMBLE,
        standardize_decision=DECISION_AUTO,
        reasons=reasons,
        warnings=warnings,
        alternatives=_alternatives_for(
            base.alternatives,
            inspection,
            value_column=None if value_reason is None else value_column,
            time_column=time_column,
        ),
        llm_mode='LLM',
    )


def _rejected(base: RefinedSuggestion, code: str, message: str) -> RefinedSuggestion:
    """回退到确定性结果，并把拒收原因带上。决策必然是 ASK。"""

    return RefinedSuggestion(
        mapping=base.mapping,
        confidence=base.confidence,
        unit_source=base.unit_source,
        standardize_decision=DECISION_ASK,
        reasons=base.reasons,
        warnings=(*base.warnings, message) if message not in base.warnings else base.warnings,
        alternatives=base.alternatives,
        llm_mode='LLM_REJECTED',
        rejected_reason=code,
    )


def _quote_is_grounded(quote: str, preamble_lines: list[str], column_names: list[str]) -> bool:
    """凭据必须在说明文字或列名里逐字出现（仅归一化空白与大小写）。"""

    needle = _normalize(quote)
    if not needle:
        return False
    haystacks = [_normalize(line) for line in preamble_lines]
    haystacks.extend(_normalize(name) for name in column_names)
    return any(needle in haystack for haystack in haystacks if haystack)


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(' ', str(text or '')).strip().lower()


def _peak_abs(rows: list[dict[str, str]], column: str) -> float:
    peak = 0.0
    for row in rows:
        raw = str(row.get(column, '')).strip()
        if not raw:
            continue
        try:
            value = abs(float(raw))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > peak:
            peak = value
    return peak


def _mapping_with_channel(
    mapping: dict[str, Any],
    *,
    unit: str,
    value_column: str,
) -> dict[str, Any]:
    updated = dict(mapping)
    channels = [dict(channel) for channel in (mapping.get('channels') or [])]
    if channels:
        channels[0]['sourceUnit'] = unit
        channels[0]['valueColumn'] = value_column
    updated['channels'] = channels
    return updated


def _resolve_value_column(
    reading: LoadDeclarationReading,
    base: RefinedSuggestion,
    *,
    preamble_lines: list[str],
    column_names: list[str],
    inspection: dict[str, Any],
) -> tuple[str, str | None, tuple[str, str] | None]:
    """定下取哪一列作加速度时程，返回 (列名, 改选依据或 None, 拒收原因或 None)。

    确定性层选列靠两条规则：列名含 acc/加速度，或"排除时间列后的第一个数值列"
    （load_mapping_inference._pick_value_column）。后一条在多列文件里就是猜——
    一份 ``C1 C2 C3`` 的表头里，第一个数值列凭什么是地震记录。模型读得懂
    "第 3 列为校正后加速度"这类说明，补的正是这一段。

    取错列的危害和猜错单位同级：产出一条完全无关的时程，照样带合法 SHA256 走完
    审批。所以覆盖确定性选择要过四道回验。任何一项不过就整份读数拒收，不退化成
    "单位照收、列按原样"——模型连指向哪一列都读错或编造时，它对单位的理解也不
    值得采信（与既有 LLM_COLUMN_NOT_FOUND 同一口径）。
    """

    channels = list(base.mapping.get('channels') or [])
    deterministic = str(channels[0].get('valueColumn') or '')
    nominated = reading.value_column
    if not nominated or nominated == deterministic:
        return deterministic, None, None

    quote = str(reading.value_column_quote or '').strip()
    if not quote:
        return deterministic, None, (
            'LLM_VALUE_COLUMN_QUOTE_MISSING',
            f'数值列改选为 {nominated} 缺少原文凭据，不予采信',
        )
    if not _quote_is_grounded(quote, preamble_lines, column_names):
        logger.warning('value column quote not grounded: %s', quote[:120])
        return deterministic, None, (
            'LLM_VALUE_COLUMN_QUOTE_NOT_GROUNDED',
            f'数值列改选为 {nominated} 的原文凭据在文件里找不到，不予采信',
        )
    # 拿单位声明那一行当选列依据不算依据：那句话讲的是单位，没讲哪一列。
    if _normalize(quote) == _normalize(reading.declaration_quote or ''):
        return deterministic, None, (
            'LLM_VALUE_COLUMN_QUOTE_NOT_SPECIFIC',
            f'数值列改选为 {nominated} 只引用了单位声明，未给出指向该列的原文',
        )
    time_column = (base.mapping.get('time') or {}).get('column')
    if nominated in {time_column, reading.time_column}:
        return deterministic, None, (
            'LLM_VALUE_COLUMN_IS_TIME_COLUMN',
            f'{nominated} 同时被读作时间列与数值列，声明自相矛盾',
        )
    if not _column_is_fully_numeric(inspection, nominated):
        return deterministic, None, (
            'LLM_VALUE_COLUMN_NOT_NUMERIC',
            f'数值列改选为 {nominated}，但该列并非整列数值',
        )
    return (
        nominated,
        f'数值列取 {nominated}：文件说明「{quote[:80]}」'
        f'（LLM 读出，已回验原文与整列数值）',
        None,
    )


def _column_is_fully_numeric(inspection: dict[str, Any], column: str) -> bool:
    """整列都是有效数值才能当时程用。用 inspect 实测的列画像判定，不问模型。"""

    row_count = int(inspection.get('rowCount') or 0)
    if row_count <= 0:
        return False
    for profile in (inspection.get('columns') or []):
        if str(profile.get('name')) == column:
            return int(profile.get('numericCount') or 0) == row_count
    return False


def _without_value_column_reason(reasons: tuple[str, ...]) -> tuple[str, ...]:
    """去掉确定性层的选列依据。换过列之后，那句话说的是另一列。"""

    return tuple(reason for reason in reasons if not reason.startswith('数值列取 '))


def _alternatives_for(
    base_alternatives: dict[str, Any],
    inspection: dict[str, Any],
    *,
    value_column: str | None,
    time_column: str | None,
) -> dict[str, Any]:
    """换过数值列后重算备选列；value_column 为 None 表示没换，原样返回。

    确定性层把"已选中的那一列"排除在备选之外（load_mapping_inference），换列后
    这份名单就是错的：新选中的列还留在备选里，被换下的原列反而不在。审批界面的
    下拉框直接用它，不重算就会少掉一个真正的候选列。
    """

    if value_column is None:
        return base_alternatives
    excluded = {value_column, time_column}
    return {
        **base_alternatives,
        'valueColumns': [
            str(profile['name'])
            for profile in (inspection.get('columns') or [])
            if str(profile['name']) not in excluded
            and int(profile.get('numericCount') or 0) > 0
        ],
    }


def _confirm_time_column(
    nominated: str | None,
    inspection: dict[str, Any],
    rows: list[dict[str, str]],
) -> str | None:
    """模型提名的时间列必须自己通过等步长实测才算数。"""

    if not nominated:
        return None
    profiles = [
        profile for profile in (inspection.get('columns') or [])
        if str(profile.get('name')) == nominated
    ]
    if not profiles:
        return None
    detection = detect_time_column(profiles, rows)
    return detection.column if detection.column == nominated else None
