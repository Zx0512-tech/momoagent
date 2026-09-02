"""阻尼器耗能必须由真实的力-速度时程积分得出。"""
from __future__ import annotations

from pathlib import Path

from pyansys_bridge.core.result_summary import dissipated_energy_from_relative_response


def test_dissipated_energy_uses_each_damper_force_velocity_history(tmp_path: Path) -> None:
    source = tmp_path / 'tower_girder_relative_response.csv'
    source.write_text(
        '\n'.join([
            'time,Pair1_rel_vel,Pair1_damper_force,Pair2_rel_vel,Pair2_damper_force',
            '0.0,0.0,0.0,0.0,0.0',
            '0.5,2.0,3.0,-1.0,-4.0',
            '1.0,1.0,5.0,-2.0,-6.0',
        ]),
        encoding='utf-8',
    )

    result = dissipated_energy_from_relative_response(source)

    assert result['dissipated_energy'] == 13.5
    assert result['perDamper'] == {'Pair1': 5.5, 'Pair2': 8.0}
    assert result['source'] == 'tower_girder_relative_response.csv'
