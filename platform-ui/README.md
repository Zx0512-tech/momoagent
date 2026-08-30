# MOMO 桥梁分析与优化平台前端 UI

基于 React 19 + Vite 8 + TypeScript 与 Vanilla CSS 构建的平台级前端控制台。

## 功能特性

1. **Dashboard 看板**: 汇总展示代码版本、回归测试门槛、求解器一致性及运行可用性。
2. **多工况荷载生成**: 覆盖随机交通流、抖振风荷载及三向地震动力输入。
3. **命令流模块组装**: 基于后端预注册的安全白名单机制，进行 APDL/Python 模块选择与 SHA256 审计核签。
4. **有限元批处理**: ANSYS / OpenSeesPy 并行批量执行与 OpenSeesPy In-process 串行状态安全锁。
5. **时程与报告可视化**: 时程曲线（位移/加速度/阻尼力）交互式预览与 MD 状态报告解析。
6. **主动学习加点**: 不确定性多维膝点探索与真实有限元求解复核队列。
7. **多目标决策**: 帕累托优化前沿图与熵权 TOPSIS 算法最优减震参数比选。

## 目录结构

* `src/api/` - Typed REST API 客户端 (包含 Mock 仿真数据库)
* `src/stores/` - 全局状态管理 (Zustand 状态与自动轮询 Poller 机制)
* `src/components/` - 高信息密度科研级 Vanilla UI 组件库
* `src/pages/` - 7 大平台核心能力操作台页面

## 快速启动

1. **安装依赖**
   ```bash
   npm install
   ```

2. **本地开发调试**
   ```bash
   npm run dev
   ```
   * 开发环境下默认启用 **Mock 仿真模式**，前端自带计算引擎模拟器，会自动推进排队、运行中的 Job 进度并生成 Artifact 文件，支持在脱离后端时完整调试全部计算流页面和交互。

3. **构建与生产部署**
   ```bash
   npm run test
   npm run lint
   npm run build
   ```
   * 构建产物保存在 `dist/`，正式运行由根目录 `start.ps1` 通过 FastAPI 同源托管。

## 求解运行模式切换

修改 `.env.development` 或 `.env.production` 配置文件：

* `VITE_API_MODE=mock` : 启动本地内存仿真模式，无需连接后端。
* `VITE_API_MODE=live` : 启动真实接口交互模式，访问 `VITE_API_BASE` 指向的 REST 服务（如 `http://127.0.0.1:8000`）。

`start-platform-ui.bat` 仅用于开发/Mock 调试，不能作为 MOMO 正式发布入口。当前版本由仓库根目录 `VERSION` 注入构建，并显示在平台顶栏。
