"""维护脚本：全量重建 verification/SUBMISSION_MANIFEST.json。

不再"沿用旧 SHA"——manifest 应当反映实际交付的字节，因此对提交范围内
每个存在的文件都重新读盘计算 SHA256。

提交范围 = 原 manifest 的路径集合（定义了交付边界，天然排除 node_modules/.venv）
         + RESCAN_DIRS 按磁盘现状重扫（捕获新增文件）
         - 已不存在的文件（本次删除的旧实现）
         - .pytest_cache（pytest 临时缓存，非交付物且权限锁定）

RESCAN_DIRS 必须覆盖所有会新增交付文件的目录，否则新文件永远进不了清单：
verification/verify_submission.py 只校验 fileCount 与 files 条目数自洽，不做文件系统比对，
因此这类缺失不会被任何质量门发现。docs/examples/templates 是求解器模板的
交付目录（模板 SHA 被基线用例引用），必须在重扫范围内。
"""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_RELATIVE_PATH = "verification/SUBMISSION_MANIFEST.json"
MANIFEST = REPOSITORY_ROOT / MANIFEST_RELATIVE_PATH

old = json.loads(MANIFEST.read_text(encoding="utf-8"))

RESCAN_DIRS = (
    "analysis_data",
    # 取整个 docs：real_agent_baseline_manifest.json
    # 直接躺在 examples 根下，被 test_real_agent_baseline_manifest.py 当作基线口径
    # 读取，却一直没进提交清单（rglob 会连带覆盖 templates 子目录）。
    "docs",
    "momo_agent/backend/app",
    "momo_agent/backend/tests",
    "platform-ui/src",
    "platform-ui/dist",
    "pyansys_bridge",
    "tools",
    "verification",
)


def excluded(rel: str) -> bool:
    return (
        "/.pytest_cache/" in rel
        or rel.startswith("momo_agent/backend/.pytest_cache/")
        or "/__pycache__/" in rel
        or rel.endswith(".pyc")
        or rel == MANIFEST_RELATIVE_PATH
    )


# 1) 收集提交范围内的路径
paths: set[str] = set()
for item in old["files"]:
    rel = item["path"]
    if not excluded(rel):
        paths.add(rel)

# 2) 按磁盘重扫本次改造涉及的目录（捕获新增文件）
for directory in RESCAN_DIRS:
    base = REPOSITORY_ROOT / directory
    if base.exists():
        for p in base.rglob("*"):
            if p.is_file():
                rel = p.relative_to(REPOSITORY_ROOT).as_posix()
                if not excluded(rel):
                    paths.add(rel)

# 3) 全量重算 SHA，丢弃已删除的文件
files: list[dict] = []
dropped: list[str] = []
for rel in sorted(paths):
    p = REPOSITORY_ROOT / rel
    if not p.is_file():
        dropped.append(rel)
        continue
    data = p.read_bytes()
    files.append({
        "path": rel,
        "sizeBytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    })

# 防呆
assert not any(f["path"] == MANIFEST_RELATIVE_PATH for f in files)
assert not any("node_modules/" in f["path"] for f in files)
assert not any(".venv/" in f["path"] for f in files)
assert not any(excluded(f["path"]) for f in files)

new = dict(old)
new["generatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
new["fileCount"] = len(files)
new["totalSizeBytes"] = sum(f["sizeBytes"] for f in files)
new["files"] = files

MANIFEST.write_text(json.dumps(new, indent=2, ensure_ascii=False), encoding="utf-8")

print(f"fileCount: {old['fileCount']} -> {new['fileCount']}")
print(f"dropped (已删除的文件): {len(dropped)}")
for rel in dropped:
    print("   -", rel)
