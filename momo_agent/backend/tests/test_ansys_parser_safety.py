from __future__ import annotations

import pytest

from bridge_models.stbridge_opensees.parse_ansys_to_opensees import safe_eval


def test_ansys_expression_parser_accepts_numeric_arithmetic() -> None:
    assert safe_eval('jjl(1) * 2.9 + 1.0E-2', {'jjl': [2.0]}) == pytest.approx(5.81)


@pytest.mark.parametrize('expression', [
    "__import__('os').getcwd()",
    '().__class__.__base__.__subclasses__()',
    'missing_name + 1',
])
def test_ansys_expression_parser_rejects_non_arithmetic_without_silent_zero(expression: str) -> None:
    with pytest.raises(ValueError, match='unsupported ANSYS numeric expression'):
        safe_eval(expression, {})
