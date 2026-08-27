# Live E2E 设计

使用 Playwright（仓库已有 `@vitest/browser-playwright` 传递依赖，本任务改为显式业务测试）。测试启动本地 backend + `platform-ui` Live 模式，种子一条可查询的地震 OpenSeesPy 运行，或走最短审批执行链。选择对 CI 最稳的确定性 solver，避免真实 ANSYS。
