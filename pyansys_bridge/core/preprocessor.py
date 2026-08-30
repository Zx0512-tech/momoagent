"""
模型加载与预处理模块
负责加载现有APDL模型、添加阻尼器等预处理操作
"""
import os
import numpy as np
from typing import List, Tuple, Optional, Dict
from pathlib import Path

try:
    from ansys.mapdl.core import Mapdl
except ImportError:
    pass


class ModelLoader:
    """模型加载器"""

    def __init__(self, mapdl: 'Mapdl'):
        self.mapdl = mapdl
        self.model_info = {}

    def load_input_file(self, file_path: str) -> bool:
        """
        加载APDL输入文件

        Args:
            file_path: 模型文件路径(.txt, .inp, .mac)

        Returns:
            是否加载成功
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"模型文件不存在: {file_path}")

        print(f"[模型] 正在加载模型文件: {file_path}")

        try:
            # 清除之前的数据
            self.mapdl.clear()
            self.mapdl.prep7()

            # 使用/input命令加载文件
            self.mapdl.input(file_path)

            # 获取模型信息
            self._extract_model_info()

            print(f"[模型] 加载成功!")
            print(f"[模型] 节点数: {self.model_info.get('n_nodes', 'Unknown')}")
            print(f"[模型] 单元数: {self.model_info.get('n_elements', 'Unknown')}")

            return True

        except Exception as e:
            print(f"[模型错误] 加载模型失败: {e}")
            raise

    def _extract_model_info(self):
        """提取模型基本信息"""
        try:
            self.model_info['n_nodes'] = self.mapdl.mesh.n_node
            self.model_info['n_elements'] = self.mapdl.mesh.n_elem
            self.model_info['n_keypoints'] = len(self.mapdl.geometry.get_keypoints())
            self.model_info['n_lines'] = len(self.mapdl.geometry.get_lines())
            self.model_info['n_areas'] = len(self.mapdl.geometry.get_areas())
        except Exception as e:
            print(f"[模型警告] 获取模型信息失败: {e}")

    def get_model_info(self) -> Dict:
        """获取模型信息字典"""
        return self.model_info

    def list_nodes(self, start: int = None, end: int = None) -> List[int]:
        """
        列出节点编号

        Args:
            start: 起始节点号
            end: 结束节点号

        Returns:
            节点编号列表
        """
        if start and end:
            self.mapdl.nsel('S', 'NODE', '', start, end)

        node_nums = self.mapdl.mesh.nnum
        self.mapdl.allsel()
        return node_nums.tolist() if hasattr(node_nums, 'tolist') else list(node_nums)

    def list_elements(self) -> List[int]:
        """列出单元编号"""
        elem_nums = self.mapdl.mesh.enum
        return elem_nums.tolist() if hasattr(elem_nums, 'tolist') else list(elem_nums)

    def get_node_coords(self, node_num: int) -> Tuple[float, float, float]:
        """
        获取节点坐标

        Args:
            node_num: 节点编号

        Returns:
            (x, y, z) 坐标元组
        """
        x = float(self.mapdl.get('NODE', node_num, 'LOC', 'X'))
        y = float(self.mapdl.get('NODE', node_num, 'LOC', 'Y'))
        z = float(self.mapdl.get('NODE', node_num, 'LOC', 'Z'))
        return (x, y, z)

    def get_node_by_location(self, x: float = None, y: float = None, z: float = None,
                            tolerance: float = 1e-6) -> List[int]:
        """
        根据坐标位置查找节点

        Args:
            x: X坐标
            y: Y坐标
            z: Z坐标
            tolerance: 容差

        Returns:
            节点编号列表
        """
        self.mapdl.nsel('S', 'LOC', 'X', x - tolerance, x + tolerance) if x is not None else None
        self.mapdl.nsel('R', 'LOC', 'Y', y - tolerance, y + tolerance) if y is not None else None
        self.mapdl.nsel('R', 'LOC', 'Z', z - tolerance, z + tolerance) if z is not None else None

        nodes = self.mapdl.mesh.nnum
        self.mapdl.allsel()
        return nodes.tolist() if hasattr(nodes, 'tolist') else list(nodes)


class DamperHandler:
    """阻尼器处理器"""

    # 单元类型映射
    ELEMENT_TYPES = {
        'COMBIN14': 14,  # 弹簧-阻尼器单元
        'COMBIN40': 40,  # 组合单元
        'MATRIX27': 27,  # 刚度矩阵单元
        'COMBI165': 165, # 显式弹簧阻尼
    }

    def __init__(self, mapdl: 'Mapdl'):
        self.mapdl = mapdl
        self.damper_elements = []
        self.element_type_id = None
        self.real_constant_id = None

    def setup_element_type(self, element_type: str = 'COMBIN14'):
        """
        设置阻尼器单元类型

        Args:
            element_type: 单元类型名称或编号
        """
        if isinstance(element_type, str):
            if element_type.upper() not in self.ELEMENT_TYPES:
                raise ValueError(f"不支持的单元类型: {element_type}")
            elem_num = self.ELEMENT_TYPES[element_type.upper()]
        else:
            elem_num = element_type

        # 进入前处理
        self.mapdl.prep7()

        # 定义新的单元类型
        existing_types = self._get_existing_etypes()
        self.element_type_id = len(existing_types) + 1

        # 定义COMBIN14单元 (关键选项KEYOPT设置)
        self.mapdl.et(self.element_type_id, elem_num)

        # KEYOPT设置: 0-纵向弹簧阻尼(UX), 1-横向((UY)等
        # KEYOPT(2)=0: 使用实常数定义
        self.mapdl.keyopt(self.element_type_id, 1, 0)  # 自由度选择 (0=轴向)
        self.mapdl.keyopt(self.element_type_id, 2, 0)  # 实常数使用方式

        print(f"[阻尼器] 单元类型 {element_type} 已定义, 类型编号: {self.element_type_id}")

    def setup_properties(self, stiffness: float, damping_coeff: float,
                        real_constant_id: int = None):
        """
        设置阻尼器特性参数

        Args:
            stiffness: 刚度系数 K (N/m)
            damping_coeff: 阻尼系数 C (N*s/m)
            real_constant_id: 实常数编号，不指定则自动分配
        """
        self.mapdl.prep7()

        if real_constant_id is None:
            self.real_constant_id = self.element_type_id
        else:
            self.real_constant_id = real_constant_id

        # 定义实常数
        # R, NSET, R1, R2, R3, R4, R5, R6
        # For COMBIN14: R1=K (刚度), R2=C (阻尼)
        self.mapdl.r(self.real_constant_id, stiffness, damping_coeff)

        print(f"[阻尼器] 实常数已定义: K={stiffness:e}, C={damping_coeff:e}")

    def add_damper(self, node_i: int, node_j: int, orientation: str = 'X') -> int:
        """
        在两节点间添加阻尼器

        Args:
            node_i: 起始节点
            node_j: 终止节点
            orientation: 方向 'X', 'Y', 'Z', 或 'ALL'

        Returns:
            阻尼器单元编号
        """
        if self.element_type_id is None:
            raise RuntimeError("请先调用setup_element_type()设置单元类型")

        if self.real_constant_id is None:
            raise RuntimeError("请先调用setup_properties()设置属性")

        self.mapdl.prep7()

        # 设置单元类型和实常数
        self.mapdl.type(self.element_type_id)
        self.mapdl.real(self.real_constant_id)
        self.mapdl.mat(1)  # 使用材料1

        # 根据方向设置KEYOPT(3)
        orientation_map = {'X': 0, 'Y': 1, 'Z': 2, 'ROTX': 3, 'ROTY': 4, 'ROTZ': 5}
        if orientation.upper() in orientation_map:
            self.mapdl.keyopt(self.element_type_id, 3, orientation_map[orientation.upper()])

        # 创建单元
        elem_num = self.mapdl.e(node_i, node_j)

        self.damper_elements.append({
            'elem': elem_num,
            'nodes': (node_i, node_j),
            'orientation': orientation
        })

        print(f"[阻尼器] 已添加: 单元{elem_num}, 节点{node_i}-{node_j}, 方向{orientation}")

        return elem_num

    def add_dampers_batch(self, node_pairs: List[Tuple[int, int]], orientation: str = 'X'):
        """
        批量添加阻尼器

        Args:
            node_pairs: 节点对列表 [(n1, n2), (n3, n4), ...]
            orientation: 方向

        Returns:
            创建的单元编号列表
        """
        element_nums = []
        for node_i, node_j in node_pairs:
            elem_num = self.add_damper(node_i, node_j, orientation)
            element_nums.append(elem_num)

        print(f"[阻尼器] 批量添加了 {len(element_nums)} 个阻尼器")
        return element_nums

    def _get_existing_etypes(self) -> List[int]:
        """获取已定义的单元类型编号"""
        try:
            etlist = self.mapdl.etlist()
            return list(range(1, len(etlist) + 1)) if etlist else []
        except:
            return []

    def get_damper_info(self) -> List[Dict]:
        """获取所有阻尼器信息"""
        return self.damper_elements

    def plot_dampers(self):
        """绘制阻尼器位置"""
        if self.damper_elements:
            self.mapdl.eplot(vtk=True)


class DampingHandler:
    """阻尼处理器 (瑞利阻尼、模态阻尼)"""

    def __init__(self, mapdl: 'Mapdl'):
        self.mapdl = mapdl

    def set_rayleigh_damping(self, alpha: float = 0.0, beta: float = 0.0):
        """
        设置瑞利阻尼 (C = alpha*M + beta*K)

        Args:
            alpha: 质量阻尼系数
            beta: 刚度阻尼系数
        """
        # 进入求解器前需要先完成前处理
        self.mapdl.finish()
        self.mapdl.slashsolu()

        # 设置瑞利阻尼
        # ALPHAD - 质量阻尼系数
        # BETAD - 刚度阻尼系数
        if alpha != 0.0:
            self.mapdl.alphad(alpha)
        if beta != 0.0:
            self.mapdl.betad(beta)

        print(f"[阻尼] 瑞利阻尼已设置: α={alpha}, β={beta}")

    def set_modal_damping(self, mode_ratios: List[float]):
        """
        设置模态阻尼比

        Args:
            mode_ratios: 各阶模态的阻尼比列表
        """
        self.mapdl.finish()
        self.mapdl.slashsolu()

        # MDAMP命令设置模态阻尼
        # 阻尼比不能为0
        for i, ratio in enumerate(mode_ratios, start=1):
            damp_val = max(ratio, 0.001)  # 确保最小阻尼
            self.mapdl.mdamp(i, damp_val)

        # 设置剩余模态使用最后一个阻尼比
        if len(mode_ratios) > 0:
            self.mapdl.mdamp(len(mode_ratios) + 1, -1)

        print(f"[阻尼] 模态阻尼已设置: {len(mode_ratios)} 阶模态")

    def set_constant_damping(self, ratio: float):
        """
        设置恒定阻尼比

        Args:
            ratio: 阻尼比 (如 0.02 表示 2%)
        """
        self.mapdl.finish()
        self.mapdl.slashsolu()

        # DMPRAT命令设置恒定阻尼比
        damp_val = max(ratio, 0.001)
        self.mapdl.dmprat(damp_val)

        print(f"[阻尼] 恒定阻尼比已设置: {ratio*100}%")

    def get_recommended_rayleigh(self, freq1: float, freq2: float,
                                  zeta1: float = 0.02, zeta2: float = 0.02) -> Tuple[float, float]:
        """
        根据目标频率和阻尼比计算瑞利阻尼系数

        公式: [zeta1]   [1/(2*w1)  w1/2] [alpha]
              [zeta2] = [1/(2*w2)  w2/2] [beta ]

        Args:
            freq1: 第一目标频率 (Hz)
            freq2: 第二目标频率 (Hz)
            zeta1: 第一频率处的阻尼比
            zeta2: 第二频率处的阻尼比

        Returns:
            (alpha, beta) 瑞利阻尼系数
        """
        w1 = 2 * np.pi * freq1
        w2 = 2 * np.pi * freq2

        # 求解方程组
        A = np.array([[1/(2*w1), w1/2], [1/(2*w2), w2/2]])
        b = np.array([zeta1, zeta2])

        try:
            alpha, beta = np.linalg.solve(A, b)
            print(f"[阻尼计算] 推荐系数: α={alpha:.6f}, β={beta:.6e}")
            print(f"[阻尼计算] 基于频率: f1={freq1}Hz, f2={freq2}Hz")
            return (alpha, beta)
        except np.linalg.LinAlgError:
            print("[阻尼计算警告] 矩阵奇异，使用默认阻尼")
            return (0.0, 0.0)


class ModelPreprocessor:
    """模型预处理器 - 综合处理功能"""

    def __init__(self, mapdl: 'Mapdl'):
        self.mapdl = mapdl
        self.loader = ModelLoader(mapdl)
        self.damper_handler = DamperHandler(mapdl)
        self.damping_handler = DampingHandler(mapdl)

    def load_and_prepare(self, model_file: str, config: dict = None) -> bool:
        """
        加载模型并进行预处理

        Args:
            model_file: 模型文件路径
            config: 预处理配置字典

        Returns:
            是否成功
        """
        # 1. 加载模型
        self.loader.load_input_file(model_file)

        # 2. 添加阻尼器 (如果配置中有)
        if config and config.get('damper', {}).get('enabled', False):
            self._setup_dampers(config['damper'])

        # 3. 设置阻尼
        if config and config.get('damping'):
            self._setup_damping(config['damping'])

        self.mapdl.finish()
        print("[预处理] 模型准备完成")
        return True

    def _setup_dampers(self, damper_config: dict):
        """配置阻尼器"""
        element_type = damper_config.get('element_type', 'COMBIN14')
        props = damper_config.get('properties', {})
        locations = damper_config.get('locations', [])

        if not locations:
            print("[阻尼器] 未配置阻尼器位置，跳过")
            return

        # 设置单元类型和属性
        self.damper_handler.setup_element_type(element_type)
        self.damper_handler.setup_properties(
            stiffness=props.get('stiffness', 1e6),
            damping_coeff=props.get('damping_coeff', 1e5)
        )

        # 批量添加阻尼器
        self.damper_handler.add_dampers_batch(locations)

        print(f"[阻尼器] 共添加 {len(locations)} 个阻尼器")

    def _setup_damping(self, damping_config: dict):
        """配置阻尼"""
        damping_type = damping_config.get('type', 'RAYLEIGH')

        if damping_type == 'RAYLEIGH':
            rayleigh = damping_config.get('rayleigh', {})
            self.damping_handler.set_rayleigh_damping(
                alpha=rayleigh.get('alpha', 0.0),
                beta=rayleigh.get('beta', 0.0)
            )
        elif damping_type == 'MODAL':
            modal = damping_config.get('modal', {})
            ratios = modal.get('ratios', [0.02])
            self.damping_handler.set_modal_damping(ratios)
        else:
            print(f"[阻尼] 未知的阻尼类型: {damping_type}")
