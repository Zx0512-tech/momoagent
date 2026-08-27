# 技术设计

- 请求和响应基类分离；请求通过 alias generator、`populate_by_name` 和 `extra=forbid` 保证参数保真。
- 通用 job 创建先按 JobType 选择专用 Pydantic 模型，再序列化规范字段进入服务层。
- preflight 适配真实配置检查为现有响应形状；内部异常转换为具名失败原因。
- 将 manifest 验证提取为无副作用纯函数，规范化路径后再读取和校验哈希。
