from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


SUBMISSION_ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('verify_submission_module', SUBMISSION_ROOT / 'verify_submission.py')
assert SPEC and SPEC.loader
verify_submission = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_submission)


def _write_manifest(root: Path, files: list[dict[str, object]], *, file_count: int | None = None) -> Path:
    manifest_path = root / 'SUBMISSION_MANIFEST.json'
    manifest_path.write_text(
        json.dumps({'fileCount': len(files) if file_count is None else file_count, 'files': files}),
        encoding='utf-8',
    )
    return manifest_path


def test_verify_submission_manifest_accepts_complete_manifest(tmp_path: Path) -> None:
    source = tmp_path / 'app.py'
    source.write_text('print("ok")\n', encoding='utf-8')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = _write_manifest(tmp_path, [{'path': 'app.py', 'sha256': digest}])

    result = verify_submission.verify_submission_manifest(tmp_path, manifest)

    assert result['fileCount'] == 1


@pytest.mark.parametrize(
    ('files', 'file_count', 'error'),
    [
        ([{'path': 'app.py', 'sha256': '0' * 64}], 2, 'fileCount'),
        (
            [
                {'path': 'app.py', 'sha256': '0' * 64},
                {'path': 'app.py', 'sha256': '0' * 64},
            ],
            2,
            '重复',
        ),
        (
            [
                {'path': 'app.py', 'sha256': '0' * 64},
                {'path': 'APP.py', 'sha256': '0' * 64},
            ],
            2,
            '重复',
        ),
        ([{'path': '../outside.py', 'sha256': '0' * 64}], 1, '路径'),
        ([{'path': 'missing.py', 'sha256': '0' * 64}], 1, '不存在'),
        ([{'path': 'app.py', 'sha256': '0' * 64}], 1, '校验失败'),
    ],
)
def test_verify_submission_manifest_fails_closed(
    tmp_path: Path,
    files: list[dict[str, object]],
    file_count: int,
    error: str,
) -> None:
    (tmp_path / 'app.py').write_text('print("ok")\n', encoding='utf-8')
    manifest = _write_manifest(tmp_path, files, file_count=file_count)

    with pytest.raises((FileNotFoundError, RuntimeError), match=error):
        verify_submission.verify_submission_manifest(tmp_path, manifest)


def test_verify_submission_manifest_requires_manifest_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match='清单'):
        verify_submission.verify_submission_manifest(tmp_path, tmp_path / 'SUBMISSION_MANIFEST.json')
