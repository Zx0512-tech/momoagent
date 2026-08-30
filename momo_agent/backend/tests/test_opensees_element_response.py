"""OpenSees 点名单元内力输出：solver 层注入与 CSV 列契约。

单元内力列必须与 ANSYS ``ansys-dpf-nodes`` 的 ``element_{id}_force`` 契约一致，
且未点名单元时命令流必须与该功能引入前字节级一致，否则 evidence/ 里已归档的
command stream sha256 不再匹配。
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import NamedTuple

import pytest

from pyansys_bridge.core import command_stream as command_stream_module
from pyansys_bridge.core.command_stream import CommandStreamAssembler, template_command_modules
from pyansys_bridge.core.openseespy_inproc_solver import OpenSeesPyInProcSolver
from pyansys_bridge.models import BridgeModel, DamperParams, LoadCase


@pytest.fixture
def bridge_model() -> BridgeModel:
    return BridgeModel(name='stbridge', source_path='fixture.txt', metadata={'modal_modes': 20})


@pytest.fixture
def load_case(tmp_path: Path) -> LoadCase:
    record = tmp_path / 'eq.txt'
    record.write_text('0.0\n0.01\n0.02\n', encoding='utf-8')
    return LoadCase(
        name='EQ-01',
        load_type='earthquake',
        path=str(record),
        dt=0.02,
        duration=10.0,
        scale=1.0,
        direction={'x': 1.0, 'y': 0.0, 'z': 0.0},
    )


@pytest.fixture
def damper_params() -> DamperParams:
    return DamperParams(c=7600.0, alpha=0.8)


def _configured(solver: OpenSeesPyInProcSolver, bridge_model, load_case, damper_params):
    solver.prepare_model(bridge_model)
    solver.apply_load_case(load_case)
    solver.set_damper_params(damper_params)
    return solver


def _render(solver: OpenSeesPyInProcSolver, load_case) -> str:
    """按 solve() 的方式渲染整条命令流，但不需要 openseespy 运行时。"""

    from pyansys_bridge.core.command_stream import roles_for_load_case

    roles = roles_for_load_case(
        load_case,
        include_modal=solver.include_modal,
        solver=solver.solver_name,
    )
    return CommandStreamAssembler(solver.modules).render_to_string(
        'opensees',
        roles,
        context=solver._command_context(),
        module_names={'damper': solver.damper_module},
    )


class _WriterowShape(NamedTuple):
    base_columns: int
    extension: str


def _timeseries_writerow_shapes(rendered: str) -> tuple[_WriterowShape, _WriterowShape]:
    """量出 timeseries 表头与数据行各自的基础列数和拼接式。

    审计的原话指控是"数据行比表头多列"，所以这里不查字符串在不在，而是把
    两侧的 ``[...] + <ext>`` 结构真的解析出来比基数。
    """

    header = data = None
    for node in ast.walk(ast.parse(rendered)):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == 'writerow'
            and node.args
        ):
            continue
        arg = node.args[0]
        if not isinstance(arg, ast.BinOp) or not isinstance(arg.left, ast.List):
            continue
        shape = _WriterowShape(len(arg.left.elts), ast.unparse(arg.right))
        if 'columns' in shape.extension:
            header = shape
        elif 'force' in shape.extension:
            data = shape
    assert header is not None, 'no timeseries header writerow with element extension'
    assert data is not None, 'no timeseries data writerow with element extension'
    return header, data


def test_command_context_without_elements_has_no_element_keys(
    bridge_model, load_case, damper_params
) -> None:
    """默认不点名单元时不得注入变量：空元组的 repr 在 Jinja 里是真值。"""

    solver = _configured(OpenSeesPyInProcSolver(), bridge_model, load_case, damper_params)

    context = solver._command_context()

    assert 'response_elements' not in context
    assert 'response_element_component' not in context


def test_command_context_with_elements_injects_python_literal(
    bridge_model, load_case, damper_params
) -> None:
    solver = _configured(
        OpenSeesPyInProcSolver(response_elements=(12, 20), response_element_component=1),
        bridge_model,
        load_case,
        damper_params,
    )

    context = solver._command_context()

    # 模板直接把它当 Python 字面量插进脚本，必须是 repr 而不是 list。
    assert context['response_elements'] == '(12, 20)'
    assert context['response_element_component'] == 1


def test_rendered_stream_without_elements_is_byte_stable(
    bridge_model, load_case, damper_params
) -> None:
    """未点名单元的命令流必须不含任何单元内力代码，保护已归档 sha256。"""

    solver = _configured(OpenSeesPyInProcSolver(), bridge_model, load_case, damper_params)

    rendered = _render(solver, load_case)

    assert '_requested_element_force' not in rendered
    assert '_requested_element_columns' not in rendered
    assert sha256(rendered.encode('utf-8')).hexdigest() == sha256(
        _render(solver, load_case).encode('utf-8')
    ).hexdigest()


def _template_copy_with(tmp_path: Path, relative: str, content: str) -> Path:
    """复制模板树到临时目录并替换其中一份，避免改写受控文件。"""

    packaged_root = Path(command_stream_module.__file__).resolve().parents[1] / 'templates'
    root = tmp_path / 'templates'
    shutil.copytree(packaged_root, root)
    (root / relative).write_text(content, encoding='utf-8')
    return root


def test_default_render_matches_committed_template_byte_for_byte(
    tmp_path, bridge_model, load_case, damper_params
) -> None:
    """默认渲染必须与仓库已提交版本字节一致。

    自比一致证明不了这件事：条件块的空白控制写错时（``{%- if %}`` 前面留空行
    会净吃掉一个换行），两次渲染仍然相同，但与归档证据已经不匹配。
    """

    relative = 'opensees/postprocess.pyfrag'
    git_path = f'momo_competition_submission/pyansys_bridge/templates/{relative}'
    repo_root = subprocess.run(
        ['git', 'rev-parse', '--show-toplevel'],
        capture_output=True, text=True, encoding='utf-8', check=True,
    ).stdout.strip()
    shown = subprocess.run(
        ['git', 'show', f'HEAD:{git_path}'],
        capture_output=True, text=True, encoding='utf-8', cwd=repo_root,
    )
    if shown.returncode != 0 or not shown.stdout.strip():
        pytest.skip('committed template unavailable')

    solver = _configured(OpenSeesPyInProcSolver(), bridge_model, load_case, damper_params)
    current = _render(solver, load_case)

    # 在模板树副本上改写，不碰受版本控制的文件。
    committed_root = _template_copy_with(tmp_path, relative, shown.stdout)
    solver.modules = template_command_modules(committed_root)
    committed = _render(solver, load_case)

    assert sha256(current.encode('utf-8')).hexdigest() == sha256(
        committed.encode('utf-8')
    ).hexdigest()


def test_rendered_stream_with_elements_compiles(bridge_model, load_case, damper_params) -> None:
    solver = _configured(
        OpenSeesPyInProcSolver(response_elements=(12, 20)),
        bridge_model,
        load_case,
        damper_params,
    )

    compile(_render(solver, load_case), '<rendered>', 'exec')


def test_timeseries_header_and_data_columns_stay_in_lockstep(
    bridge_model, load_case, damper_params
) -> None:
    """表头与数据行必须同时拼上单元列。

    草稿只给数据行拼了值、没给表头拼列名，csv.DictReader 会把多余的值塞进
    None 键，后处理 float() 直接崩。这条断言就是守这个。
    """

    solver = _configured(
        OpenSeesPyInProcSolver(response_elements=(12, 20)),
        bridge_model,
        load_case,
        damper_params,
    )

    rendered = _render(solver, load_case)

    header, data = _timeseries_writerow_shapes(rendered)

    assert header.base_columns == data.base_columns
    assert header.extension == '_requested_element_columns()'
    assert data.extension == (
        '[_requested_element_force(element) for element in response_elements]'
    )


def test_case_fingerprint_separates_element_requests(
    bridge_model, load_case, damper_params
) -> None:
    """点名单元会改变输出列，指纹必须区分，否则缓存会串味。"""

    fingerprints = {
        label: _configured(
            OpenSeesPyInProcSolver(**kwargs), bridge_model, load_case, damper_params
        ).case_fingerprint()
        for label, kwargs in {
            'base': {},
            'elem_12': {'response_elements': (12,)},
            'elem_20': {'response_elements': (20,)},
            'elem_12_20': {'response_elements': (12, 20)},
            'comp_1': {'response_elements': (12,), 'response_element_component': 1},
        }.items()
    }

    assert len(set(fingerprints.values())) == len(fingerprints)


def test_design_metadata_records_elements_only_when_requested(
    bridge_model, load_case, damper_params
) -> None:
    """未点名时不得凭空多出键，否则历史算例的归档元数据会变。"""

    without = _configured(OpenSeesPyInProcSolver(), bridge_model, load_case, damper_params)
    with_elements = _configured(
        OpenSeesPyInProcSolver(response_elements=(12, 20), response_element_component=1),
        bridge_model,
        load_case,
        damper_params,
    )

    assert 'response_elements' not in without.design_metadata()
    assert with_elements.design_metadata()['response_elements'] == [12, 20]
    assert with_elements.design_metadata()['response_element_component'] == 1


@pytest.mark.parametrize(
    ('kwargs', 'message'),
    [
        ({'response_elements': (12, 12)}, 'must not contain duplicates'),
        ({'response_elements': (0,)}, 'must be positive'),
        ({'response_elements': (-3,)}, 'must be positive'),
        ({'response_element_component': 3}, 'must be 0(X)/1(Y)/2(Z)'),
        ({'response_element_component': -1}, 'must be 0(X)/1(Y)/2(Z)'),
    ],
)
def test_constructor_rejects_invalid_element_requests(kwargs: dict, message: str) -> None:
    with pytest.raises(ValueError, match=re.escape(message)):
        OpenSeesPyInProcSolver(**kwargs)
