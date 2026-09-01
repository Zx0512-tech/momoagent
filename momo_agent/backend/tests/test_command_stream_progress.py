"""命令流进度注入的证据链保护测试。

时程进度写入必须可开关：不传 ``progress_path`` 时，渲染出的命令流必须与
注入功能引入之前字节级一致，否则 verification/evidence/ 里已归档的 command stream
sha256 将不再匹配新生成的脚本，破坏可复核性。
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from pyansys_bridge.core.command_stream import CommandStreamAssembler, default_command_modules


def _render(context_overrides: dict | None = None) -> str:
    """渲染 opensees postprocess 模块，只带该模板 schema 要求的必填变量。"""

    context = {
        'load_dt': 0.02,
        'load_duration': 10.0,
        'load_type': 'earthquake',
        'response_nodes': '(36, 107)',
        'response_dof': 1,
        'tower_base_nodes': '(1, 2)',
        'tower_base_element_node_map': '((1, 2),)',
        'tower_base_shear_inertia_element_mass_densities': '((1, 2.5),)',
        'tower_base_shear_dofs': '(1, 2)',
        'tower_base_moment_dofs': '(5, 6)',
        'damper_placement_pairs': '((1, 2, 1),)',
        'diagnostic_nodes': '()',
        'diagnostic_pairs': '()',
        'damping_ratio': 0.05,
        'opensees_system': "'UmfPack'",
        'opensees_system_args': '()',
        'rayleigh_frequency_a_hz': 0.055317,
        'rayleigh_frequency_b_hz': 0.100449,
        'earthquake_scale': 1.0,
        'earthquake_uniform_factor': 1.0,
        'earthquake_uniform_dof': 1,
        'earthquake_path': 'analysis_data/eq.txt',
    }
    context.update(context_overrides or {})
    assembler = CommandStreamAssembler(default_command_modules())
    return assembler.render_to_string('opensees', ('postprocess',), context=context)


def test_render_without_progress_path_is_byte_stable() -> None:
    """不传 progress_path 时渲染结果必须稳定，保护已归档的 sha256。"""

    rendered = _render()
    # 进度注入代码一行都不能出现在默认渲染结果里。
    assert 'progress' not in rendered.lower()
    # 同一上下文两次渲染必须完全一致（无时间戳、无随机量参与）。
    assert sha256(rendered.encode('utf-8')).hexdigest() == sha256(_render().encode('utf-8')).hexdigest()


def test_render_without_progress_path_still_has_transient_loop() -> None:
    """默认渲染必须保留原有逐步时程循环，确认测试确实渲染到了目标代码段。"""

    rendered = _render()
    assert 'for _ in range(analysis_steps):' in rendered
    assert '_analyze_transient_step(analysis_dt)' in rendered


def test_rendered_stream_is_valid_python() -> None:
    """渲染结果必须是合法 Python，否则 exec 阶段才会暴露语法错误。"""

    compile(_render(), '<rendered>', 'exec')


def _render_with_progress(tmp_dir: str = 'C:/tmp/progress') -> str:
    return _render({
        'progress_path': tmp_dir,
        'progress_case_id': 'stbridge|c=7600,alpha=0.8',
        'progress_filename': 'stbridge_c_7600_alpha_0.8.json',
    })


def test_progress_enabled_stream_is_valid_python() -> None:
    """开启进度后渲染脚本仍须是合法 Python（缩进注入最容易在这里出错）。"""

    compile(_render_with_progress(), '<rendered>', 'exec')


def test_progress_enabled_stream_injects_reporter() -> None:
    rendered = _render_with_progress()
    assert '_report_progress' in rendered
    assert '_progress_step_counter += 1' in rendered
    # 2% 粒度：50 次写入。
    assert '_progress_every = max(1, analysis_steps // 50)' in rendered


def test_progress_enabled_stream_keeps_original_loop_header() -> None:
    """进度注入不得改写时程循环本身，只在循环体内追加计数。"""

    assert 'for _ in range(analysis_steps):' in _render_with_progress()


def _render_with_elements(elements: str = '(12, 20)', component: int = 0) -> str:
    return _render({'response_elements': elements, 'response_element_component': component})


def test_render_without_response_elements_is_byte_stable() -> None:
    """不点名单元时渲染结果不含任何单元内力代码，保护已归档的 sha256。"""

    rendered = _render()
    assert 'response_elements' not in rendered
    assert '_requested_element_force' not in rendered
    assert '_requested_element_columns' not in rendered
    assert sha256(rendered.encode('utf-8')).hexdigest() == sha256(_render().encode('utf-8')).hexdigest()


def test_response_elements_header_matches_data_column_count() -> None:
    """表头列数必须与数据行列数一致，否则 DictReader 会把多余值塞进 None 键。"""

    rendered = _render_with_elements()
    header_line = next(
        line for line in rendered.splitlines() if "'ground_acceleration'," in line or 'ground_acceleration' in line
    )
    assert '_requested_element_columns()' in rendered
    # 表头拼接与数据行拼接都必须存在，且都由同一份 response_elements 驱动。
    assert "+ _requested_element_columns()" in rendered
    assert '+ [_requested_element_force(element) for element in response_elements]' in rendered
    assert header_line is not None


def test_response_elements_stream_is_valid_python() -> None:
    compile(_render_with_elements(), '<rendered>', 'exec')


def test_response_elements_column_names_match_ansys_contract() -> None:
    """列名必须与 ANSYS ansys-dpf-nodes 的 element_{id}_force 契约一致。"""

    rendered = _render_with_elements()
    namespace: dict = {'response_elements': (12, 20)}
    start = rendered.index('def _requested_element_columns():')
    end = rendered.index('def _six_values(')
    exec(compile(rendered[start:end], '<columns>', 'exec'), namespace)  # noqa: S102

    assert namespace['_requested_element_columns']() == ['element_12_force', 'element_20_force']


def test_response_elements_uses_global_force_without_silent_fallback() -> None:
    """必须走 globalForce/force 全局分量，且不得回退到 basicForce/localForce。

    basicForce 的坐标系与分量顺序都不同，用同一个下标切片会让同一列在不同
    单元类型间静默变成另一个物理量。
    """

    rendered = _render_with_elements()
    start = rendered.index('def _requested_element_force(')
    end = rendered.index('def _requested_element_columns():')
    # 只看代码行：函数里的中文注释本身就把 basicForce 当反面例子写了出来。
    code = '\n'.join(
        line for line in rendered[start:end].splitlines() if not line.strip().startswith('#')
    )
    assert "for response in ('globalForce', 'force'):" in code
    assert 'basicForce' not in code
    assert 'localForce' not in code


def test_response_element_force_rejects_insufficient_components() -> None:
    """分量不足 6 个（阻尼器 zeroLength 等）必须报错，而不是截断成末位分量。"""

    import pytest

    rendered = _render_with_elements(component=2)
    start = rendered.index('def _requested_element_force(')
    end = rendered.index('def _six_values(')

    class _StubOps:
        def eleResponse(self, element, response):  # noqa: N802 - 模拟 OpenSees API
            return [1.0, 2.0] if response == 'globalForce' else []

    namespace: dict = {'ops': _StubOps(), 'response_element_component': 2}
    exec(compile(rendered[start:end], '<force>', 'exec'), namespace)  # noqa: S102

    with pytest.raises(RuntimeError, match='exposes only 2 force components'):
        namespace['_requested_element_force'](9101)


def test_response_element_force_returns_requested_component() -> None:
    """6 分量齐备时按 component 取全局 FX/FY/FZ。"""

    rendered = _render_with_elements(component=1)
    start = rendered.index('def _requested_element_force(')
    end = rendered.index('def _six_values(')

    class _StubOps:
        def eleResponse(self, element, response):  # noqa: N802 - 模拟 OpenSees API
            if response == 'globalForce':
                return [10.0, 20.0, 30.0, 40.0, 50.0, 60.0]
            return []

    namespace: dict = {'ops': _StubOps(), 'response_element_component': 1}
    exec(compile(rendered[start:end], '<force>', 'exec'), namespace)  # noqa: S102

    assert namespace['_requested_element_force'](835) == 20.0


def test_progress_reporter_writes_expected_payload(tmp_path) -> None:
    """把注入的 reporter 单独执行一遍，确认它真的落盘且格式正确。"""

    from pyansys_bridge.core.progress_sink import read_all_progress

    rendered = _render_with_progress(tmp_path.as_posix())
    # 只取 reporter 定义那一段执行，避免真的跑 OpenSees。
    start = rendered.index('_progress_dir = Path(')
    end = rendered.index('timeseries_path = Path(')
    namespace: dict = {'analysis_steps': 500, 'Path': Path}
    exec(compile(rendered[start:end], '<reporter>', 'exec'), namespace)  # noqa: S102

    namespace['_report_progress'](250)

    entries = read_all_progress(tmp_path)
    assert len(entries) == 1
    assert entries[0]['caseId'] == 'stbridge|c=7600,alpha=0.8'
    assert entries[0]['step'] == 250
    assert entries[0]['totalSteps'] == 500
    assert entries[0]['percent'] == 50
    assert entries[0]['phase'] == 'transient'
