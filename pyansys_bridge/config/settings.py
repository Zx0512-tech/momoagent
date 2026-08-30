"""
配置管理模块
用于加载和管理YAML配置文件
"""
import yaml
import os
from typing import Dict, Any
from pathlib import Path


class Config:
    """配置管理类"""

    def __init__(self, config_path: str = None):
        """
        初始化配置

        Args:
            config_path: 配置文件路径，默认为None时自动查找
        """
        self.config_data = {}
        if config_path and os.path.exists(config_path):
            self.load_config(config_path)
        else:
            self._load_default_config()
        self._apply_env_overrides()

    def load_config(self, config_path: str) -> Dict[str, Any]:
        """
        加载YAML配置文件

        Args:
            config_path: 配置文件路径

        Returns:
            配置字典
        """
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                self.config_data = yaml.safe_load(f) or {}
            print(f"[配置] 成功加载配置文件: {config_path}")
            self._apply_env_overrides()
            return self.config_data
        except Exception as e:
            print(f"[配置错误] 加载配置文件失败: {e}")
            self._load_default_config()
            return self.config_data

    def _load_default_config(self):
        project_root = Path(__file__).resolve().parents[2]
        output_dir = project_root / 'output'
        """加载默认配置"""
        self.config_data = {
            'paths': {
                'model_file': str(project_root / 'bridge_models' / 'stbridge_ansys' / 'STbridge_apdl_model.txt'),
                'output_dir': str(output_dir),
                'seismic_data_dir': str(project_root / 'analysis_data' / 'earthquake_inputs'),
                'wim_path': str(project_root / 'RandomTrafficLoadGeneration'),
                'result_db': str(output_dir / 'result.db')
            },
            'mapdl': {
                'executable': 'MAPDL.exe',
                'ansys_root': None,
                'nproc': 1,
                'ram': 4096,
                'gui': False,
                'max_workers': 1,
                'timeout_s': None,
                'cleanup_zombies': True,
            },
            'model': {
                'name': '斜拉桥模型',
                'unit_system': 'SI'
            },
            'seismic': {
                'analysis_type': 'TRANSIENT',
                'total_time': 10.0,
                'time_step': 0.01
            }
        }
        print("[配置] 已加载默认配置")

    def _apply_env_overrides(self):
        """从环境变量覆盖可移植部署相关配置。"""
        overrides = {
            'PYANSYS_BRIDGE_MODEL_FILE': ('paths.model_file', str),
            'PYANSYS_BRIDGE_WIM_PATH': ('paths.wim_path', str),
            'PYANSYS_BRIDGE_MAPDL_EXECUTABLE': ('mapdl.executable', str),
            'PYANSYS_BRIDGE_ANSYS_ROOT': ('mapdl.ansys_root', str),
            'PYANSYS_BRIDGE_MAPDL_MAX_WORKERS': ('mapdl.max_workers', int),
            'PYANSYS_BRIDGE_MAPDL_TIMEOUT_S': ('mapdl.timeout_s', float),
            'PYANSYS_BRIDGE_MAPDL_CLEANUP_ZOMBIES': ('mapdl.cleanup_zombies', _parse_bool),
        }
        for env_name, (key, caster) in overrides.items():
            raw_value = os.getenv(env_name)
            if raw_value is None or raw_value == '':
                continue
            self.set(key, caster(raw_value))

    def get(self, key: str, default=None) -> Any:
        """
        获取配置项，支持多级键访问 (如 'mapdl.nproc')

        Args:
            key: 配置键，支持 '.' 分隔的多级键
            default: 默认值

        Returns:
            配置值
        """
        keys = key.split('.')
        value = self.config_data
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    def set(self, key: str, value: Any):
        """
        设置配置项

        Args:
            key: 配置键，支持 '.' 分隔的多级键
            value: 配置值
        """
        keys = key.split('.')
        config = self.config_data
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        config[keys[-1]] = value

    def get_path(self, key: str) -> Path:
        """获取路径配置并转换为Path对象"""
        path_str = self.get(f'paths.{key}')
        return Path(path_str) if path_str else None

    def ensure_directories(self):
        """确保所有输出目录存在"""
        output_dir = self.get_path('output_dir')
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)

        seismic_dir = self.get_path('seismic_data_dir')
        if seismic_dir:
            seismic_dir.mkdir(parents=True, exist_ok=True)

    def print_summary(self):
        """打印配置摘要"""
        print("\n" + "=" * 50)
        print("分析配置摘要")
        print("=" * 50)
        print(f"模型文件: {self.get('paths.model_file')}")
        print(f"输出目录: {self.get('paths.output_dir')}")
        print(f"分析类型: {self.get('seismic.analysis_type')}")
        print(f"处理器数: {self.get('mapdl.nproc')}")
        if self.get('damper.enabled'):
            print(f"阻尼器: 启用 (C={self.get('damper.properties.damping_coeff')})")
        print("=" * 50 + "\n")


# 全局配置实例
_global_config = None


def get_config(config_path: str = None) -> Config:
    """获取全局配置实例"""
    global _global_config
    if _global_config is None or config_path:
        _global_config = Config(config_path)
    return _global_config


def reset_config():
    """重置全局配置"""
    global _global_config
    _global_config = None


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    if normalized in {'0', 'false', 'no', 'off'}:
        return False
    raise ValueError(f"无法解析布尔配置值: {value}")
