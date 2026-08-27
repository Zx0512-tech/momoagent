# 批量时程对比与证据复核设计

## 边界与数据流

```text
已完成 Job 的原始 summary.json + command stream + output manifest
  -> 统一真实输出验证器
  -> caseResults.isVerifiedSolverOutput / 证据报告

已登记的每案例 timeseries.csv + caseResults 参数
  -> 批量时程比较 API
  -> 响应量选择 + 参数工况选择
  -> 每个响应量一张多工况叠加图
```

## 证据复核

- 在 `PlatformStore` 中集中真实案例验证规则，执行模式从
  `metadata.solver_design.execution_mode` 读取；保留顶层字段仅作旧结果兼容。
- 验证仍要求：案例完成、执行模式为 `run`、求解器已验证标记为 true、命令流非
  dry-run 且求解输出不含 dry-run 标记。禁止因放宽字段位置而接受模拟结果。
- 参数扫描和既有阻尼器对比使用同一验证器，避免两处字段路径再次漂移。
- 为当前 `job_54edb4a16bc3` 执行一次受控、幂等的证据重建：只重算 case 的验证布尔值、
  批量摘要和报告；从现有 manifest 中核验源 summary 和已登记 Artifact，不触发求解器。

## 时程比较契约

保留旧接口 `GET /agent/runs/{runId}/timeseries` 的单来源行为，并新增比较接口：

```text
GET /agent/runs/{runId}/timeseries/compare
  ?columns=displacement
  &caseIds=case_viscous_c1000_alpha03,case_viscous_c2000_alpha05
  &maxPoints=1000
```

省略 `caseIds` 表示选择该 run 的全部已验证参数工况。响应返回每个案例的：

```json
{
  "runId": "...",
  "cases": [{
    "caseId": "case_viscous_c1000_alpha03",
    "parameters": {"c": 1000, "alpha": 0.3, "vfloor": 0.001},
    "label": "c=1000，α=0.3，vfloor=0.001",
    "availableColumns": ["time", "displacement"],
    "columns": ["time", "displacement"],
    "series": {"time": [], "displacement": []},
    "peaks": {}
  }]
}
```

服务端仅从该 run 关联 Job 的已登记 `timeseries.csv` 读取；每个 artifact 必须通过现有
`ResultInquiryService` 和结果目录验证。工况与文件通过受控 `caseId` 路径段及 Job
`caseResults` 对齐，不以重复的文件名作为唯一键。

## 前端交互

- 默认响应量为梁端位移，默认勾选全部已验证工况。
- 响应量和参数工况是独立多选器；参数用于筛选工况并显示在图例，不作为随时间变化的曲线。
- 对每个选中响应量独立渲染一张图，其中每条线是一个工况；因此同一图只有一个物理量和单位。
- 没有可验证工况、工况间不存在共同响应列或服务端拒绝未登记数据时，显示明确原因，不回退到任意文件系统路径。

## 兼容与回滚

- 旧时程接口、旧前端调用和非参数扫描 run 保持不变。
- 比较接口只为 `DAMPER_PARAMETER_SWEEP` 暴露多案例数据；其他 run 继续使用原接口。
- 回滚时移除新前端入口和比较接口；原始 CSV、Job 和求解结果不被删除。
