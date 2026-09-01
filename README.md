# MOMO 桥梁地震分析与阻尼器优化智能体（比赛提交版）

本目录是从完整 MOMO 工程中提取的独立 OpenSeesPy 比赛作品。它包含智能体前后端、受控工具链、桥梁模型、登记地震记录、USER300 黏滞阻尼器运行时、多目标优化流程，以及一组可离线核验的真实分析证据。

## 核心能力

- 自然语言识别求解器、荷载、阻尼器类型、布置与优化目标。
- 在执行前冻结工程合同并要求人工审批，防止模型直接拼接任意求解命令。
- 调用 OpenSeesPy 完成基线、真实 DOE、代理模型比较、Pareto 前沿、熵权 TOPSIS 和最终 FEM 复核。
- 记录命令流、求解器版本、输入来源、文件 SHA256、时程及最终决策证据。
- 本系统需要配置兼容 OpenAI 接口的大模型。意图识别、参数抽取、多轮澄清与结果解释均由模型完成，未配置时智能体对话不可用（平台的制品浏览与任务队列不受影响）。

## 运行环境

- Windows 10/11 x64
- Python 3.13 x64（内置 USER300 二进制与此版本绑定）
- PowerShell 5.1 或更高版本
- Node.js 仅在需要重新构建前端时使用；目录已包含生产构建
- 建议至少 16 GB 内存、8 个 CPU 逻辑核心和 5 GB 可用磁盘

## 快速开始

```powershell
cd D:\path\to\repository
powershell -ExecutionPolicy Bypass -File .\install.ps1 -SkipFrontendBuild
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

浏览器访问 `http://127.0.0.1:8000`。停止服务使用 `Ctrl+C`。

智能体对话需要在 `.env` 中配置 `MOMO_LLM_BASE_URL`、`MOMO_LLM_MODEL` 和 `MOMO_LLM_API_KEY` 后重启服务；未配置时服务仍会启动，但只提供平台页面、制品浏览和任务队列功能。

默认运行模式是 `MOMO_AGENT_RUNTIME=WORKFLOW_HARNESS`：LLM 负责理解和选择已登记工作流，Python 冻结流程快照并强制执行步骤、审批、幂等与证据门禁。设置 `MOMO_AGENT_PERSISTENT_LOOP=true` 后，Job 结束后的内部阶段由 LLM 按冻结步骤逐轮调用工具，每轮状态都会写回 run，进程重启后可继续。迁移排障时可以临时设置 `MOMO_AGENT_RUNTIME=LEGACY`，重启服务后回到旧路由。

Harness 默认通过 `MOMO_LLM_HARNESS_THINKING=true` 启用模型内部思考，并为推理和原生工具调用保留 `MOMO_LLM_HARNESS_MAX_TOKENS=4096`。内部思维链不会发送到前端；用户只看到模型结论、工具状态和可审计证据。模型生成的工具参数若不是合法 JSON，Harness 会把格式错误反馈给模型并最多自动修复两次。

独立平台工程入口由 `MOMO_PLATFORM_MODE` 控制：生产环境请显式设置为 `LIVE`，未通过真实执行验收的能力会在创建 Job/Artifact 前返回 501；本地演示可设置为 `MOCK`，返回结果必须按模拟数据处理。

离线自检：

```powershell
.\.venv\Scripts\python.exe .\verify_submission.py
```

## 自然语言演示

配置好大模型后，将 [`demo_prompt.txt`](demo_prompt.txt) 中的指令粘贴到智能体页面。系统会展示：

1. 意图和求解器识别；
2. 节点布置、目标、预算与来源冻结；
3. 环境预检和人工审批；
4. OpenSeesPy Job、真实输出与制品登记；
5. 推荐参数及最终 FEM 复核。

将 `.env.example` 复制为 `.env`，填写兼容接口地址、模型和密钥。`.env` 已被忽略，禁止提交真实密钥。

## 已归档真实结果

本次演示共执行 17 个真实 OpenSees 算例：1 个无阻尼基线、15 个 DOE、1 个推荐点复算。另有 728 个代理候选点，不计入真实求解数。

`evidence/` 内的历史命令流保留原始 `D:\momo` 绝对路径作为不可改写的来源证据，不作为搬迁后的执行入口；从本提交目录发起新任务时，系统会按当前位置重新生成命令流。

最终推荐为：单个阻尼器 `c=7600`、`alpha=0.8`，每塔两个阻尼器、每塔总 `c=15200`。

| 指标 | 无阻尼基线 | 推荐点真实复算 | 变化 |
|---|---:|---:|---:|
| 梁端位移 | 0.391930 m | 0.135040 m | -65.5% |
| 塔底剪力 | 52.299850 MN | 48.103118 MN | -8.0% |
| 塔底弯矩 | 2.240498 GN·m | 1.622609 GN·m | -27.6% |
| 阻尼器最大力 | — | 1.230846 MN | — |
| 阻尼器最大行程 | — | 0.129595 m | — |
| 四个阻尼器总耗能 | — | 2.950745 MJ | — |

推荐点代理值与最终 FEM 值的相对误差为：位移 3.09%、剪力 0.027%、弯矩 1.48%，均通过 5% 最终复核门槛。

## 目录结构

```text
platform-ui/                  React 前端源码与生产构建
momo_agent/backend/           FastAPI、智能体、审批和任务队列
pyansys_bridge/               OpenSees 调用、DOE、代理与优化核心
bridge_models/                桥梁模型和 USER300 OpenSeesPy 运行时
analysis_data/                登记地震加速度记录
docs/examples/templates/      受控工作流配置和校准证据
evidence/                     本次真实优化的可复核结果
```

## 结果边界

本提交版不捆绑商业 ANSYS 软件，只演示 OpenSeesPy 独立路径。当前归档结果通过同求解器最终真实复核，但没有执行独立 ANSYS 互证，因此工程状态属于诊断级结果，不应冒充正式设计审查结论。代理模型对位移和弯矩的全局交叉验证误差仍有改进空间，比赛答辩时应如实说明。

前端依赖在本机完成测试、lint 和生产构建。安装过程曾返回 3 个高危项的在线摘要，但在线审计明细因外发依赖元数据的安全限制未获取；离线缓存审计返回 0。正式提交前应由参赛者明确授权并运行 `npm audit`，根据明细决定升级或记录仅开发依赖的风险接受理由。

第三方与定制 OpenSees 材料说明见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
