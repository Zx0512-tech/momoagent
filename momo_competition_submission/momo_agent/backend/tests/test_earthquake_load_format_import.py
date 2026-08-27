"""地震荷载输入文件的格式转换。

覆盖两条入口：自描述格式（PEER NGA .AT2/.AT1，采样步长与单位写在文件头）
和通用表格（CSV/TXT，列语义靠推断）。两条最终都要产出满足既有地震契约的
标准 CSV —— 单通道、EARTHQUAKE、UNIFORM_EXCITATION、ACCELERATION、m/s2，
即 platform_store._apply_agent_standard_earthquake_load 放行的形状。
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.load_format_readers import (
    LoadFormatError,
    is_self_describing_format,
    parse_peer_at2,
)
from app.services.load_artifact_service import load_artifact_service
from app.services.load_import_service import load_import_service
from app.services.platform_store import PlatformStore
from app.services.load_mapping_inference import (
    DECISION_ASK,
    DECISION_AUTO,
    UNIT_SOURCE_COLUMN_NAME,
    UNIT_SOURCE_FILE_NAME,
    UNIT_SOURCE_HEADER,
    UNIT_SOURCE_MAGNITUDE,
    UNIT_SOURCE_UNKNOWN,
    detect_time_column,
    infer_acceleration_unit,
    unit_from_name,
)


FIXTURES = Path(__file__).parent / 'fixtures' / 'earthquake'

# 三条真实 PEER NGA 记录的表头声明值与实测峰值。dt 各不相同，且 RSN1633
# 的 DT 行没有尾随逗号，覆盖不同导出写法。
PEER_RECORDS = (
    ('RSN767_LOMAP_G03090.AT2', 7998, 0.005, 0.3682262, 'Loma Prieta'),
    ('RSN164_IMPVALL.H_H-CPE147.AT2', 6382, 0.010, 0.1682932, 'Imperial Valley'),
    ('RSN1633_MANJIL_ABBAR--L.AT2', 2676, 0.020, 0.5145641, 'Manjil'),
)

STANDARD_GRAVITY = 9.80665


def _read_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _standard_records(content: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content.decode('utf-8'))))


def _standardize_with_suggestion(file_name: str, content: bytes):
    """按 inspect 给出的建议映射做标准化，模拟用户确认建议后的流程。"""

    inspection, _ = load_import_service.inspect(file_name, content)
    mapping = inspection['suggestedMapping']['mapping']
    return inspection, load_import_service.standardize(
        file_name=file_name,
        content=content,
        mapping=mapping,
    )


# --- 自描述格式：PEER NGA -------------------------------------------------


@pytest.mark.parametrize(('name', 'npts', 'dt', 'peak_g', 'event'), PEER_RECORDS)
def test_parse_peer_at2_reads_header_declared_metadata(
    name: str,
    npts: int,
    dt: float,
    peak_g: float,
    event: str,
) -> None:
    record = parse_peer_at2(_read_fixture(name))

    assert record.sample_count == npts
    assert record.dt == pytest.approx(dt)
    assert record.unit == 'g'
    assert record.quantity == 'ACCELERATION'
    assert record.format_name == 'PEER_NGA'
    assert max(abs(value) for value in record.values) == pytest.approx(peak_g, abs=1e-6)
    assert event in record.metadata['description']
    assert record.metadata['declaredNpts'] == npts


def test_peer_records_are_distinct() -> None:
    """三条记录必须解析出各自不同的结果，防止取值串台。"""

    parsed = [parse_peer_at2(_read_fixture(name)) for name, *_ in PEER_RECORDS]
    assert len({record.sample_count for record in parsed}) == 3
    assert len({record.dt for record in parsed}) == 3


def test_is_self_describing_format_matches_peer_suffixes() -> None:
    assert is_self_describing_format('RSN767.AT2')
    assert is_self_describing_format('record.at1')
    assert not is_self_describing_format('load.csv')
    assert not is_self_describing_format('load.txt')


def test_parse_peer_at2_rejects_npts_mismatch() -> None:
    """NPTS 与实际值数不符说明文件被截断或拼接，必须失败关闭。"""

    truncated = b'\n'.join([
        b'PEER NGA STRONG MOTION DATABASE RECORD',
        b'Synthetic, 1/1/2000, Test Station, 0',
        b'ACCELERATION TIME SERIES IN UNITS OF G',
        b'NPTS=   100, DT=   .0100 SEC,',
        b'  .1000000E-01  .2000000E-01',
        b'',
    ])
    with pytest.raises(LoadFormatError) as excinfo:
        parse_peer_at2(truncated)
    assert excinfo.value.code == 'PEER_NPTS_MISMATCH'


def test_parse_peer_at2_rejects_trailing_text_after_data() -> None:
    """数据块后出现非数值行说明文件被改过，静默跳过会得到错误时程。"""

    spliced = b'\n'.join([
        b'PEER NGA STRONG MOTION DATABASE RECORD',
        b'Synthetic, 1/1/2000, Test Station, 0',
        b'ACCELERATION TIME SERIES IN UNITS OF G',
        b'NPTS=   4, DT=   .0100 SEC,',
        b'  .1000000E-01  .2000000E-01',
        b'END OF RECORD',
        b'  .3000000E-01  .4000000E-01',
        b'',
    ])
    with pytest.raises(LoadFormatError) as excinfo:
        parse_peer_at2(spliced)
    assert excinfo.value.code == 'PEER_UNEXPECTED_TRAILING_TEXT'


def test_parse_peer_at2_requires_dt_in_header() -> None:
    without_dt = b'\n'.join([
        b'PEER NGA STRONG MOTION DATABASE RECORD',
        b'Synthetic, 1/1/2000, Test Station, 0',
        b'ACCELERATION TIME SERIES IN UNITS OF G',
        b'NPTS=   2',
        b'  .1000000E-01  .2000000E-01',
        b'',
    ])
    with pytest.raises(LoadFormatError) as excinfo:
        parse_peer_at2(without_dt)
    assert excinfo.value.code == 'PEER_MISSING_DT'


def test_parse_peer_at2_accepts_fortran_d_exponent() -> None:
    """部分导出用 Fortran 的 D 指数记法。"""

    record = parse_peer_at2(b'\n'.join([
        b'PEER NGA STRONG MOTION DATABASE RECORD',
        b'Synthetic, 1/1/2000, Test Station, 0',
        b'ACCELERATION TIME SERIES IN UNITS OF G',
        b'NPTS=   2, DT=   .0100 SEC,',
        b'  0.1500000D-01  -0.2500000D-01',
        b'',
    ]))
    assert record.values == pytest.approx((0.015, -0.025))


def test_peer_at2_inspect_exposes_two_column_table() -> None:
    """自描述格式展开成 time_s + acceleration，下游与通用表格同路。"""

    name, npts, dt, _, _ = PEER_RECORDS[0]
    inspection, rows = load_import_service.inspect(name, _read_fixture(name))

    assert inspection['format'] == 'PEER_NGA'
    assert inspection['rowCount'] == npts
    assert [column['name'] for column in inspection['columns']] == ['time_s', 'acceleration']
    assert inspection['selfDescribing']['dt'] == pytest.approx(dt)
    assert inspection['selfDescribing']['sourceUnit'] == 'g'
    assert float(rows[1]['time_s']) == pytest.approx(dt)


def test_peer_at2_suggestion_uses_header_not_guesswork() -> None:
    """自描述格式的 dt 与单位是读出来的，建议置信度必须为 HIGH。"""

    name = PEER_RECORDS[0][0]
    inspection, _ = load_import_service.inspect(name, _read_fixture(name))
    suggestion = inspection['suggestedMapping']

    assert suggestion['confidence'] == 'HIGH'
    channel = suggestion['mapping']['channels'][0]
    assert channel['sourceUnit'] == 'g'
    assert channel['applicationType'] == 'UNIFORM_EXCITATION'
    assert channel['quantity'] == 'ACCELERATION'
    assert suggestion['mapping']['loadKind'] == 'EARTHQUAKE'


@pytest.mark.parametrize(('name', 'npts', 'dt', 'peak_g', '_event'), PEER_RECORDS)
def test_peer_at2_standardizes_to_earthquake_contract(
    name: str,
    npts: int,
    dt: float,
    peak_g: float,
    _event: str,
) -> None:
    """端到端：AT2 上传 → 建议映射 → 标准 CSV，且满足既有地震契约。"""

    content = _read_fixture(name)
    _, standardized = _standardize_with_suggestion(name, content)
    records = _standard_records(standardized.content)

    assert len(records) == npts
    # platform_store._apply_agent_standard_earthquake_load 的放行条件。
    assert len({row['channel_id'] for row in records}) == 1
    assert all(row['load_kind'] == 'EARTHQUAKE' for row in records)
    assert all(row['application_type'] == 'UNIFORM_EXCITATION' for row in records)
    assert all(row['quantity'] == 'ACCELERATION' for row in records)
    assert all(row['unit'] == 'm/s2' for row in records)

    # 时间列必须是从 0 起步的等步长网格。
    for index, row in enumerate(records):
        assert float(row['time_s']) == pytest.approx(index * dt, abs=max(dt, 1.0) * 1e-9)

    peak_out = max(abs(float(row['value'])) for row in records)
    assert peak_out == pytest.approx(peak_g * STANDARD_GRAVITY, abs=1e-6)
    assert standardized.report['channels'][0]['conversionFactor'] == STANDARD_GRAVITY


# --- 单位换算 -------------------------------------------------------------


@pytest.mark.parametrize(('source_unit', 'factor'), [
    ('g', STANDARD_GRAVITY),
    ('m/s2', 1.0),
    ('m/s²', 1.0),
    ('cm/s2', 0.01),
    ('gal', 0.01),
    ('Gal', 0.01),
    ('mm/s2', 0.001),
])
def test_acceleration_unit_conversions(source_unit: str, factor: float) -> None:
    assert load_import_service._unit_conversion('ACCELERATION', source_unit) == (factor, 'm/s2')


def test_force_unit_conversions_unchanged() -> None:
    """力的换算是既有行为，扩表不能改动它。"""

    assert load_import_service._unit_conversion('FORCE', 'N') == (1.0, 'N')
    assert load_import_service._unit_conversion('FORCE', 'kN') == (1000.0, 'N')


def test_incompatible_unit_still_rejected() -> None:
    with pytest.raises(HTTPException) as excinfo:
        load_import_service._unit_conversion('ACCELERATION', 'kN')
    assert excinfo.value.detail['code'] == 'INCOMPATIBLE_UNIT'


def test_unsupported_suffix_rejected() -> None:
    with pytest.raises(HTTPException) as excinfo:
        load_import_service.inspect('model.dwg', b'anything')
    assert excinfo.value.detail['code'] == 'UNSUPPORTED_FILE_TYPE'


# --- 通用表格：列语义推断 -------------------------------------------------


def test_detect_time_column_rejects_step_index_decoy() -> None:
    """递增的序号列不是时间列。两点判据会误判，整列等差检测不会。"""

    profiles = [{'name': 'step'}, {'name': 'time'}, {'name': 'acc'}]
    rows = [
        {'step': '1', 'time': '0.00', 'acc': '0.01'},
        {'step': '2', 'time': '0.01', 'acc': '0.02'},
        {'step': '3', 'time': '0.02', 'acc': '0.03'},
    ]
    detection = detect_time_column(profiles, rows)
    assert detection.column == 'time'
    assert detection.step_s == pytest.approx(0.01)


def test_detect_time_column_rejects_non_uniform_column() -> None:
    profiles = [{'name': 'time'}, {'name': 'acc'}]
    rows = [
        {'time': '0.00', 'acc': '0.01'},
        {'time': '0.01', 'acc': '0.02'},
        {'time': '0.05', 'acc': '0.03'},
    ]
    assert detect_time_column(profiles, rows).column is None


def test_detect_time_column_tolerates_decimal_rounding() -> None:
    """真实时间列常有 1e-17 量级的十进制舍入，不能因此判成非等步长。"""

    profiles = [{'name': 'time'}, {'name': 'acc'}]
    rows = [
        {'time': repr(index * 0.01), 'acc': '0.1'}
        for index in range(6)
    ]
    detection = detect_time_column(profiles, rows)
    assert detection.column == 'time'
    assert detection.step_s == pytest.approx(0.01)


@pytest.mark.parametrize(('name', 'expected'), [
    ('acc(g)', 'g'),
    ('加速度(gal)', 'cm/s2'),
    ('a_m/s2', 'm/s2'),
    ('acc[mm/s2]', 'mm/s2'),
    ('range', None),
    ('acceleration', None),
])
def test_unit_from_name(name: str, expected: str | None) -> None:
    assert unit_from_name(name) == expected


@pytest.mark.parametrize(('peak', 'unit', 'confidence'), [
    (0.35, 'g', 'HIGH'),
    (3.5, 'm/s2', 'MEDIUM'),
    (12.0, 'm/s2', 'HIGH'),
    # 猜只在 g 与 m/s² 之间进行。超出这两个量级时不许硬猜一个 cm/s²——
    # 150 既可能是 gal 也可能是量纲写错的 m/s²，返回 None 让人来定。
    (150.0, None, 'LOW'),
    (350.0, None, 'LOW'),
    (0.0, None, 'LOW'),
])
def test_infer_acceleration_unit_by_magnitude(peak: float, unit: str | None, confidence: str) -> None:
    got_unit, got_confidence, _ = infer_acceleration_unit(peak)
    assert (got_unit, got_confidence) == (unit, confidence)


def test_column_name_unit_beats_magnitude_guess() -> None:
    """列名显式声明单位时不再按量级猜，置信度为 HIGH。"""

    content = b'time,acc(gal)\n0.0,0\n0.01,250.0\n0.02,-100.0\n0.03,0\n'
    inspection, _ = load_import_service.inspect('quake.csv', content)
    suggestion = inspection['suggestedMapping']

    assert suggestion['confidence'] == 'HIGH'
    assert suggestion['mapping']['channels'][0]['sourceUnit'] == 'cm/s2'


def test_ambiguous_magnitude_downgrades_confidence_with_warning() -> None:
    """3.6 既可能是 m/s² 也可能是极强的 g 记录，必须降置信度并警示。"""

    content = b'time,accel\n0.0,0\n0.02,3.6\n0.04,-1.2\n0.06,0\n'
    inspection, _ = load_import_service.inspect('quake.csv', content)
    suggestion = inspection['suggestedMapping']

    assert suggestion['confidence'] == 'MEDIUM'
    assert suggestion['mapping']['channels'][0]['sourceUnit'] == 'm/s2'
    assert any('置信度' in warning for warning in suggestion['warnings'])


def test_missing_time_column_reports_low_confidence() -> None:
    """没有时间列时不能凭空造一个步长，必须降级并要求显式提供。"""

    content = b'0.01\n0.02\n-0.03\n0.01\n'
    inspection, _ = load_import_service.inspect('quake.txt', content)
    suggestion = inspection['suggestedMapping']

    assert suggestion['confidence'] == 'LOW'
    assert suggestion['mapping']['time']['column'] is None
    assert any('time.stepS' in warning for warning in suggestion['warnings'])


def test_whitespace_delimited_txt_splits_into_columns() -> None:
    """地震记录常用连续空格对齐，csv.Sniffer 会失败并把整行当一格。"""

    content = b'0.00   0.0100\n0.01   0.0250\n0.02  -0.0180\n0.03   0.0090\n'
    inspection, rows = load_import_service.inspect('quake.txt', content)

    assert inspection['columnCount'] == 2
    assert inspection['delimiter'] == 'whitespace'
    suggestion = inspection['suggestedMapping']
    assert suggestion['mapping']['time']['column'] == 'column_1'
    assert suggestion['mapping']['channels'][0]['valueColumn'] == 'column_2'
    assert float(rows[1]['column_1']) == pytest.approx(0.01)


def test_tab_delimited_txt_still_parsed_by_sniffer() -> None:
    content = b'time\tacc\n0.00\t0.01\n0.01\t0.02\n0.02\t-0.03\n'
    inspection, _ = load_import_service.inspect('quake.txt', content)
    assert inspection['columnCount'] == 2
    assert inspection['suggestedMapping']['mapping']['time']['column'] == 'time'


def test_alternatives_offer_other_columns_and_units() -> None:
    """建议是候选，界面要能改选，所以备选列与单位必须一起返回。"""

    content = b'time,acc,temp\n0.0,0.1,20\n0.01,0.2,21\n0.02,-0.1,22\n'
    inspection, _ = load_import_service.inspect('quake.csv', content)
    alternatives = inspection['suggestedMapping']['alternatives']

    assert 'temp' in alternatives['valueColumns']
    # 可选单位收窄到 g / m/s²：界面上不再提供猜不出来的 gal 与 mm/s²。
    assert alternatives['sourceUnits'] == ['g', 'm/s2']


def test_declared_unit_outside_narrowed_set_stays_selectable() -> None:
    """文件声明 gal 时要把 gal 一起给界面，否则下拉框渲染成空的像是没识别出来。"""

    content = b'time,acc(gal)\n0.0,0\n0.01,250.0\n0.02,-100.0\n0.03,0\n'
    inspection, _ = load_import_service.inspect('quake.csv', content)

    assert inspection['suggestedMapping']['alternatives']['sourceUnits'] == ['g', 'm/s2', 'cm/s2']


# --- 单位来源与自动标准化决策 ---------------------------------------------


@pytest.mark.parametrize('name', [record[0] for record in PEER_RECORDS])
def test_peer_header_unit_is_read_not_guessed(name: str) -> None:
    """PEER 头写着 g 就按声明采信。

    这三条记录的峰值都落在 g 区间，按量级猜也能得到 g——所以只断言单位相等
    是测不出区别的，必须断言来源是文件头声明。
    """

    inspection, _ = load_import_service.inspect(name, _read_fixture(name))
    suggestion = inspection['suggestedMapping']

    assert suggestion['unitSource'] == UNIT_SOURCE_HEADER
    assert suggestion['mapping']['channels'][0]['sourceUnit'] == 'g'
    assert suggestion['standardizeDecision'] == DECISION_AUTO


def test_column_name_declaration_allows_auto_standardization() -> None:
    """列名声明单位是从文件内容里读出来的，够格自动标准化。"""

    content = b'time,acc(g)\n0.0,0\n0.01,0.35\n0.02,-0.2\n0.03,0\n'
    inspection, _ = load_import_service.inspect('quake.csv', content)
    suggestion = inspection['suggestedMapping']

    assert suggestion['unitSource'] == UNIT_SOURCE_COLUMN_NAME
    assert suggestion['standardizeDecision'] == DECISION_AUTO


def test_magnitude_guess_always_asks_even_at_high_confidence() -> None:
    """按量级猜中 g 区间也只是猜——猜错就是 9.8 倍荷载误差，必须回到审批界面。"""

    content = b'time,accel\n0.0,0\n0.01,0.35\n0.02,-0.2\n0.03,0\n'
    inspection, _ = load_import_service.inspect('quake.csv', content)
    suggestion = inspection['suggestedMapping']

    assert suggestion['confidence'] == 'HIGH'
    assert suggestion['unitSource'] == UNIT_SOURCE_MAGNITUDE
    assert suggestion['standardizeDecision'] == DECISION_ASK
    assert any('未声明单位' in warning for warning in suggestion['warnings'])


def test_file_name_declaration_is_not_enough_for_auto() -> None:
    """文件名不是文件内容，改个名就变，不能凭它自动转。"""

    content = b'time,accel\n0.0,0\n0.01,0.35\n0.02,-0.2\n0.03,0\n'
    inspection, _ = load_import_service.inspect('quake_g.csv', content)
    suggestion = inspection['suggestedMapping']

    assert suggestion['mapping']['channels'][0]['sourceUnit'] == 'g'
    assert suggestion['unitSource'] == UNIT_SOURCE_FILE_NAME
    assert suggestion['standardizeDecision'] == DECISION_ASK


def test_unresolvable_unit_asks_and_fails_closed_on_standardize() -> None:
    """量级超出 g 与 m/s² 时不猜，且这份建议直接拿去标准化必须被拒。"""

    content = b'time,accel\n0.0,0\n0.01,250.0\n0.02,-100.0\n0.03,0\n'
    inspection, _ = load_import_service.inspect('quake.csv', content)
    suggestion = inspection['suggestedMapping']

    assert suggestion['mapping']['channels'][0]['sourceUnit'] is None
    assert suggestion['unitSource'] == UNIT_SOURCE_UNKNOWN
    assert suggestion['standardizeDecision'] == DECISION_ASK

    with pytest.raises(HTTPException) as excinfo:
        load_import_service.standardize(
            file_name='quake.csv', content=content, mapping=suggestion['mapping'],
        )
    assert excinfo.value.detail['code'] == 'INCOMPATIBLE_UNIT'


def test_missing_time_column_asks_even_with_declared_unit() -> None:
    """单位读出来了但时间轴没读出来，一样不能自动转。"""

    content = b'acc(g)\n0.01\n0.02\n-0.03\n0.01\n'
    inspection, _ = load_import_service.inspect('quake.csv', content)
    suggestion = inspection['suggestedMapping']

    assert suggestion['unitSource'] == UNIT_SOURCE_COLUMN_NAME
    assert suggestion['mapping']['time']['column'] is None
    assert suggestion['standardizeDecision'] == DECISION_ASK


def test_declared_unit_stays_selectable_even_when_not_inferable() -> None:
    """文件自己声明 gal 时，界面选项要带上它，否则下拉框会渲染成空值。"""

    content = b'time,acc(gal)\n0.0,0\n0.01,250.0\n0.02,-100.0\n0.03,0\n'
    inspection, _ = load_import_service.inspect('quake.csv', content)
    suggestion = inspection['suggestedMapping']

    assert suggestion['mapping']['channels'][0]['sourceUnit'] == 'cm/s2'
    assert suggestion['alternatives']['sourceUnits'] == ['g', 'm/s2', 'cm/s2']


@pytest.mark.parametrize(('header', 'value', 'expected'), [
    (b'acc(g)', b'0.5', 0.5 * STANDARD_GRAVITY),
    (b'acc(m/s2)', b'3.6', 3.6),
    (b'acc(gal)', b'250.0', 2.5),
    (b'acc(mm/s2)', b'3000.0', 3.0),
])
def test_plain_csv_standardizes_to_metres_per_second_squared(
    header: bytes,
    value: bytes,
    expected: float,
) -> None:
    """通用 CSV 也要走完整条转换链，输出统一为 m/s2。"""

    content = b'time,' + header + b'\n0.0,0\n0.01,' + value + b'\n0.02,0\n'
    _, standardized = _standardize_with_suggestion('quake.csv', content)
    records = _standard_records(standardized.content)

    assert float(records[1]['value']) == pytest.approx(expected)
    assert all(row['unit'] == 'm/s2' for row in records)
    assert all(row['load_kind'] == 'EARTHQUAKE' for row in records)


def test_standardization_is_deterministic() -> None:
    """同一文件两次标准化必须得到同一摘要，否则审批冻结的 SHA256 会失效。"""

    name = PEER_RECORDS[2][0]
    content = _read_fixture(name)
    _, first = _standardize_with_suggestion(name, content)
    _, second = _standardize_with_suggestion(name, content)
    assert first.digest == second.digest


def test_report_records_unit_normalization_evidence() -> None:
    """报告要留下换算依据，产物才可追溯。"""

    content = b'time,acc(gal)\n0.0,0\n0.01,250.0\n0.02,0\n'
    _, standardized = _standardize_with_suggestion('quake.csv', content)
    channel = standardized.report['channels'][0]

    assert channel['sourceUnit'] == 'cm/s2'
    assert channel['standardUnit'] == 'm/s2'
    assert channel['conversionFactor'] == 0.01


# --- 绑定到求解器输入 -----------------------------------------------------


def _store_with_standard_artifact(content: bytes, *, sha: str = 'e' * 64) -> PlatformStore:
    store = PlatformStore.__new__(PlatformStore)
    store.get_artifact = lambda _artifact_id: SimpleNamespace(  # type: ignore[method-assign]
        artifact=SimpleNamespace(artifact_id='load-eq-1', kind='CSV_TIMESERIES', sha256=sha),
        content=content,
    )
    return store


def _earthquake_params(**overrides) -> dict:
    return {
        'loadKind': 'EARTHQUAKE',
        'loadDatasetArtifactId': 'load-eq-1',
        'loadDatasetSha256': 'e' * 64,
        **overrides,
    }


def test_peer_at2_reaches_solver_input_as_bare_values(tmp_path: Path) -> None:
    """完整链路：AT2 → 标准 CSV → 求解器裸值 txt。

    裸 txt 是求解器真正读的东西，行数必须等于 NPTS，首末值必须是换算后的
    m/s²——少一行或多一行都会让时程与 dt 错位。
    """

    name, npts, dt, peak_g, _ = PEER_RECORDS[0]
    content = _read_fixture(name)
    _, standardized = _standardize_with_suggestion(name, content)
    records = _standard_records(standardized.content)

    store = _store_with_standard_artifact(standardized.content)
    baseline_config: dict = {'bridge_model': {'name': 'STbridge', 'metadata': {}}}
    optimization_config: dict = {'load_cases': [{'name': 'earthquake'}]}

    evidence = store._apply_agent_standard_earthquake_load(
        baseline_config,
        optimization_config,
        tmp_path,
        _earthquake_params(),
    )

    solver_input = Path(baseline_config['load_case']['path'])
    assert solver_input.name == 'agent_earthquake_acceleration_mps2.txt'
    lines = solver_input.read_text(encoding='utf-8').splitlines()
    assert len(lines) == npts
    assert float(lines[0]) == pytest.approx(float(records[0]['value']))
    assert float(lines[-1]) == pytest.approx(float(records[-1]['value']))
    assert max(abs(float(line)) for line in lines) == pytest.approx(
        peak_g * STANDARD_GRAVITY, abs=1e-6,
    )

    assert baseline_config['load_case']['load_type'] == 'earthquake'
    assert baseline_config['load_case']['dt'] == pytest.approx(dt)
    assert baseline_config['load_case']['duration'] == pytest.approx(dt * (npts - 1))
    assert optimization_config['load_cases'][0]['path'] == str(solver_input)

    assert evidence['sampleCount'] == npts
    assert evidence['timeStepS'] == pytest.approx(dt)
    assert evidence['unit'] == 'm/s2'
    assert evidence['component'] == 'UX'
    assert len(evidence['solverInputSha256']) == 64


def test_solver_binding_rejects_hash_mismatch(tmp_path: Path) -> None:
    """审批冻结的 SHA256 与制品不符时必须 409，防止换掉荷载再执行。"""

    name = PEER_RECORDS[2][0]
    _, standardized = _standardize_with_suggestion(name, _read_fixture(name))
    store = _store_with_standard_artifact(standardized.content, sha='e' * 64)

    with pytest.raises(HTTPException) as excinfo:
        store._apply_agent_standard_earthquake_load(
            {'bridge_model': {}},
            {},
            tmp_path,
            _earthquake_params(loadDatasetSha256='f' * 64),
        )
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail['code'] == 'LOAD_ARTIFACT_HASH_MISMATCH'


def test_plain_csv_in_gal_reaches_solver_input_in_mps2(tmp_path: Path) -> None:
    """通用 CSV 与 PEER 走同一条绑定路径，单位也必须已经换成 m/s²。"""

    content = b'time,acc(gal)\n0.0,0\n0.01,250.0\n0.02,-100.0\n0.03,0\n'
    _, standardized = _standardize_with_suggestion('quake.csv', content)
    store = _store_with_standard_artifact(standardized.content)
    baseline_config: dict = {'bridge_model': {}}

    evidence = store._apply_agent_standard_earthquake_load(
        baseline_config, {}, tmp_path, _earthquake_params(),
    )

    lines = Path(baseline_config['load_case']['path']).read_text(encoding='utf-8').splitlines()
    assert [float(line) for line in lines] == pytest.approx([0.0, 2.5, -1.0, 0.0])
    assert evidence['timeStepS'] == pytest.approx(0.01)
    assert evidence['unit'] == 'm/s2'


# --- 表头/前言判定 ---------------------------------------------------------


def test_single_column_numeric_txt_keeps_every_sample() -> None:
    """单列纯数值文件没有表头，第一个采样点不能被当成列名吃掉。

    csv.Sniffer().has_header 对这类文件会误判成有表头。丢掉的那一点不会报
    错——输出的样本数、等步长、峰值全都合法，只是整条时程少一点且平移了一个
    dt，还带着一条合法的哈希链。真实记录（OpenSees record01.txt）就是这个形状。
    """

    content = b'0\n-0.0064\n-0.00603\n0.00053\n0.00774\n'
    inspection, rows = load_import_service.inspect('record01.txt', content)

    assert inspection['rowCount'] == 5
    assert [column['name'] for column in inspection['columns']] == ['column_1']
    assert [float(row['column_1']) for row in rows] == pytest.approx(
        [0.0, -0.0064, -0.00603, 0.00053, 0.00774]
    )


def test_leading_preamble_is_stripped_and_reported() -> None:
    """数据前的说明文字要摘掉并回报，不能当成表头也不能静默丢弃。"""

    content = (
        b'The Northridge (USA) earthquake of January 17, 1994.\n'
        b'Source: PEER Strong Motion Database\n'
        b'Frequency range: 0.12-23.0 Hz\n'
        b'Time[s]\tAccel[g]\n'
        b'0.0000\t-0.0012\n'
        b'0.0100\t-0.0007\n'
        b'0.0200\t-0.0003\n'
    )
    inspection, rows = load_import_service.inspect('northridge.txt', content)

    assert inspection['rowCount'] == 3
    assert [column['name'] for column in inspection['columns']] == ['Time[s]', 'Accel[g]']
    assert len(inspection['preambleLines']) == 3
    assert 'Northridge' in inspection['preambleLines'][0]
    assert float(rows[1]['Time[s]']) == pytest.approx(0.01)


def test_preamble_header_split_on_whitespace_recovers_declared_unit() -> None:
    """表头用空格、数据用制表符时，表头仍要切成列名，单位才能从声明里读出来。

    否则表头列数与数据宽度对不上会被当前言摘掉，单位只能靠峰值量级猜——同一
    条记录按 g 还是 m/s² 解读差 9.8 倍。
    """

    content = (
        b'Recording station: 090 CDMG STATION 24278\n'
        b'Time[s] Accel[g]\n'
        b'0.0000\t-0.0012\n'
        b'0.0100\t0.5683\n'
        b'0.0200\t-0.0003\n'
    )
    inspection, _ = load_import_service.inspect('northridge.txt', content)
    suggestion = inspection['suggestedMapping']

    assert [column['name'] for column in inspection['columns']] == ['Time[s]', 'Accel[g]']
    assert suggestion['confidence'] == 'HIGH'
    assert suggestion['mapping']['channels'][0]['sourceUnit'] == 'g'
    assert any('Accel[g]' in reason for reason in suggestion['reasons'])


def test_dat_suffix_accepted_as_table() -> None:
    """.dat 是强震记录常见后缀，内容就是普通表格，不该在上传口被拒。"""

    content = b'Time[s]\tAccel[g]\n0.00\t0.01\n0.01\t0.02\n0.02\t0.03\n'
    inspection, _ = load_import_service.inspect('northridge.dat', content)

    assert inspection['format'] == 'DAT'
    assert inspection['rowCount'] == 3
    assert load_artifact_service.mime_type('northridge.dat') == 'text/plain'


# --- PEER 旧 PGA 库头部写法 -----------------------------------------------


@pytest.mark.parametrize('header', [
    b'   6 0.00500 NPTS, DT',
    b'   6 0.00500 NPTS DT',
])
def test_parse_peer_at2_accepts_old_pga_header(header: bytes) -> None:
    """旧 PGA 库把数值写在前、关键字写在后，两种写法见 ReadRecord.tcl。"""

    record = parse_peer_at2(b'\n'.join([
        b'PACIFIC ENGINEERING AND ANALYSIS STRONG-MOTION DATA',
        b'IMPERIAL VALLEY 10/15/79 2319, EL CENTRO ARRAY 6, 230',
        b'ACCELERATION TIME HISTORY IN UNITS OF G',
        header,
        b'  .1000000E-01  .2000000E-01  .3000000E-01',
        b'  .4000000E-01  .5000000E-01  .6000000E-01',
        b'',
    ]))
    assert record.sample_count == 6
    assert record.dt == pytest.approx(0.005)
    assert record.unit == 'g'
    assert record.metadata['declaredNpts'] == 6


def test_old_pga_header_still_guards_truncation() -> None:
    """旧写法也要读到 NPTS，否则截断守卫会静默失效。"""

    with pytest.raises(LoadFormatError) as excinfo:
        parse_peer_at2(b'\n'.join([
            b'PACIFIC ENGINEERING AND ANALYSIS STRONG-MOTION DATA',
            b'IMPERIAL VALLEY 10/15/79 2319, EL CENTRO ARRAY 6, 230',
            b'ACCELERATION TIME HISTORY IN UNITS OF G',
            b'   6 0.00500 NPTS, DT',
            b'  .1000000E-01  .2000000E-01',
            b'',
        ]))
    assert excinfo.value.code == 'PEER_NPTS_MISMATCH'
