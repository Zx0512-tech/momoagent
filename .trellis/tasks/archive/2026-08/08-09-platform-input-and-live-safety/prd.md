# 平台输入与生产安全

## Goal

阻止 Live 模式伪成功，并让用户选择的载荷文件以真实制品内容进入求解链。

## Requirements

- Live 模式拒绝未实现的独立 `SOLVER_BATCH`、`SURROGATE_TRAINING`、`ACTIVE_LEARNING`，返回 501 `CAPABILITY_NOT_IMPLEMENTED`，且拒绝前不创建记录。
- 真实智能体分析、比较和优化链保持可用。
- Mock 模式可演示，前端必须显示模拟标识；Live 前端禁用未实现入口。
- 复用现有流式上传和 `LoadImportService`，提供通用载荷制品上传。
- 本地文件模式必须使用真实 `inputArtifactId`，显式映射列、单位、方向/分量和目标。
- 无效内容或映射返回 422，不生成占位载荷。

## Acceptance Criteria

- [x] Live 占位请求返回 501 且 Job/Artifact 数量不变。
- [x] 上传返回 artifactId、sha256、文件名和列检查信息。
- [x] 地震、风和交通文件均从登记制品读取真实字节。
- [x] 前端不再把文件名当 Artifact ID，Live/Mock 状态清晰可见。
