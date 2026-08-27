# 能力矩阵对齐

以 `real_execution/registry.py` 为唯一广告源。先做矩阵表：每个 `(jobType, mode, solver, scenario)` 标注 LIVE / 实现存在未登记 / 无物理模型。然后只改目录或测试，不补物理。OpenSees 对比走“有测试就登记，没对等就下架”二选一，禁止第三种含糊状态。
