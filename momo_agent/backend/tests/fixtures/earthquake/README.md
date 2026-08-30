# 地震记录测试夹具

三条 PEER NGA West2 强震记录，逐字节原样保存，用于 `test_earthquake_load_format_import.py`
验证 `.AT2` 解析（NPTS / DT / 单位声明都写在文件头里）。

| 文件 | RSN | 事件 | NPTS | DT (s) | PGA (g) |
| --- | --- | --- | --- | --- | --- |
| `RSN767_LOMAP_G03090.AT2` | 767 | Loma Prieta 1989 | 7998 | 0.005 | 0.3682262 |
| `RSN164_IMPVALL.H_H-CPE147.AT2` | 164 | Imperial Valley 1979 | 6382 | 0.010 | 0.1682932 |
| `RSN1633_MANJIL_ABBAR--L.AT2` | 1633 | Manjil 1990 | 2676 | 0.020 | 0.5145641 |

PGA 一列是本仓库解析器实测的峰值绝对值，不是 PEER 元数据里的标称值；测试直接断言这些数。

## 来源与合规

这三个文件取自公开 GitHub 镜像仓库
`GeorgePapazafeiropoulos/PEER-Ground-Motion-Data-Base-Reader`，**不是**从 PEER 官方站点下载的。
原因：NGA-West2 下载需要机构邮箱注册（明确不接受 Gmail/Yahoo 一类公共邮箱），有配额限制，
也不提供匿名直链，无法在此环境中代为注册。

因此这批文件按**开发期测试夹具**对待。正式验收时应使用机构账号从 PEER 官方重新下载同样的
RSN 记录 —— 文件内容应当逐字节一致，届时替换本目录即可，测试里的期望值不需要改。

不要在这里添加体积更大的记录：夹具是用来覆盖表头写法差异的（RSN1633 的 DT 行没有尾随逗号），
不是用来做性能测试的。

## 行尾必须锁死

这三个文件的行尾本来就不一致：RSN767 是 LF，RSN1633 与 RSN164 是 CRLF。本仓库 `core.autocrlf=true`，
若不做处理，git 会在暂存时把 CR 剥掉，磁盘字节与 blob 就不再一致 —— "逐字节原样"随即失效。

因此根目录 `.gitattributes` 给本目录的 `*.AT1/*.AT2` 声明了 `-text`，禁用行尾转换。
**不要删掉那条规则**，也不要用编辑器"统一行尾"来整理本目录。改动后可以这样自检
（三行都应输出 `same`）：

```powershell
git hash-object --no-filters <file>   # 裸字节
git rev-parse :<repo-relative-path>   # 暂存 blob
```

解析器本身对两种行尾都能正常工作（`splitlines()` 通吃），锁行尾是为了让夹具的字节级溯源成立，
而不是解析器有此要求。
