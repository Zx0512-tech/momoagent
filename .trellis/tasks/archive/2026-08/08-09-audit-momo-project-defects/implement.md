# 审计执行清单

1. [x] 读取任务 PRD、设计和适用的后端/前端/跨层规范，建立审计矩阵。
2. [x] 运行后端全量 pytest，并对失败或 warning 逐项分类。
3. [x] 运行前端 Vitest、oxlint、TypeScript/Vite build 和 bundle budget。
4. [x] 运行 Python compileall、现有 Ruff/mypy 检查及 `verify_submission.py`。
5. [x] 对工具目录、工作流 `allowedTools`、Pydantic Schema、阶段参数和模型可调用子集做机器可检查的一致性审计。
6. [x] 追踪审批、幂等、参数保真、run/job/tool-call/artifact 的创建、失败、取消与恢复路径。
7. [x] 比较 FastAPI Schema 与前端 TypeScript 类型、状态枚举、错误载荷和未知状态回退。
8. [x] 检查路径、文件上传/读取、子进程、环境变量、日志、CORS 和资源上限等信任边界。
9. [x] 抽样追踪单次分析、阻尼器比较和优化的输入到最终证据门禁与 UI 呈现。
10. [x] 对每个候选问题执行最小复现或静态反证检查，剔除不可达路径和设计内行为。
11. [x] 编写 `audit-report.md`，记录缺陷、改进项、排除项、基线和未验证风险。
12. [x] 检查工作区只包含任务文档，提交审计报告并按 Trellis 流程归档。

## 主要验证命令

```powershell
# 后端
python -m pytest -q
python -m compileall app tests

# 前端
npm.cmd test
npm.cmd run lint
npm.cmd run build
npm.cmd run check:bundle

# 提交包
python verify_submission.py
```

Ruff 与 mypy 以仓库现有可运行配置为准；若只能对明确文件集执行，报告中必须记录实际范围。

## 停止条件

- 发现测试会修改提交证据或触发不可控长时工程计算时停止该命令，改用隔离的最小复现。
- 需要联网、外部许可证、真实 LLM 密钥或 ANSYS 才能确认的问题标记为“未验证风险”，不得升级为已证实缺陷。
- 发现 P0 时先完整保存本地证据并立即报告，不继续执行可能扩大影响的路径。
