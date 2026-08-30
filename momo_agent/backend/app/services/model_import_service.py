from __future__ import annotations

import re
from hashlib import sha256
from pathlib import Path, PurePath
from typing import Any

from fastapi import HTTPException

from app.services.platform_store import gen_id, platform_store


MAX_MODEL_BYTES = 50 * 1024 * 1024
ALLOWED_MODEL_SUFFIXES = {'.txt', '.dat', '.inp', '.cdb', '.apdl', '.mac'}

# APDL 模型在受控执行环境里以命令流方式运行，必须拦截外壳逃逸与
# 破坏性文件操作。未公开命令（~ 前缀）行为不可审计，一并拒绝。
FORBIDDEN_APDL_COMMANDS: tuple[str, ...] = ('/sys', '/syp', '/delete', '~')

_NODE_COMMAND = re.compile(r'^\s*n\s*,', re.IGNORECASE)
_ELEMENT_COMMAND = re.compile(r'^\s*(e|en)\s*,', re.IGNORECASE)
_DO_LOOP = re.compile(r'^\s*\*do\b', re.IGNORECASE)
_BLOCK_COMMAND = re.compile(r'^\s*(nblock|eblock)\b', re.IGNORECASE)


class ModelImportService:
    """校验并登记用户上传的 ANSYS APDL 有限元模型。

    APDL 模型普遍使用 *do 循环和参数表达式建模，静态解析无法枚举
    最终节点/单元集合；这里只做安全扫描与结构统计，节点存在性由
    求解后处理阶段按 RST 实际内容校验。
    """

    def inspect(self, file_name: str, content: bytes) -> dict[str, Any]:
        self._validate_file_name(file_name)
        if not content:
            self._error('EMPTY_FILE', '模型文件为空')
        if len(content) > MAX_MODEL_BYTES:
            self._error('FILE_TOO_LARGE', '模型文件上限为 50 MB')
        suffix = Path(file_name).suffix.lower()
        if suffix not in ALLOWED_MODEL_SUFFIXES:
            self._error(
                'UNSUPPORTED_FILE_TYPE',
                f'仅支持 APDL 文本模型（{", ".join(sorted(ALLOWED_MODEL_SUFFIXES))}）',
            )
        text = self._decode_text(content)
        lines = text.splitlines()

        forbidden_hits: list[dict[str, Any]] = []
        node_commands = 0
        element_commands = 0
        do_loops = 0
        block_commands = 0
        has_prep7 = False
        for line_number, line in enumerate(lines, start=1):
            stripped = line.strip().lower()
            if not stripped or stripped.startswith('!'):
                continue
            for marker in FORBIDDEN_APDL_COMMANDS:
                if stripped.startswith(marker):
                    forbidden_hits.append({'line': line_number, 'command': line.strip()[:120]})
                    break
            if stripped.startswith('/prep7'):
                has_prep7 = True
            if _NODE_COMMAND.match(line):
                node_commands += 1
            elif _ELEMENT_COMMAND.match(line):
                element_commands += 1
            elif _DO_LOOP.match(line):
                do_loops += 1
            elif _BLOCK_COMMAND.match(line):
                block_commands += 1

        if forbidden_hits:
            raise HTTPException(status_code=422, detail={
                'code': 'FORBIDDEN_APDL_COMMAND',
                'message': '模型包含被禁止的 APDL 命令（外壳执行/文件删除/未公开命令）',
                'hits': forbidden_hits[:10],
            })
        if not has_prep7:
            self._error('MISSING_PREP7', '模型必须包含 /PREP7 前处理段')
        if node_commands == 0 and block_commands == 0:
            self._error('NO_NODE_DEFINITIONS', '模型中没有节点定义（N 命令或 NBLOCK）')
        if element_commands == 0 and block_commands == 0:
            self._error('NO_ELEMENT_DEFINITIONS', '模型中没有单元定义（E/EN 命令或 EBLOCK）')

        return {
            'fileName': file_name,
            'format': 'APDL',
            'sha256': sha256(content).hexdigest(),
            'sizeBytes': len(content),
            'lineCount': len(lines),
            'nodeCommandCount': node_commands,
            'elementCommandCount': element_commands,
            'doLoopCount': do_loops,
            'blockCommandCount': block_commands,
            'parametricModel': do_loops > 0,
            'validation': {
                'forbiddenCommandScan': 'PASSED',
                'nodeIdEnumeration': (
                    'SKIPPED_PARAMETRIC' if do_loops > 0 else 'STATIC_COMMANDS_ONLY'
                ),
                'note': '节点/单元存在性在求解后处理阶段按 RST 实际内容校验。',
            },
        }

    def upload(self, file_name: str, content: bytes) -> dict[str, Any]:
        inspection = self.inspect(file_name, content)
        upload_id = gen_id('femup')
        artifact = platform_store.register_artifact(
            kind='FEM_MODEL',
            name=file_name,
            path=f'output/platform_store/model_uploads/{upload_id}/{file_name}',
            mime_type='text/plain; charset=utf-8',
            preview=inspection,
            content=content,
        )
        return {
            'artifactId': artifact.artifact_id,
            'sha256': artifact.sha256,
            'fileName': file_name,
            'inspection': inspection,
            'usage': {
                'solver': 'ANSYS',
                'howTo': (
                    '在对话中说明“使用我上传的模型 <artifactId> 进行地震分析，'
                    '输出节点 <编号列表> 的响应”，即可用该模型创建受控分析。'
                ),
            },
        }

    def list_models(self) -> list[dict[str, Any]]:
        models = []
        for record in platform_store.artifacts:
            artifact = record.artifact
            if artifact.kind != 'FEM_MODEL':
                continue
            models.append({
                'artifactId': artifact.artifact_id,
                'name': artifact.name,
                'sha256': artifact.sha256,
                'sizeBytes': artifact.size_bytes,
                'createdAt': artifact.created_at,
            })
        return models

    @staticmethod
    def _decode_text(content: bytes) -> str:
        for encoding in ('utf-8-sig', 'utf-8', 'gb18030', 'latin-1'):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        ModelImportService._error('UNSUPPORTED_ENCODING', '无法识别模型文本编码')
        raise AssertionError('unreachable')

    @staticmethod
    def _validate_file_name(file_name: str) -> None:
        if not file_name or PurePath(file_name).name != file_name or '/' in file_name or '\\' in file_name:
            raise HTTPException(status_code=422, detail={'code': 'INVALID_FILE_NAME', 'message': '文件名不合法'})

    @staticmethod
    def _error(code: str, message: str) -> None:
        raise HTTPException(status_code=422, detail={'code': code, 'message': message})


model_import_service = ModelImportService()
