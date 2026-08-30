"""自描述地震记录格式的解析器。

上传的荷载文件分两类：一类是通用表格（CSV/TXT/XLSX），列含义要靠推断
（见 load_mapping_inference）；另一类是专业格式，采样步长和单位直接写在
文件头里。本模块只负责后者——把文件头读出来，转成通用表格加一份可信的
元数据，让后续流程不必猜。

首期只做 PEER NGA 的 .AT2/.AT1（加速度时程）。格式约定见
https://scec.usc.edu/scecpedia/PEER_Data_Format ：前 4 行是文本头，
第 3 行声明单位，第 4 行给 NPTS 与 DT，其后是每行若干个数值的定宽块，
按行优先展开成一条等步长序列。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PEER_SUFFIXES = frozenset({'.at1', '.at2'})

# NPTS 与 DT 在同一行，形如 "NPTS=   7998, DT=   .0050 SEC,"。
# 各家导出的空格、逗号、尾随文本都不一致，只锚定关键字与数值本身。
_PEER_NPTS = re.compile(r'NPTS\s*[=:]\s*(\d+)', re.IGNORECASE)
_PEER_DT = re.compile(r'\bDT\s*[=:]\s*(\d*\.?\d+(?:[eE][+-]?\d+)?)', re.IGNORECASE)
# 少数导出把步长写成 "0.005 SEC" 而不带 DT= 前缀。
_PEER_DT_FALLBACK = re.compile(r'(\d*\.?\d+(?:[eE][+-]?\d+)?)\s*SEC', re.IGNORECASE)
# 旧 PGA 库的导出把数值写在前、关键字写在后：" 3930 0.00500 NPTS, DT"。
# 两种写法见 OpenSees 自带的 ReadRecord.tcl。标准写法（NPTS=/DT=）优先匹配，
# 这条只在前者匹配不到时兜底，因此不会抢正常文件头的解析。
_PEER_NPTS_DT_TRAILING = re.compile(
    r'(\d+)\s+(\d*\.?\d+(?:[eE][+-]?\d+)?)\s+NPTS\s*,?\s*DT',
    re.IGNORECASE,
)
_PEER_UNIT_TOKENS = (
    ('m/s2', ('m/sec2', 'm/s2', 'm/sec^2', 'm/s^2')),
    ('cm/s2', ('cm/sec2', 'cm/s2', 'cm/sec^2', 'cm/s^2', 'gal')),
    ('g', ('g',)),
)


@dataclass(frozen=True)
class ParsedRecord:
    """一条从自描述格式解析出的等步长时程。"""

    values: tuple[float, ...]
    dt: float
    unit: str
    quantity: str
    format_name: str
    header_lines: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def sample_count(self) -> int:
        return len(self.values)

    @property
    def duration_s(self) -> float:
        return self.dt * max(self.sample_count - 1, 0)


class LoadFormatError(ValueError):
    """自描述格式解析失败。带 code 供上层转成 HTTP 明细。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def is_self_describing_format(file_name: str) -> bool:
    """文件名后缀是否属于已支持的自描述格式。"""

    return Path(file_name).suffix.lower() in PEER_SUFFIXES


def parse_self_describing(file_name: str, content: bytes) -> ParsedRecord:
    """按后缀分派到具体解析器。"""

    suffix = Path(file_name).suffix.lower()
    if suffix in PEER_SUFFIXES:
        return parse_peer_at2(content)
    raise LoadFormatError('UNSUPPORTED_LOAD_FORMAT', f'不支持的自描述荷载格式：{suffix}')


