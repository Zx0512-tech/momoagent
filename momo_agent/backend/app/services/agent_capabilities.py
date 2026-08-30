from __future__ import annotations

from typing import Any

from app.services.agent_engineering import DAMPER_TYPES, ENGINEERING_SOLVERS, RESPONSE_CATALOG


def build_capability_facts() -> dict[str, Any]:
    """能力清单只来自代码注册表，不由模型编造。"""
    return {
        'model': 'STbridge（默认已登记桥梁模型）；ANALYSIS 任务支持使用已上传登记的用户 APDL 模型（modelArtifactId，仅 ANSYS）',
        'taskTypes': {
            'ANALYSIS': '给定荷载下求结构响应',
            'DAMPER_OPTIMIZATION': '为一种阻尼器寻优参数',
            'DAMPER_COMPARISON': '等最大出力口径下对比两种或三种阻尼器',
            'DAMPER_PARAMETER_SWEEP': '给定多个阻尼器参数案例并行计算响应，不做优化',
        },
        'solvers': list(ENGINEERING_SOLVERS),
        'damperTypes': {
            name: {
                'productionReady': spec['productionReady'],
                'optimizationReady': spec['optimizationReady'],
            }
            for name, spec in DAMPER_TYPES.items()
        },
        'responseCatalog': sorted(RESPONSE_CATALOG),
        'loadKinds': ['EARTHQUAKE', 'WIND', 'TRAFFIC', 'GENERIC_NODAL'],
        'responseOutputs': (
            'ANALYSIS 支持点名输出指定节点位移/加速度时程（responseNodes，X/Y/Z 方向）'
            '与指定单元内力时程（responseElementIds），每类最多 16 个'
        ),
        'constraints': [
            '所有真实求解需人工审批后执行。',
            '阻尼器布置只能从 STbridge 受控候选表中选择。',
            '优化任务当前仅放行 EARTHQUAKE 荷载。',
            '双工况对比当前仅放行已校准的 ANSYS USER300 路径。',
            '用户上传 FEM 模型仅支持 ANSYS APDL 文本，且必须显式指定响应节点。',
        ],
    }
