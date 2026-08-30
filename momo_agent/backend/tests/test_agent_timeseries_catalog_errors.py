from __future__ import annotations

import json
from hashlib import sha256
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import app.services.agent_conversation as conversation_module
import app.services.agent_service as agent_service_module
from app.services.agent_service import AgentService
from app.services.platform_store import is_inquiry_result_csv


def _record(artifact_id: str, *, name: str, kind: str, path: str, content: bytes):
    return SimpleNamespace(
        artifact=SimpleNamespace(
            artifact_id=artifact_id,
            name=name,
            kind=kind,
            path=path,
            sha256=sha256(content).hexdigest(),
        ),
        content=content,
        preview={},
    )


def _service(records: dict, run: dict, monkeypatch) -> AgentService:
    class Store:
        def get_artifact(self, artifact_id: str):
            return records[artifact_id]

    class Repository:
        def get_run(self, run_id: str):
            return run if run_id == run['runId'] else None

    store = Store()
    service = AgentService()
    service.repository = lambda: Repository()
    monkeypatch.setattr(agent_service_module, 'platform_store', store)
    monkeypatch.setattr(conversation_module, 'platform_store', store)
    return service


def test_timeseries_reports_422_when_catalog_declares_unregistered_source(monkeypatch) -> None:
    """旧运行的结果目录声明了未登记的来源 CSV 时返回 422，而不是 500。

    _load_result_catalog 把"目录声明的来源没有登记制品"视为篡改并抛
    ResultInquiryError；该异常过去在 _run_csv_artifacts 处逃逸成 500。
    """
    ts_path = 'output/real/run_1/solver_outputs/a/timeseries.csv'
    ts_content = b'time,displacement\n0,0\n1,0.1\n'
    timeseries = _record('art_ts', name='timeseries.csv', kind='CSV_TIMESERIES', path=ts_path, content=ts_content)
    orphan_content = b'time,Elem835_MZ\n0,0\n1,5\n'
    catalog_content = json.dumps({
        'schemaVersion': '1.0',
        'runId': 'real_run_1',
        'verified': True,
        'entryCount': 2,
        'entries': [
            {
                'artifactPath': ts_path,
                'columns': ['time', 'displacement'],
                'units': {'time': 's', 'displacement': 'm'},
                'sha256': timeseries.artifact.sha256,
                'verified': True,
            },
            {
                # 目录里有，但从未登记成可追问制品。
                'artifactPath': 'output/real/run_1/solver_outputs/a/tower_base_member_moment.csv',
                'columns': ['time', 'Elem835_MZ'],
                'units': {'time': 's', 'Elem835_MZ': 'N*m'},
                'sha256': sha256(orphan_content).hexdigest(),
                'verified': True,
            },
        ],
    }, ensure_ascii=False).encode('utf-8')
    catalog = _record(
        'art_catalog',
        name='result_catalog.json',
        kind='JSON_SUMMARY',
        path='output/real/run_1/result_catalog.json',
        content=catalog_content,
    )
    records = {record.artifact.artifact_id: record for record in (timeseries, catalog)}
    run = {'runId': 'agr_broken', 'taskType': 'ANALYSIS', 'artifactIds': list(records)}
    service = _service(records, run, monkeypatch)

    with pytest.raises(HTTPException) as excinfo:
        service.get_run_timeseries('agr_broken', max_points=1000)

    assert excinfo.value.status_code == 422
    assert excinfo.value.detail['code'] == 'RESULT_CATALOG_INVALID'
    assert 'tower_base_member_moment.csv' in excinfo.value.detail['message']


def test_timeseries_still_serves_run_whose_catalog_matches_registration(monkeypatch) -> None:
    """目录与登记一致时仍返回时程，422 只针对损坏的目录。"""
    ts_path = 'output/real/run_2/solver_outputs/a/timeseries.csv'
    ts_content = b'time,displacement,tower_base_shear\n0,0,10\n1,0.1,-12\n'
    timeseries = _record('art_ts', name='timeseries.csv', kind='CSV_TIMESERIES', path=ts_path, content=ts_content)
    catalog_content = json.dumps({
        'schemaVersion': '1.0',
        'runId': 'real_run_2',
        'verified': True,
        'entryCount': 1,
        'entries': [{
            'artifactPath': ts_path,
            'columns': ['time', 'displacement', 'tower_base_shear'],
            'units': {'time': 's', 'displacement': 'm', 'tower_base_shear': 'N'},
            'sha256': timeseries.artifact.sha256,
            'verified': True,
        }],
    }, ensure_ascii=False).encode('utf-8')
    catalog = _record(
        'art_catalog',
        name='result_catalog.json',
        kind='JSON_SUMMARY',
        path='output/real/run_2/result_catalog.json',
        content=catalog_content,
    )
    records = {record.artifact.artifact_id: record for record in (timeseries, catalog)}
    run = {'runId': 'agr_ok', 'taskType': 'ANALYSIS', 'artifactIds': list(records)}
    service = _service(records, run, monkeypatch)

    payload = service.get_run_timeseries('agr_ok', max_points=1000)

    assert payload['availableColumns'] == ['time', 'displacement', 'tower_base_shear']
    assert payload['series']['tower_base_shear'] == [10.0, -12.0]
    assert payload['peaks']['tower_base_shear']['peakAbsolute'] == 12.0


def test_inquiry_result_csv_predicate_covers_every_solver_output_but_index(tmp_path) -> None:
    """结果目录与可追问制品共用同一判定：除 index.csv 外的求解 CSV 都要登记。

    ANSYS 运行会额外产出 tower_base_section_force.csv 等文件；旧白名单漏掉它们，
    导致目录声明的来源没有对应制品。
    """
    names = [
        'timeseries.csv',
        'tower_base_shear_components.csv',
        'tower_girder_relative_response.csv',
        'tower_base_section_force.csv',
        'tower_base_member_moment.csv',
        'damper_force.csv',
        'ansys_damper_node_response.csv',
    ]
    for name in names:
        (tmp_path / name).write_bytes(b'time,value\n0,0\n')
    (tmp_path / 'index.csv').write_bytes(b'case_id,status\nc1,OK\n')

    assert all(is_inquiry_result_csv(tmp_path / name) for name in names)
    assert not is_inquiry_result_csv(tmp_path / 'index.csv')
    assert not is_inquiry_result_csv(tmp_path / 'missing.csv')
    assert not is_inquiry_result_csv(tmp_path)
