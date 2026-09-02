from pathlib import Path

path = Path('scripts/pr9_apply.py')
text = path.read_text(encoding='utf-8')
text = text.replace('import importlib\n', 'import ast\n')
old = '''# Import the PR8 source once, before rewriting it, so descriptions/input models/risk metadata
# are migrated mechanically rather than copied into a second hand-maintained table.
harness_module = importlib.import_module('app.services.agent_harness')
legacy_specs = getattr(harness_module, '_HARNESS_TOOL_SPECS')
if set(legacy_specs) != set(POLICIES):
    missing = sorted(set(legacy_specs) - set(POLICIES))
    extra = sorted(set(POLICIES) - set(legacy_specs))
    raise RuntimeError(f'policy map mismatch: missing={missing}, extra={extra}')

capability_lines: list[str] = []
for capability_id, spec in sorted(legacy_specs.items()):
    prerequisites, evidence_policy = POLICIES[capability_id]
    capability_lines.extend([
        '    EngineeringCapability(',
        f'        capability_id={capability_id!r},',
        f'        description={spec.description!r},',
        f'        input_model={spec.input_model.__name__},',
        f'        risk=ToolRisk.{spec.risk.name},',
        f'        requires_approval={spec.requires_approval!r},',
        f'        prerequisites={prerequisites!r},',
        f'        evidence_policy=EvidencePolicy.{evidence_policy},',
        f'        idempotency_key_source={spec.idempotency_key_source!r},',
        '    ),',
    ])
'''
new = '''# Parse the PR8 static spec table without importing the application. Importing agent_harness
# pulls optional solver bridges into the migration process; AST keeps this one-shot migration
# deterministic and side-effect free.
source_before_migration = HARNESS.read_text(encoding='utf-8')
tree = ast.parse(source_before_migration)
spec_dict = None
for node in tree.body:
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == '_HARNESS_TOOL_SPECS':
        spec_dict = node.value
        break
if not isinstance(spec_dict, ast.Dict):
    raise RuntimeError('_HARNESS_TOOL_SPECS AST dict not found')


def _literal(node: ast.AST):
    return ast.literal_eval(node)


def _attr_expr(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f'{_attr_expr(node.value)}.{node.attr}'
    raise RuntimeError(f'unsupported expression: {ast.dump(node)}')


def _kw(call: ast.Call, name: str, default):
    for item in call.keywords:
        if item.arg == name:
            return _literal(item.value) if isinstance(item.value, ast.Constant) else _attr_expr(item.value)
    return default


legacy_specs: dict[str, dict[str, object]] = {}
for key_node, value_node in zip(spec_dict.keys, spec_dict.values, strict=True):
    capability_id = _literal(key_node)
    if not isinstance(value_node, ast.Call):
        raise RuntimeError(f'{capability_id}: capability spec is not a call')
    callee = _attr_expr(value_node.func)
    if callee == 'HarnessToolSpec':
        description = _literal(value_node.args[0])
        input_model = _attr_expr(value_node.args[1])
        idempotency = _literal(value_node.args[2]) if len(value_node.args) > 2 else None
        risk = _attr_expr(value_node.args[3]) if len(value_node.args) > 3 else 'ToolRisk.READ_ONLY'
        requires_approval = _literal(value_node.args[4]) if len(value_node.args) > 4 else False
    elif callee == '_run_spec':
        description = _literal(value_node.args[0])
        input_model = 'HarnessRunInput'
        server_derived = bool(_kw(value_node, 'server_derived_idempotency', False))
        idempotency = 'SERVER_DERIVED' if server_derived else None
        risk = str(_kw(value_node, 'risk', 'ToolRisk.READ_ONLY'))
        requires_approval = bool(_kw(value_node, 'requires_approval', False))
    else:
        raise RuntimeError(f'{capability_id}: unsupported spec constructor {callee}')
    legacy_specs[capability_id] = {
        'description': description,
        'input_model': input_model,
        'idempotency': idempotency,
        'risk': risk,
        'requires_approval': requires_approval,
    }

if set(legacy_specs) != set(POLICIES):
    missing = sorted(set(legacy_specs) - set(POLICIES))
    extra = sorted(set(POLICIES) - set(legacy_specs))
    raise RuntimeError(f'policy map mismatch: missing={missing}, extra={extra}')

capability_lines: list[str] = []
for capability_id, spec in sorted(legacy_specs.items()):
    prerequisites, evidence_policy = POLICIES[capability_id]
    capability_lines.extend([
        '    EngineeringCapability(',
        f'        capability_id={capability_id!r},',
        f'        description={spec["description"]!r},',
        f'        input_model={spec["input_model"]},',
        f'        risk={spec["risk"]},',
        f'        requires_approval={spec["requires_approval"]!r},',
        f'        prerequisites={prerequisites!r},',
        f'        evidence_policy=EvidencePolicy.{evidence_policy},',
        f'        idempotency_key_source={spec["idempotency"]!r},',
        '    ),',
    ])
'''
if text.count(old) != 1:
    raise RuntimeError(f'AST migration target count={text.count(old)}')
path.write_text(text.replace(old, new, 1), encoding='utf-8')
print('PR9 migration switched to AST source parsing')
