# API 与交付门禁

## Goal

消除公共请求的静默参数修正，并让预检与提交清单在输入不可信时失败关闭。

## Requirements

- 命令请求 DTO 默认拒绝未知字段，同时接受声明的 camelCase 和 snake_case。
- 通用 `/jobs` 使用 JobType 到专用请求模型的注册表，不得绕过专用校验。
- 参数规范化必须在契约中公开，并在返回/审计中报告。
- 公共 preflight 调用真实 `preflight_config`，失败返回兼容的结构化 FAIL，不暴露堆栈。
- 提交 manifest 必须存在，条目数量、唯一路径、边界、存在性和 SHA-256 全部校验。

## Acceptance Criteria

- [x] 合法 camel/snake 均成功，未知字段和嵌套拼写错误返回 422。
- [x] 通用 `/jobs` 无法绕过专用请求约束。
- [x] 有效、缺失、无效配置和求解器缺失的 preflight 结果真实可靠。
- [x] manifest 缺失、计数错误、重复/越界、缺文件和哈希错误均失败；有效清单通过。

## Out of Scope

- 因缓存或构建输出存在而强制所有磁盘文件列入 manifest。
