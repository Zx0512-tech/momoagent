from __future__ import annotations

from types import SimpleNamespace

from app.agents.evidence_gates import (
    artifacts_have_no_non_real_markers,
    input_provenance_passed,
    output_manifest_passed,
    result_catalog_passed,
    solver_version_profile_passed,
)


class _Store:
    def __init__(self, previews: dict[str, object]) -> None:
        self.previews = previews

    def get_artifact(self, artifact_id: str) -> SimpleNamespace:
        return SimpleNamespace(preview=self.previews[artifact_id])


def _profile(*, user300: bool = False) -> dict:
    payload = {
        'schemaVersion': '1.0',
        'solver': {'name': 'ANSYS_MAPDL', 'version': '2024 R1'},
        'responseContract': {'id': 'ANSYS_RESPONSE_R1'},
    }
    if user300:
        payload['userElement'] = {'name': 'USER300', 'calibrationHashVerified': True}
    return payload


def test_solver_profile_gate_supports_analysis_and_user300_modes() -> None:
    assert solver_version_profile_passed(_profile(), require_user300=False)
    assert not solver_version_profile_passed(_profile(), require_user300=True)
    assert solver_version_profile_passed(_profile(user300=True), require_user300=True)


def test_solver_profile_gate_rejects_missing_solver_version_evidence() -> None:
    for invalid_version in (None, '', 'UNKNOWN', 'NOT_INSTALLED'):
        profile = _profile()
        profile['solver']['version'] = invalid_version
        assert not solver_version_profile_passed(profile, require_user300=False)


def test_input_provenance_requires_unique_complete_fields_and_allowed_sources() -> None:
    valid = [
        {'field': 'solver', 'source': 'USER_DECISION'},
        {'field': 'workflowConfigPath', 'source': 'VERIFIED_TEMPLATE'},
        {'field': 'loadCase', 'source': 'VERIFIED_TEMPLATE'},
    ]
    assert input_provenance_passed(valid)
    assert not input_provenance_passed(valid[:-1])
    assert not input_provenance_passed(valid + [{'field': 'solver', 'source': 'USER_DECISION'}])
    assert not input_provenance_passed(valid[:2] + [{'field': 'loadCase', 'source': 'MODEL_GUESS'}])


def test_output_manifest_rejects_path_traversal_and_binds_artifact_hash() -> None:
    manifest = {
        'schemaVersion': '1.0',
        'fileCount': 1,
        'files': [{'path': 'results/summary.json', 'sizeBytes': 10, 'sha256': 'a' * 64}],
    }
    artifact = {'artifactId': 'manifest-1', 'name': 'real_output_manifest.json', 'sha256': 'b' * 64}
    store = _Store({'manifest-1': manifest})
    result = {'outputManifestArtifactId': 'manifest-1', 'outputManifestSha256': 'b' * 64}
    assert output_manifest_passed(result, [artifact], store)

    manifest['files'][0]['path'] = '../outside.json'
    assert not output_manifest_passed(result, [artifact], store)


def test_artifact_gate_rejects_non_real_markers() -> None:
    artifacts = [{'artifactId': 'a1'}]
    assert artifacts_have_no_non_real_markers(artifacts, _Store({'a1': {'mode': 'REAL'}}))
    assert not artifacts_have_no_non_real_markers(artifacts, _Store({'a1': {'dry_run': True}}))


def test_result_catalog_gate_requires_verified_columns_units_and_source_hash() -> None:
    artifact = {'artifactId': 'catalog-1', 'name': 'result_catalog.json', 'sha256': 'c' * 64}
    catalog = {
        'schemaVersion': '1.0',
        'verified': True,
        'entryCount': 1,
        'entries': [{
            'artifactPath': 'timeseries.csv',
            'columns': ['time', 'displacement'],
            'units': {'time': 's', 'displacement': 'm'},
            'sha256': 'd' * 64,
            'verified': True,
        }],
    }
    store = _Store({'catalog-1': catalog})
    assert result_catalog_passed({'resultCatalogArtifactId': 'catalog-1'}, [artifact], store)

    catalog['entries'][0]['sha256'] = 'invalid'
    assert not result_catalog_passed({'resultCatalogArtifactId': 'catalog-1'}, [artifact], store)