def parse_peer_at2(content: bytes) -> ParsedRecord:
    """解析 PEER NGA .AT2/.AT1 加速度时程。

    文本头行数不固定（多数是 4 行，个别导出多一行空行或注释），因此不按
    行号硬取，而是扫描到第一行"全是数值"为止，把之前的行都当文本头。
    """

    text = _decode(content)
    lines = text.splitlines()
    if not lines:
        raise LoadFormatError('EMPTY_FILE', 'PEER 记录文件为空')

    header_lines: list[str] = []
    values: list[float] = []
    data_started = False
    for line in lines:
        stripped = line.strip()
        if not data_started:
            if not stripped:
                header_lines.append(line)
                continue
            numbers = _all_numbers_or_none(stripped)
            if numbers is None:
                header_lines.append(line)
                continue
            data_started = True
            values.extend(numbers)
            continue
        if not stripped:
            continue
        numbers = _all_numbers_or_none(stripped)
        if numbers is None:
            # 数据块之后出现的非数值行一律拒绝：PEER 格式没有尾注，出现
            # 说明文件被改过或截断拼接，静默跳过会得到一条错误的时程。
            raise LoadFormatError(
                'PEER_UNEXPECTED_TRAILING_TEXT',
                f'PEER 记录数据块中出现非数值行：{stripped[:60]}',
            )
        values.extend(numbers)

    if not values:
        raise LoadFormatError('PEER_NO_DATA', 'PEER 记录没有数值数据行')

    header_text = '\n'.join(header_lines)
    dt = _peer_dt(header_text)
    unit = _peer_unit(header_text)
    npts = _peer_npts(header_text)
    if npts is not None and npts != len(values):
        # NPTS 与实际值数不一致意味着文件被截断或拼接过。声明的步长与真实
        # 样本数对不上时，任何一侧都不可信，直接失败关闭。
        raise LoadFormatError(
            'PEER_NPTS_MISMATCH',
            f'PEER 记录声明 NPTS={npts}，实际解析出 {len(values)} 个数值',
        )

    return ParsedRecord(
        values=tuple(values),
        dt=dt,
        unit=unit,
        quantity='ACCELERATION',
        format_name='PEER_NGA',
        header_lines=tuple(line.strip() for line in header_lines if line.strip()),
        metadata={
            'declaredNpts': npts,
            'declaredDt': dt,
            'declaredUnit': unit,
            'description': _peer_description(header_lines),
        },
    )


def _peer_dt(header_text: str) -> float:
    match = _PEER_DT.search(header_text) or _PEER_DT_FALLBACK.search(header_text)
    if match is not None:
        dt = float(match.group(1))
    else:
        trailing = _PEER_NPTS_DT_TRAILING.search(header_text)
        if trailing is None:
            raise LoadFormatError('PEER_MISSING_DT', 'PEER 记录文件头缺少 DT 采样步长')
        dt = float(trailing.group(2))
    if dt <= 0.0:
        raise LoadFormatError('PEER_INVALID_DT', f'PEER 记录的 DT 必须为正数，读到 {dt}')
    return dt


def _peer_npts(header_text: str) -> int | None:
    match = _PEER_NPTS.search(header_text)
    if match is not None:
        return int(match.group(1))
    # 旧 PGA 库写法也要读出 NPTS，否则这类文件的截断守卫（NPTS 与实际值数
    # 比对）会静默失效——文件被截断照样放行。
    trailing = _PEER_NPTS_DT_TRAILING.search(header_text)
    return int(trailing.group(1)) if trailing else None


def _peer_unit(header_text: str) -> str:
    """从"ACCELERATION TIME SERIES IN UNITS OF G"这类行提取单位。

    先匹配长记号（m/s2、cm/s2、gal）再匹配单字母 G，避免把 "gal" 的首字母
    当成 g，或把 "UNITS OF G" 里其他词的字母误判。
    """

    lowered = header_text.lower()
    units_index = lowered.rfind('units of')
    scope = lowered[units_index + len('units of'):] if units_index >= 0 else lowered
    for canonical, tokens in _PEER_UNIT_TOKENS:
        for token in tokens:
            if re.search(rf'(?<![a-z0-9/^]){re.escape(token)}(?![a-z0-9/^])', scope):
                return canonical
    raise LoadFormatError(
        'PEER_MISSING_UNIT',
        'PEER 记录文件头未声明可识别的加速度单位（支持 g、m/s2、cm/s2、gal）',
    )


def _peer_description(header_lines: list[str]) -> str:
    """取文件头里的事件描述行（PEER 约定为第 2 行）。"""

    candidates = [line.strip() for line in header_lines if line.strip()]
    return candidates[1] if len(candidates) >= 2 else ''


def _all_numbers_or_none(line: str) -> list[float] | None:
    """整行都是数值时返回解析结果，否则返回 None。"""

    cells = [cell for cell in re.split(r'[,\s]+', line.strip()) if cell]
    if not cells:
        return None
    numbers: list[float] = []
    for cell in cells:
        number = _fortran_float_or_none(cell)
        if number is None:
            return None
        numbers.append(number)
    return numbers


def _fortran_float_or_none(cell: str) -> float | None:
    """解析数值，兼容 Fortran 的 D 指数记法（1.5D-03）。"""

    text = cell.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    if 'd' in text.lower():
        try:
            return float(re.sub(r'[dD]', 'e', text))
        except ValueError:
            return None
    return None


def _decode(content: bytes) -> str:
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030', 'latin-1'):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise LoadFormatError('UNSUPPORTED_ENCODING', '无法识别荷载文件文本编码')
