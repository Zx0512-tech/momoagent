from __future__ import annotations

try:
    import sitecustomize  # noqa: F401
except Exception:
    # 让真正的扩展模块导入错误暴露出来，避免在包初始化阶段吞掉关键信息。
    pass
