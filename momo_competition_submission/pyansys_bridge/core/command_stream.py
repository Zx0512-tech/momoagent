"""Composable solver command stream modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined

from pyansys_bridge.models import LoadCase
from pyansys_bridge.templates.template_schema import validate_rendered_template, validate_template_context
from pyansys_bridge.template_registry import iter_command_templates


DEFAULT_MODULE_ORDER = ("model", "damper", "gravity", "modal", "earthquake", "wind", "traffic", "postprocess")
_JINJA_ENV = Environment(
    keep_trailing_newline=True,
    trim_blocks=False,
    lstrip_blocks=False,
    autoescape=False,  # nosec B701 - solver command templates render plain text, not HTML.
    undefined=StrictUndefined,
)


@dataclass(frozen=True)
class CommandModule:
    """A reusable command block for one solver and one workflow role."""

    name: str
    solver: str
    role: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        metadata = dict(self.metadata)
        syntax = metadata.setdefault("template_syntax", "jinja2")
        if syntax != "jinja2":
            raise ValueError("CommandModule templates must use jinja2 syntax")
        object.__setattr__(self, "metadata", metadata)

    @classmethod
    def from_file(
        cls,
        name: str,
        solver: str,
        role: str,
        path: str | Path,
        *,
        syntax: str = "jinja2",
    ) -> "CommandModule":
        if syntax != "jinja2":
            raise ValueError("CommandModule templates must use jinja2 syntax")
        source = Path(path)
        return cls(
            name=name,
            solver=solver,
            role=role,
            content=source.read_text(encoding="utf-8"),
            metadata={"source_path": str(source), "template_syntax": syntax},
        )

    def render(self, context: dict[str, Any] | None = None) -> str:
        render_context = dict(context or {})
        render_context.setdefault("command_role", self.role)
        render_context.setdefault("command_module_name", self.name)
        return _JINJA_ENV.from_string(self.content).render(**render_context).strip()


class CommandStreamAssembler:
    """Assemble model, damper, modal, and load modules into one command stream."""

    def __init__(self, modules: list[CommandModule], order: tuple[str, ...] = DEFAULT_MODULE_ORDER) -> None:
        self.modules = modules
        self.order = order

    def assemble(
        self,
        solver: str,
        roles: tuple[str, ...],
        context: dict[str, Any] | None = None,
        module_names: dict[str, str] | None = None,
    ) -> str:
        if not roles:
            raise ValueError("At least one command module role is required")

        selected = self._select_modules(solver, roles, module_names or {})
        if len(selected) != len(roles):
            missing = sorted(set(roles) - {module.role for module in selected})
            raise ValueError(f"Missing command modules for solver {solver}: {', '.join(missing)}")

        rendered = []
        for module in selected:
            validation_context = dict(context or {})
            validation_context.setdefault("command_role", module.role)
            validation_context.setdefault("command_module_name", module.name)
            validate_template_context(module, validation_context)
            block = module.render(context)
            validate_rendered_template(
                block,
                template_name=f"{module.solver}/{module.name}",
                check_legacy_placeholders=module.solver.lower() == "ansys",
            )
            rendered.append(block)
        return "\n\n".join(block for block in rendered if block)

    def render_to_string(
        self,
        solver: str,
        roles: tuple[str, ...],
        context: dict[str, Any] | None = None,
        module_names: dict[str, str] | None = None,
    ) -> str:
        """Render a command stream without writing it to disk."""

        return self.assemble(
            solver,
            roles,
            context=context,
            module_names=module_names,
        )

    def _select_modules(
        self,
        solver: str,
        roles: tuple[str, ...],
        module_names: dict[str, str],
    ) -> list[CommandModule]:
        normalized_solver = solver.lower()
        order_index = {role: index for index, role in enumerate(self.order)}
        requested_index = {role: index for index, role in enumerate(roles)}
        selected = []
        for role in roles:
            candidates = [
                module
                for module in self.modules
                if module.solver.lower() == normalized_solver and module.role == role
            ]
            preferred = module_names.get(role)
            if preferred is not None:
                candidates = [module for module in candidates if module.name == preferred]
            if len(candidates) > 1:
                names = ", ".join(module.name for module in candidates)
                raise ValueError(f"Multiple command modules for solver {solver} role {role}: {names}")
            if candidates:
                selected.append(candidates[0])
        return sorted(
            selected,
            key=lambda module: (
                order_index.get(module.role, len(self.order)),
                requested_index.get(module.role, len(roles)),
                module.name,
            ),
        )


def default_command_modules() -> list[CommandModule]:
    """Return file-backed command modules for dry-run ANSYS and OpenSeesPy streams."""

    return template_command_modules()


def template_command_modules(template_root: str | Path | None = None) -> list[CommandModule]:
    """Load reusable command modules from the packaged template directory."""

    root = Path(template_root) if template_root is not None else Path(__file__).resolve().parents[1] / "templates"
    modules = []
    for template in iter_command_templates():
        modules.append(
            CommandModule.from_file(
                name=template.name,
                solver=template.solver,
                role=template.role,
                path=root / template.relative_path,
                syntax=template.syntax,
            )
        )
    return modules


def built_in_command_modules() -> list[CommandModule]:
    """Return fallback in-code modules, mainly for tests that do not ship templates."""

    return [
        CommandModule(
            name="model_apdl",
            solver="ansys",
            role="model",
            content="/CLEAR\n! 允许 USER300/USERRC 在长瞬态分析中重复输出 warning\n/NERR,999999,999999\n/PREP7\n/INPUT,{{ model_path }}",
        ),
        CommandModule(
            name="damper_viscous",
            solver="ansys",
            role="damper",
            content="! Viscous damper module\n{{ damper_commands }}",
        ),
        CommandModule(
            name="modal_apdl",
            solver="ansys",
            role="modal",
            content=(
                "! Modal analysis module\n"
                "/SOLU\nANTYPE,MODAL\nMODOPT,LANB,{{ modal_modes }},,,2,off\n"
                "MXPAND,{{ modal_modes }},,,YES\n"
                "ACEL,{{ gravity_accel_x }},{{ gravity_accel_y }},{{ gravity_accel_z }}\n"
                "PSTRES,1\nALLS\nSOLVE\nFINISH\n"
                "/POST1\nSET,LIST\n*GET,M,MODE,1,FREQ\n*GET,N,MODE,2,FREQ\nFINISH"
            ),
        ),
        CommandModule(
            name="gravity_apdl",
            solver="ansys",
            role="gravity",
            content=(
                "! Gravity static preload module\n"
                "/SOLU\nANTYPE,STATIC\nACEL,{{ gravity_accel_x }},{{ gravity_accel_y }},{{ gravity_accel_z }}\n"
                "TIME,{{ gravity_time }}\nNSUBST,{{ gravity_substeps }}\nOUTRES,ALL,ALL\n"
                "SSTIF,ON\nPSTRES,ON\nALLS\nSOLVE\nFINISH"
            ),
        ),
        CommandModule(
            name="earthquake_apdl",
            solver="ansys",
            role="earthquake",
            content=(
                "! Earthquake load module\n"
                "! load={{ earthquake_name }}, path={{ earthquake_path }}, scale={{ earthquake_scale }}, "
                "dt={{ earthquake_dt }}, duration={{ earthquake_duration }}\n"
                "{{ ansys_earthquake_table_commands }}"
            ),
        ),
        CommandModule(
            name="traffic_apdl",
            solver="ansys",
            role="traffic",
            content=(
                "! Traffic load module\n"
                "! load={{ traffic_name }}, path={{ traffic_path }}, scale={{ traffic_scale }}, "
                "dt={{ traffic_dt }}, duration={{ traffic_duration }}\n"
                "{{ ansys_traffic_table_commands }}"
            ),
        ),
        CommandModule(
            name="wind_apdl",
            solver="ansys",
            role="wind",
            content=(
                "! Wind load module\n"
                "! load={{ wind_name }}, path={{ wind_path }}, scale={{ wind_scale }}, "
                "dt={{ wind_dt }}, duration={{ wind_duration }}\n"
                "{{ ansys_wind_table_commands }}"
            ),
        ),
        CommandModule(
            name="model_openseespy",
            solver="opensees",
            role="model",
            content=(
                "import runpy\n"
                "import openseespy.opensees as ops\n\n"
                "model_globals = runpy.run_path(r\"{{ model_path }}\")\n"
                "configured_load_nodes = {{ opensees_load_nodes }}\n"
                "for node_name, configured_nodes in configured_load_nodes.items():\n"
                "    if configured_nodes is not None:\n"
                "        model_globals[node_name] = configured_nodes\n"
                "model_builder = model_globals.get('build_command_stream_model') or model_globals.get('build_model')\n"
                "if callable(model_builder):\n"
                "    builder_result = model_builder()\n"
                "    if isinstance(builder_result, dict):\n"
                "        model_globals.update(builder_result)\n"
                "if not ops.getNodeTags():\n"
                "    raise RuntimeError('OpenSees model module did not build any nodes')"
            ),
        ),
        CommandModule(
            name="damper_viscous",
            solver="opensees",
            role="damper",
            content="# Viscous damper module\nimport openseespy.opensees as ops\n\n{{ damper_commands }}",
        ),
        CommandModule(
            name="modal_openseespy",
            solver="opensees",
            role="modal",
            content="# Modal analysis module\nmodal_eigenvalues = ops.eigen({{ modal_modes }})",
        ),
        CommandModule(
            name="gravity_openseespy",
            solver="opensees",
            role="gravity",
            content=(
                "# Gravity static preload module\n"
                "opensees_system = {{ opensees_system }}\n"
                "opensees_system_args = {{ opensees_system_args }}\n"
                "if model_globals.get('gravity_preloaded'):\n"
                "    pass\n"
                "else:\n"
                "gravity_nodes = model_globals.get('gravity_load_nodes', model_globals.get('deck_nodes', []))\n"
                "    if not gravity_nodes:\n"
                "        gravity_nodes = ops.getNodeTags()\n"
                "    if not gravity_nodes:\n"
                "        raise RuntimeError('OpenSees gravity module requires model nodes')\n"
                "    ops.timeSeries('Linear', 3001)\n"
                "    ops.pattern('Plain', 3001, 3001)\n"
                "    for node_tag in gravity_nodes:\n"
                "        ops.load(int(node_tag), {{ gravity_accel_x }}, {{ gravity_accel_y }}, {{ gravity_accel_z }}, 0.0, 0.0, 0.0)\n"
                "    ops.constraints('Transformation')\n"
                "    ops.numberer('RCM')\n"
                "    ops.system(opensees_system, *opensees_system_args)\n"
                "    ops.test('NormDispIncr', 1.0e-8, 20)\n"
                "    ops.algorithm('Newton')\n"
                "    ops.integrator('LoadControl', 1.0 / {{ gravity_substeps }})\n"
                "    ops.analysis('Static')\n"
                "    if ops.analyze({{ gravity_substeps }}) != 0:\n"
                "        raise RuntimeError('OpenSees gravity static preload failed')\n"
                "    ops.loadConst('-time', 0.0)"
            ),
        ),
        CommandModule(
            name="earthquake_openseespy",
            solver="opensees",
            role="earthquake",
            content=(
                "# Earthquake load module\n"
                "# load={{ earthquake_name }}, path={{ earthquake_path }}, scale={{ earthquake_scale }}, "
                "dt={{ earthquake_dt }}, duration={{ earthquake_duration }}\n"
                "eq_path = r\"{{ earthquake_path }}\"\n"
                "if eq_path and eq_path != \"None\":\n"
                "    ops.timeSeries('Path', 4101, '-filePath', eq_path, "
                "'-dt', {{ earthquake_dt }}, '-factor', {{ earthquake_scale }})\n"
                "else:\n"
                "    ops.timeSeries('Constant', 4101, '-factor', {{ earthquake_scale }})\n"
                "ops.pattern('UniformExcitation', 4101, 1, '-accel', 4101)"
            ),
        ),
        CommandModule(
            name="traffic_openseespy",
            solver="opensees",
            role="traffic",
            content=(
                "# Traffic load module\n"
                "# load={{ traffic_name }}, path={{ traffic_path }}, scale={{ traffic_scale }}, "
                "dt={{ traffic_dt }}, duration={{ traffic_duration }}\n"
                "traffic_path = r\"{{ traffic_path }}\"\n"
                "if traffic_path and traffic_path != \"None\":\n"
                "    ops.timeSeries('Path', 4301, '-filePath', traffic_path, "
                "'-dt', {{ traffic_dt }}, '-factor', {{ traffic_scale }})\n"
                "else:\n"
                "    ops.timeSeries('Constant', 4301, '-factor', {{ traffic_scale }})\n"
                "ops.pattern('Plain', 4301, 4301)\n"
                "traffic_nodes = model_globals.get('traffic_load_nodes', model_globals.get('deck_nodes', []))\n"
                "if not traffic_nodes:\n"
                "    raise RuntimeError('OpenSees traffic load module requires traffic_load_nodes or deck_nodes')\n"
                "for node_tag in traffic_nodes:\n"
                "    ops.load(int(node_tag), {{ traffic_direction_x }}, {{ traffic_direction_y }}, {{ traffic_direction_z }}, "
                "0.0, 0.0, 0.0)"
            ),
        ),
        CommandModule(
            name="wind_openseespy",
            solver="opensees",
            role="wind",
            content=(
                "# Wind load module\n"
                "# load={{ wind_name }}, path={{ wind_path }}, scale={{ wind_scale }}, "
                "dt={{ wind_dt }}, duration={{ wind_duration }}\n"
                "wind_path = r\"{{ wind_path }}\"\n"
                "if wind_path and wind_path != \"None\":\n"
                "    ops.timeSeries('Path', 4201, '-filePath', wind_path, "
                "'-dt', {{ wind_dt }}, '-factor', {{ wind_scale }})\n"
                "else:\n"
                "    ops.timeSeries('Constant', 4201, '-factor', {{ wind_scale }})\n"
                "ops.pattern('Plain', 4201, 4201)\n"
                "wind_nodes = model_globals.get('wind_load_nodes', model_globals.get('deck_nodes', []))\n"
                "if not wind_nodes:\n"
                "    raise RuntimeError('OpenSees wind load module requires wind_load_nodes or deck_nodes')\n"
                "for node_tag in wind_nodes:\n"
                "    ops.load(int(node_tag), {{ wind_direction_x }}, {{ wind_direction_y }}, {{ wind_direction_z }}, "
                "0.0, 0.0, 0.0)"
            ),
        ),
        CommandModule(
            name="postprocess_apdl",
            solver="ansys",
            role="postprocess",
            content=(
                "! Analysis execution and postprocess handoff\n"
                "{{ ansys_solver_execution_commands }}\n"
                "! summary extraction is handled by configured postprocessor"
            ),
        ),
        CommandModule(
            name="postprocess_openseespy",
            solver="opensees",
            role="postprocess",
            content=(
                "# Transient analysis execution and postprocess handoff\n"
                "from csv import writer\n"
                "from pathlib import Path\n\n"
                "analysis_dt = {{ load_dt }}\n"
                "analysis_duration = {{ load_duration }}\n"
                "response_nodes = {{ response_nodes }}\n"
                "response_dof = {{ response_dof }}\n"
                "tower_base_nodes = {{ tower_base_nodes }}\n"
                "tower_base_shear_dofs = {{ tower_base_shear_dofs }}\n"
                "tower_base_moment_dofs = {{ tower_base_moment_dofs }}\n"
                "damper_placement_pairs = {{ damper_placement_pairs }}\n"
                "damping_ratio = float({{ damping_ratio }})\n"
                "opensees_system = {{ opensees_system }}\n"
                "opensees_system_args = {{ opensees_system_args }}\n"
                "rayleigh_frequency_a_hz = {{ rayleigh_frequency_a_hz }}\n"
                "rayleigh_frequency_b_hz = {{ rayleigh_frequency_b_hz }}\n"
                "if analysis_dt is None or analysis_duration is None:\n"
                "    raise ValueError('OpenSees transient analysis requires load dt and duration')\n"
                "\n"
                "def _signed_envelope(values):\n"
                "    return max(values, key=lambda value: abs(value), default=0.0)\n"
                "\n"
                "def _element_envelope(response):\n"
                "    return _signed_envelope(\n"
                "        [\n"
                "            float(value)\n"
                "            for element in damper_elements\n"
                "            for value in ops.eleResponse(element, response)\n"
                "        ]\n"
                "    )\n"
                "\n"
                "def _response_displacements():\n"
                "    return [float(ops.nodeDisp(node, response_dof)) for node in response_nodes]\n"
                "\n"
                "def _relative_to_baseline(values, baseline):\n"
                "    return [\n"
                "        float(value) - float(baseline[index])\n"
                "        for index, value in enumerate(values)\n"
                "    ]\n"
                "\n"
                "def _standard_response_displacements():\n"
                "    return _relative_to_baseline(_response_displacements(), baseline_response_displacements)\n"
                "\n"
                "def _response_displacement_increments():\n"
                "    return _relative_to_baseline(_response_displacements(), baseline_response_displacements)\n"
                "\n"
                "def _placement_strokes(pairs, dof):\n"
                "    strokes = []\n"
                "    for pair in pairs:\n"
                "        if len(pair) == 2:\n"
                "            node_i, node_j = pair\n"
                "            pair_dof = dof\n"
                "        else:\n"
                "            node_i, node_j, pair_dof = pair\n"
                "        strokes.append(float(ops.nodeDisp(int(node_j), int(pair_dof))) - float(ops.nodeDisp(int(node_i), int(pair_dof))))\n"
                "    return strokes\n"
                "\n"
                "def _placement_stroke_envelope(pairs, dof):\n"
                "    strokes = _placement_strokes(pairs, dof)\n"
                "    if strokes:\n"
                "        return _signed_envelope(strokes)\n"
                "    return _element_envelope('basicDeformation')\n"
                "\n"
                "def _node_reaction_totals(nodes, dofs):\n"
                "    node_reactions = {int(node): ops.nodeReaction(int(node)) for node in nodes}\n"
                "    totals = []\n"
                "    for dof in dofs:\n"
                "        index = dof - 1\n"
                "        total = 0.0\n"
                "        for values in node_reactions.values():\n"
                "            if index < len(values):\n"
                "                total += float(values[index])\n"
                "        totals.append(total)\n"
                "    return totals\n"
                "\n"
                "def _write_response_row(output):\n"
                "    ops.reactions()\n"
                "    output.writerow(\n"
                "        [\n"
                "            float(ops.getTime()),\n"
                "            _signed_envelope(_standard_response_displacements()),\n"
                "            _signed_envelope(_response_displacement_increments()),\n"
                "            _signed_envelope(_response_displacements()),\n"
                "            _signed_envelope([float(ops.nodeAccel(node, response_dof)) for node in response_nodes]),\n"
                "            _signed_envelope(_relative_to_baseline(_node_reaction_totals(tower_base_nodes, tower_base_moment_dofs), baseline_tower_base_moments)),\n"
                "            _signed_envelope(_relative_to_baseline(_node_reaction_totals(tower_base_nodes, tower_base_shear_dofs), baseline_tower_base_shears)),\n"
                "            _element_envelope('basicForce'),\n"
                "            _signed_envelope(_relative_to_baseline(_placement_strokes(damper_placement_pairs, response_dof), baseline_placement_strokes))\n"
                "            if damper_placement_pairs\n"
                "            else _element_envelope('basicDeformation'),\n"
                "        ]\n"
                "    )\n"
                "\n"
                "ops.wipeAnalysis()\n"
                "ops.constraints('Transformation')\n"
                "ops.numberer('RCM')\n"
                "ops.system(opensees_system, *opensees_system_args)\n"
                "ops.test('NormDispIncr', 1.0e-8, 20)\n"
                "ops.algorithm('Newton')\n"
                "ops.integrator('Newmark', 0.5, 0.25)\n"
                "if damping_ratio > 0.0:\n"
                "    freq_a = rayleigh_frequency_a_hz or 0.055317\n"
                "    freq_b = rayleigh_frequency_b_hz or 0.100449\n"
                "    alpha_m = 4.0 * 3.141592653589793 * damping_ratio * freq_a * freq_b / (freq_a + freq_b)\n"
                "    beta_k = damping_ratio / (3.141592653589793 * (freq_a + freq_b))\n"
                "    ops.rayleigh(alpha_m, beta_k, 0.0, 0.0)\n"
                "ops.analysis('Transient')\n"
                "ops.reactions()\n"
                "baseline_response_displacements = _response_displacements()\n"
                "baseline_placement_strokes = _placement_strokes(damper_placement_pairs, response_dof)\n"
                "baseline_tower_base_moments = _node_reaction_totals(tower_base_nodes, tower_base_moment_dofs)\n"
                "baseline_tower_base_shears = _node_reaction_totals(tower_base_nodes, tower_base_shear_dofs)\n"
                "analysis_steps = int(round(analysis_duration / analysis_dt))\n"
                "if analysis_steps <= 0:\n"
                "    raise ValueError('OpenSees transient analysis requires at least one time step')\n"
                "timeseries_path = Path(__file__).with_name('timeseries.csv')\n"
                "with timeseries_path.open('w', encoding='utf-8', newline='') as handle:\n"
                "    timeseries_writer = writer(handle)\n"
                "    timeseries_writer.writerow([\n"
                "        'time',\n"
                "        'displacement',\n"
                "        'displacement_increment',\n"
                "        'absolute_displacement',\n"
                "        'acceleration',\n"
                "        'tower_base_moment',\n"
                "        'tower_base_shear',\n"
                "        'damper_force',\n"
                "        'damper_stroke',\n"
                "    ])\n"
                "    _write_response_row(timeseries_writer)\n"
                "    for _ in range(analysis_steps):\n"
                "        if ops.analyze(1, analysis_dt) != 0:\n"
                "            raise RuntimeError('OpenSees transient analysis failed')\n"
                "        _write_response_row(timeseries_writer)"
            ),
        ),
    ]


def roles_for_load_case(
    load_case: LoadCase,
    include_modal: bool = False,
    *,
    solver: str | None = None,
) -> tuple[str, ...]:
    """Return command module roles needed by a load case."""

    load_types = _load_types(load_case)
    normalized_solver = "" if solver is None else str(solver).lower()
    operation_only = bool(load_types & {"wind", "traffic"}) and "earthquake" not in load_types
    use_separate_gravity = not (
        normalized_solver == "ansys"
        and operation_only
        and not include_modal
    )

    roles = ["model", "damper"]
    if use_separate_gravity:
        roles.append("gravity")
    if include_modal:
        roles.append("modal")

    for role in ("earthquake", "wind", "traffic"):
        if role in load_types:
            roles.append(role)
    roles.append("postprocess")
    return tuple(roles)


def _load_types(load_case: LoadCase) -> set[str]:
    if load_case.load_type != "combination":
        return {load_case.load_type}

    component_types = load_case.metadata.get("component_types")
    if component_types:
        return {str(item) for item in component_types}

    inferred = set()
    for component in load_case.components:
        lowered = component.lower()
        if "eq" in lowered or "earthquake" in lowered:
            inferred.add("earthquake")
        if "traffic" in lowered or "vehicle" in lowered or "car" in lowered:
            inferred.add("traffic")
        if "wind" in lowered:
            inferred.add("wind")
    return inferred
