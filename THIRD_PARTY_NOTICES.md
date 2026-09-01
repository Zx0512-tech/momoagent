# 第三方组件说明

本作品使用 FastAPI、React、NumPy、SciPy、scikit-learn、pymoo、OpenSees/OpenSeesPy 等第三方组件。各组件版权和许可归其原作者所有，提交或发布前应按比赛规则复核对应依赖版本的许可证。

`bridge_models/stbridge_opensees/openseespy/` 包含面向 Windows、Python 3.13 x64 的定制 OpenSeesPy 二进制，用于提供 `User300Viscous`、`User300Friction` 和 `User300EddyCurrent` 材料。相关 OpenSees 源码补丁保存在 `third_party_sources/opensees_user300/`，便于技术审查；完整上游 OpenSees 源码未重复打包。

本比赛提交版不包含 ANSYS/MAPDL 商业软件、许可证文件或可执行程序。

