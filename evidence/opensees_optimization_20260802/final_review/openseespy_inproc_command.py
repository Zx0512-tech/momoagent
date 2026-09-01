import importlib.util
import openseespy.opensees as ops

model_spec = importlib.util.spec_from_file_location("stbridge_opensees_model", r"D:\momo\bridge_models\stbridge_opensees\stbridge_opensees_modal_builder.py")
if model_spec is None or model_spec.loader is None:
    raise RuntimeError(r"Cannot import OpenSees model module: D:\momo\bridge_models\stbridge_opensees\stbridge_opensees_modal_builder.py")
model_module = importlib.util.module_from_spec(model_spec)
model_spec.loader.exec_module(model_module)
model_globals = model_module.__dict__
configured_load_nodes = {'deck_nodes': (36, 107), 'wind_load_nodes': None, 'traffic_load_nodes': None}
model_globals['opensees_system'] = 'UmfPack'
model_globals['opensees_system_args'] = ()
setattr(model_module, 'opensees_system', model_globals['opensees_system'])
setattr(model_module, 'opensees_system_args', model_globals['opensees_system_args'])
for node_name, configured_nodes in configured_load_nodes.items():
    if configured_nodes is not None:
        model_globals[node_name] = configured_nodes
        setattr(model_module, node_name, configured_nodes)
model_builder = getattr(model_module, 'build_command_stream_model', None) or getattr(model_module, 'build_model', None)
model_context = {}
if callable(model_builder):
    model_context = model_builder()
    builder_result = model_context
    if isinstance(builder_result, dict):
        model_globals.update(builder_result)
        model_context = builder_result
if not isinstance(model_context, dict):
    model_context = {}
model_context.setdefault('model_module', model_module)
model_context.setdefault('model_globals', model_globals)
for node_name, configured_nodes in configured_load_nodes.items():
    if configured_nodes is not None:
        model_context[node_name] = configured_nodes
if not ops.getNodeTags():
    raise RuntimeError("OpenSees model module did not build any nodes")

# Viscous damper module
# damper_type=viscous
import openseespy.opensees as ops

damper_elements = []

def _global_axis_orient():
    return (1.0, 0.0, 0.0, 0.0, 1.0, 0.0)

# north_tower_girder_1 unit 1: selected=damper_viscous; input_C=7600.0; opensees_C=7600000.0; alpha=0.8; dir=1
# USER300 等价黏滞本构：F = C*max(abs(v),VFLOOR)^(alpha-1)*v
ops.uniaxialMaterial('User300Viscous', 9001, 7600000.0, 0.8, 0.0001)
ops.element('twoNodeLink', 9101, 36, 517, '-mat', 9001, '-dir', 1, '-orient', *_global_axis_orient())
damper_elements.append(9101)
# north_tower_girder_2 unit 1: selected=damper_viscous; input_C=7600.0; opensees_C=7600000.0; alpha=0.8; dir=1
# USER300 等价黏滞本构：F = C*max(abs(v),VFLOOR)^(alpha-1)*v
ops.uniaxialMaterial('User300Viscous', 9002, 7600000.0, 0.8, 0.0001)
ops.element('twoNodeLink', 9102, 36, 518, '-mat', 9002, '-dir', 1, '-orient', *_global_axis_orient())
damper_elements.append(9102)
# south_tower_girder_1 unit 1: selected=damper_viscous; input_C=7600.0; opensees_C=7600000.0; alpha=0.8; dir=1
# USER300 等价黏滞本构：F = C*max(abs(v),VFLOOR)^(alpha-1)*v
ops.uniaxialMaterial('User300Viscous', 9003, 7600000.0, 0.8, 0.0001)
ops.element('twoNodeLink', 9103, 107, 520, '-mat', 9003, '-dir', 1, '-orient', *_global_axis_orient())
damper_elements.append(9103)
# south_tower_girder_2 unit 1: selected=damper_viscous; input_C=7600.0; opensees_C=7600000.0; alpha=0.8; dir=1
# USER300 等价黏滞本构：F = C*max(abs(v),VFLOOR)^(alpha-1)*v
ops.uniaxialMaterial('User300Viscous', 9004, 7600000.0, 0.8, 0.0001)
ops.element('twoNodeLink', 9104, 107, 521, '-mat', 9004, '-dir', 1, '-orient', *_global_axis_orient())
damper_elements.append(9104)

# Gravity static preload module
# This establishes and freezes the initial state shared by modal and transient analyses.
opensees_system = 'UmfPack'
opensees_system_args = ()
if model_globals.get('gravity_preloaded'):
    pass
elif callable(getattr(model_module, 'apply_gravity_loads', None)):
    gravity_result = model_module.apply_gravity_loads(model_context)
    if isinstance(gravity_result, dict):
        model_context.update(gravity_result)
        model_globals.update(gravity_result)
    if callable(getattr(model_module, 'commit_gravity_state', None)):
        commit_result = model_module.commit_gravity_state(model_context)
        if isinstance(commit_result, dict):
            model_context.update(commit_result)
            model_globals.update(commit_result)
    else:
        ops.loadConst('-time', 0.0)
else:
    gravity_nodes = model_globals.get('gravity_load_nodes', model_globals.get('deck_nodes', []))
    if not gravity_nodes:
        gravity_nodes = ops.getNodeTags()
    if not gravity_nodes:
        raise RuntimeError("OpenSees gravity module requires model nodes")
    ops.timeSeries('Linear', 3001)
    ops.pattern('Plain', 3001, 3001)
    for node_tag in gravity_nodes:
        ops.load(int(node_tag), 0.0, -9.81, 0.0, 0.0, 0.0, 0.0)
    ops.constraints('Plain')
    ops.numberer('RCM')
    ops.system(opensees_system, *opensees_system_args)
    ops.test('NormDispIncr', 1.0e-8, 20)
    ops.algorithm('Newton')
    ops.integrator('LoadControl', 1.0 / 2)
    ops.analysis('Static')
    if ops.analyze(2) != 0:
        raise RuntimeError("OpenSees gravity static preload failed")
    ops.loadConst('-time', 0.0)

# 平均风与重力共同构成运营工况的静力平衡基线。
wind_mappings = model_globals.get('wind_load_mappings') or []
mean_wind_mappings = [mapping for mapping in wind_mappings if float(mapping.get('mean_force_N', 0.0)) != 0.0]
if mean_wind_mappings:
    ops.timeSeries('Constant', 3101)
    ops.pattern('Plain', 3101, 3101)
    for mapping in mean_wind_mappings:
        node_tag = int(mapping.get('fem_node_id', mapping.get('node_id')))
        dof = str(mapping.get('dof', 'FY')).upper()
        axis = {'FX': 0, 'FY': 1, 'FZ': 2}.get(dof)
        if axis is None:
            raise RuntimeError(f'OpenSees mean wind mapping dof must be FX, FY, or FZ: {dof}')
        load_vector = [0.0, 0.0, 0.0]
        load_vector[axis] = float(mapping['mean_force_N']) * float(mapping.get('scale', 1.0))
        ops.load(node_tag, load_vector[0], load_vector[1], load_vector[2], 0.0, 0.0, 0.0)
    ops.wipeAnalysis()
    ops.constraints('Plain')
    ops.numberer('RCM')
    ops.system(opensees_system, *opensees_system_args)
    ops.test('NormDispIncr', 1.0e-8, 20)
    ops.algorithm('Newton')
    ops.integrator('LoadControl', 1.0 / 2)
    ops.analysis('Static')
    if ops.analyze(2) != 0:
        raise RuntimeError("OpenSees mean wind static preload failed")
    ops.loadConst('-time', 0.0)

# Modal analysis module
# requested_modes = 20
modal_eigenvalues = ops.eigen(20)

# Earthquake load module
# load=earthquake, path=D:\momo\analysis_data\earthquake_inputs\earthquake_acceleration_record.txt, scale=1.0, dt=0.01, duration=40.0
eq_path = r"D:\momo\analysis_data\earthquake_inputs\earthquake_acceleration_record.txt"
if eq_path and eq_path != "None":
    ops.timeSeries('Path', 4101, '-filePath', eq_path, '-dt', 0.01, '-factor', 1.0 * 1.0)
else:
    ops.timeSeries('Constant', 4101, '-factor', 1.0 * 1.0)
ops.pattern('UniformExcitation', 4101, 1, '-accel', 4101)

# Transient analysis execution and postprocess handoff
from csv import writer
from pathlib import Path

analysis_dt = 0.01
analysis_duration = 40.0
response_nodes = (36, 107)
response_dof = 1
tower_base_nodes = (494, 495, 511, 512, 525, 528, 530, 532, 534, 536, 538, 540, 542, 544, 547, 550)
tower_base_element_node_map = ((835, 494), (836, 495), (837, 511), (838, 512))
tower_base_shear_inertia_element_mass_densities = ((831, 196250.0), (832, 196250.0), (833, 196250.0), (834, 196250.0), (835, 273750.0), (836, 273750.0), (837, 273750.0), (838, 273750.0))
tower_base_shear_dofs = (1, 2)
tower_base_moment_dofs = (5, 6)
use_control_volume_shear = True
damper_placement_pairs = ((36, 517, 1), (36, 518, 1), (107, 520, 1), (107, 521, 1))
diagnostic_nodes = ()
diagnostic_pairs = ()
damping_ratio = float(0.05)
opensees_system = 'UmfPack'
opensees_system_args = ()
rayleigh_frequency_a_hz = None
rayleigh_frequency_b_hz = None

ground_acceleration_scale = float(1.0) * float(1.0)
ground_acceleration_path = r"D:\momo\analysis_data\earthquake_inputs\earthquake_acceleration_record.txt"

if analysis_dt is None or analysis_duration is None:
    raise ValueError("OpenSees transient analysis requires load dt and duration")

def _load_ground_acceleration_values():
    if not ground_acceleration_path or ground_acceleration_path == "None":
        return []
    values = []
    with Path(ground_acceleration_path).open("r", encoding="utf-8-sig") as stream:
        for line_number, line in enumerate(stream, start=1):
            text = line.strip()
            if not text or text.startswith(("#", "!")):
                continue
            tokens = text.replace(",", " ").split()
            try:
                values.append(float(tokens[-1]) * ground_acceleration_scale)
            except (IndexError, ValueError) as exc:
                raise ValueError(f"Cannot parse ground acceleration at line {line_number}: {line.rstrip()}") from exc
    return values

def _integrate_ground_motion(accelerations):
    velocities = [0.0] * len(accelerations)
    displacements = [0.0] * len(accelerations)
    for index in range(1, len(accelerations)):
        velocities[index] = velocities[index - 1] + 0.5 * (accelerations[index - 1] + accelerations[index]) * analysis_dt
        displacements[index] = displacements[index - 1] + 0.5 * (velocities[index - 1] + velocities[index]) * analysis_dt
    return velocities, displacements

ground_acceleration_values = _load_ground_acceleration_values()
ground_velocity_values, ground_displacement_values = _integrate_ground_motion(ground_acceleration_values)

def _signed_envelope(values):
    return max(values, key=lambda value: abs(value), default=0.0)

def _element_envelope(response):
    return _signed_envelope(
        [
            float(value)
            for element in damper_elements
            for value in ops.eleResponse(element, response)
        ]
    )

def _response_displacements():
    return [float(ops.nodeDisp(node, response_dof)) for node in response_nodes]

def _node_accel_value(node, dof):
    try:
        return float(ops.nodeAccel(node, dof))
    except Exception:
        return 0.0

def _relative_to_baseline(values, baseline):
    return [
        float(value) - float(baseline[index])
        for index, value in enumerate(values)
    ]

def _standard_response_displacements():
    return _relative_to_baseline(_response_displacements(), baseline_response_displacements)

def _response_displacement_increments():
    return _relative_to_baseline(_response_displacements(), baseline_response_displacements)

def _placement_strokes(pairs, dof):
    strokes = []
    for pair in pairs:
        if len(pair) == 2:
            node_i, node_j = pair
            pair_dof = dof
        else:
            node_i, node_j, pair_dof = pair
        try:
            strokes.append(float(ops.nodeDisp(int(node_j), int(pair_dof))) - float(ops.nodeDisp(int(node_i), int(pair_dof))))
        except Exception:
            strokes.append(0.0)
    return strokes

def _placement_stroke_envelope(pairs, dof):
    strokes = _placement_strokes(pairs, dof)
    if strokes:
        return _signed_envelope(strokes)
    return _element_envelope('basicDeformation')

def _pair_relative_disp(pair):
    node_i, node_j, pair_dof = pair
    try:
        return float(ops.nodeDisp(int(node_j), int(pair_dof))) - float(ops.nodeDisp(int(node_i), int(pair_dof)))
    except Exception:
        return 0.0

def _pair_relative_velocity(pair):
    node_i, node_j, pair_dof = pair
    try:
        return float(ops.nodeVel(int(node_j), int(pair_dof))) - float(ops.nodeVel(int(node_i), int(pair_dof)))
    except Exception:
        return 0.0

def _damper_element_force(index):
    if index >= len(damper_elements):
        return 0.0
    try:
        return _signed_envelope([float(value) for value in ops.eleResponse(damper_elements[index], 'basicForce')])
    except Exception:
        return 0.0

def _six_values(values):
    result = [float(value) for value in values]
    while len(result) < 6:
        result.append(0.0)
    return result[:6]

def _absolute_node_values(node):
    values = _six_values(ops.nodeDisp(int(node)))
    ground_displacement, _ground_velocity, _current_ground_acceleration = _ground_motion_values()
    if 1 in (1, 2, 3):
        values[1 - 1] += ground_displacement
    return values

def _node_values(node):
    return _six_values(ops.nodeDisp(int(node))) + _six_values(ops.nodeVel(int(node))) + _six_values(ops.nodeAccel(int(node)))

def _node_reaction_totals(nodes, dofs):
    node_reactions = {}
    for node in nodes:
        try:
            node_reactions[int(node)] = ops.nodeReaction(int(node))
        except Exception:
            node_reactions[int(node)] = []
    totals = []
    for dof in dofs:
        index = dof - 1
        total = 0.0
        for values in node_reactions.values():
            if index < len(values):
                total += float(values[index])
        totals.append(total)
    return totals

def _node_coords(node):
    try:
        values = [float(value) for value in ops.nodeCoord(int(node))]
    except Exception:
        values = []
    while len(values) < 3:
        values.append(0.0)
    return values[:3]

def _node_absolute_accel(node, dof, ground_acceleration):
    value = _node_accel_value(int(node), int(dof))
    if int(dof) == 1:
        value += ground_acceleration
    return value

def _tower_base_shear_inertia_values(ground_acceleration):
    # 地震塔底剪力需求采用控制体平衡：下塔柱质量惯性需计入剪力。
    mass_density_by_element = {
        int(element): float(mass_density)
        for element, mass_density in tower_base_shear_inertia_element_mass_densities
    }
    inertia_values = []
    for dof in tower_base_shear_dofs:
        total = 0.0
        for element, mass_density in mass_density_by_element.items():
            try:
                nodes = [int(value) for value in ops.eleNodes(int(element))]
                coords = [_node_coords(nodes[0]), _node_coords(nodes[1])]
            except Exception:
                continue
            length = sum((coords[1][index] - coords[0][index]) ** 2 for index in range(3)) ** 0.5
            accel_i = _node_absolute_accel(nodes[0], int(dof), ground_acceleration)
            accel_j = _node_absolute_accel(nodes[1], int(dof), ground_acceleration)
            total += mass_density * length * (accel_i + accel_j) / 2.0
        inertia_values.append(total)
    return inertia_values

def _cross_product(left, right):
    return [
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    ]

def _tower_base_reaction_resultants():
    node_reactions = {}
    for node in tower_base_nodes:
        try:
            node_reactions[int(node)] = _six_values(ops.nodeReaction(int(node)))
        except Exception:
            node_reactions[int(node)] = [0.0] * 6
    shear_values = []
    for dof in tower_base_shear_dofs:
        index = int(dof) - 1
        shear_values.append(
            sum(float(values[index]) for values in node_reactions.values() if index < len(values))
        )
    total_moment = [0.0, 0.0, 0.0]
    for node, values in node_reactions.items():
        moment = _cross_product(_node_coords(node), values[:3])
        for index in range(3):
            total_moment[index] += moment[index]
    moment_values = []
    for dof in tower_base_moment_dofs:
        index = int(dof) - 4
        if 0 <= index < 3:
            moment_values.append(total_moment[index])
    if not moment_values:
        moment_values = total_moment
    return shear_values, moment_values

def _element_end_offset(element, node):
    try:
        nodes = [int(value) for value in ops.eleNodes(int(element))]
    except Exception:
        nodes = []
    if int(node) in nodes:
        return nodes.index(int(node)) * 6
    return max(len(nodes) - 1, 1) * 6

def _element_global_force_values(element, node):
    try:
        values = [float(value) for value in ops.eleResponse(int(element), 'globalForce')]
    except Exception:
        try:
            values = [float(value) for value in ops.eleResponse(int(element), 'force')]
        except Exception:
            values = []
    offset = _element_end_offset(element, node)
    result = values[offset:offset + 6]
    while len(result) < 6:
        result.append(0.0)
    return result[:6]

def _tower_base_section_resultants():
    if not tower_base_element_node_map:
        return _tower_base_reaction_resultants()
    section_values = [
        _element_global_force_values(element, node)
        for element, node in tower_base_element_node_map
    ]
    # 与 ANSYS 截面合力口径一致：同一 DOF 先跨 835-838 塔脚求和，再转成截面内力正号。
    shear_values = [
        -sum(values[int(dof) - 1] for values in section_values if len(values) > int(dof) - 1)
        for dof in tower_base_shear_dofs
        if 1 <= int(dof) <= 3
    ]
    moment_values = [
        -sum(values[int(dof) - 1] for values in section_values if len(values) > int(dof) - 1)
        for dof in tower_base_moment_dofs
        if 4 <= int(dof) <= 6
    ]
    if not shear_values:
        shear_values, _fallback_moments = _tower_base_reaction_resultants()
    if not moment_values:
        _fallback_shears, moment_values = _tower_base_reaction_resultants()
    return shear_values, moment_values

def _ground_motion_values():
    time = float(ops.getTime())
    if ground_acceleration_values:
        index = min(max(int(round(time / analysis_dt)), 0), len(ground_acceleration_values) - 1)
        return (
            ground_displacement_values[index],
            ground_velocity_values[index],
            ground_acceleration_values[index],
        )
    return (
        0.5 * ground_acceleration_scale * time * time,
        ground_acceleration_scale * time,
        ground_acceleration_scale,
    )

def _rayleigh_frequencies():
    configured = []
    for value in (rayleigh_frequency_a_hz, rayleigh_frequency_b_hz):
        if value is not None:
            configured.append(float(value))
    if len(configured) >= 2:
        return configured[0], configured[1]
    if len(ops.getNodeTags()) <= 20:
        return 0.055317, 0.100449
    eigenvalues = []
    for args in ((2,), ('-fullGenLapack', 2), (1,), ('-fullGenLapack', 1)):
        try:
            eigenvalues = list(ops.eigen(*args))
        except Exception:
            continue
        if eigenvalues:
            break
    frequencies = [
        (float(value) ** 0.5) / (2.0 * 3.141592653589793)
        for value in eigenvalues
        if float(value) > 1.0e-12
    ]
    if len(frequencies) >= 2:
        return frequencies[0], frequencies[1]
    if len(frequencies) == 1:
        return frequencies[0], frequencies[0]
    return 0.055317, 0.100449

def _apply_rayleigh_damping():
    if damping_ratio <= 0.0:
        return None
    freq_a, freq_b = _rayleigh_frequencies()
    if freq_a <= 0.0 or freq_b <= 0.0:
        return None
    alpha_m = 4.0 * 3.141592653589793 * damping_ratio * freq_a * freq_b / (freq_a + freq_b)
    beta_k = damping_ratio / (3.141592653589793 * (freq_a + freq_b))
    ops.rayleigh(alpha_m, beta_k, 0.0, 0.0)
    return {
        'freq_a_hz': freq_a,
        'freq_b_hz': freq_b,
        'alpha_m': alpha_m,
        'beta_k': beta_k,
    }

def _write_shear_component_header(output):
    columns = ['time']
    for dof in tower_base_shear_dofs:
        dof_label = 'DOF' + str(int(dof))
        columns.extend([
            'section_raw_' + dof_label,
            'section_baseline_' + dof_label,
            'section_relative_' + dof_label,
            'inertia_raw_' + dof_label,
            'inertia_baseline_' + dof_label,
            'inertia_relative_' + dof_label,
            'dynamic_' + dof_label,
        ])
    columns.append('tower_base_shear')
    output.writerow(columns)

def _write_shear_component_row(
    output,
    tower_base_shears,
    tower_base_shear_inertia_values,
    relative_tower_base_shears,
    relative_tower_base_shear_inertia_values,
    dynamic_tower_base_shears,
    tower_base_shear,
):
    row = [float(ops.getTime())]
    for index, _dof in enumerate(tower_base_shear_dofs):
        row.extend([
            tower_base_shears[index] if index < len(tower_base_shears) else 0.0,
            baseline_tower_base_shears[index] if index < len(baseline_tower_base_shears) else 0.0,
            relative_tower_base_shears[index] if index < len(relative_tower_base_shears) else 0.0,
            tower_base_shear_inertia_values[index] if index < len(tower_base_shear_inertia_values) else 0.0,
            baseline_tower_base_shear_inertia_values[index] if index < len(baseline_tower_base_shear_inertia_values) else 0.0,
            relative_tower_base_shear_inertia_values[index] if index < len(relative_tower_base_shear_inertia_values) else 0.0,
            dynamic_tower_base_shears[index] if index < len(dynamic_tower_base_shears) else 0.0,
        ])
    row.append(tower_base_shear)
    output.writerow(row)

def _write_response_row(output, shear_components_output=None):
    ground_displacement, ground_velocity, current_ground_acceleration = _ground_motion_values()
    ops.reactions('-dynamic', '-rayleigh')
    tower_base_shears, tower_base_moments = _tower_base_section_resultants()
    tower_base_shear_inertia_values = _tower_base_shear_inertia_values(current_ground_acceleration)
    relative_tower_base_shears = _relative_to_baseline(tower_base_shears, baseline_tower_base_shears)
    relative_tower_base_shear_inertia_values = _relative_to_baseline(
        tower_base_shear_inertia_values,
        baseline_tower_base_shear_inertia_values,
    )
    if use_control_volume_shear:
        dynamic_tower_base_shears = [
            # OpenSees 截面力已转为 ANSYS 截面内力正号；地震控制体惯性按平衡方向扣除。
            relative_tower_base_shears[index] - relative_tower_base_shear_inertia_values[index]
            for index in range(min(len(relative_tower_base_shears), len(relative_tower_base_shear_inertia_values)))
        ]
        if not dynamic_tower_base_shears:
            dynamic_tower_base_shears = relative_tower_base_shears
    else:
        # 风/车运营工况与 ANSYS 对齐为塔底截面内力增量，不套用地震控制体剪力需求。
        dynamic_tower_base_shears = relative_tower_base_shears
    tower_base_shear = _signed_envelope(dynamic_tower_base_shears)
    if shear_components_output is not None:
        _write_shear_component_row(
            shear_components_output,
            tower_base_shears,
            tower_base_shear_inertia_values,
            relative_tower_base_shears,
            relative_tower_base_shear_inertia_values,
            dynamic_tower_base_shears,
            tower_base_shear,
        )
    output.writerow(
        [
            float(ops.getTime()),
            _signed_envelope(_standard_response_displacements()),
            _signed_envelope(_response_displacement_increments()),
            _signed_envelope(_response_displacements()),
            _signed_envelope([_node_accel_value(node, response_dof) for node in response_nodes]),
            _signed_envelope(_relative_to_baseline(tower_base_moments, baseline_tower_base_moments)),
            tower_base_shear,
            _element_envelope('basicForce'),
            _signed_envelope(_relative_to_baseline(_placement_strokes(damper_placement_pairs, response_dof), baseline_placement_strokes))
            if damper_placement_pairs
            else _element_envelope('basicDeformation'),
            ground_displacement,
            ground_velocity,
            current_ground_acceleration,
        ]
    )

def _write_diagnostic_node_header(output):
    columns = ['time']
    for node in diagnostic_nodes:
        for prefix in ('UX', 'UY', 'UZ', 'ROTX', 'ROTY', 'ROTZ', 'VX', 'VY', 'VZ', 'VRX', 'VRY', 'VRZ', 'AX', 'AY', 'AZ', 'ARX', 'ARY', 'ARZ'):
            columns.append('Node' + str(int(node)) + '_' + prefix)
        for prefix in ('ABS_UX', 'ABS_UY', 'ABS_UZ', 'ABS_ROTX', 'ABS_ROTY', 'ABS_ROTZ'):
            columns.append('Node' + str(int(node)) + '_' + prefix)
    output.writerow(columns)

def _write_diagnostic_node_row(output):
    row = [float(ops.getTime())]
    for node in diagnostic_nodes:
        row.extend(_node_values(node))
        row.extend(_absolute_node_values(node))
    output.writerow(row)

def _write_diagnostic_pair_header(output):
    columns = ['time']
    for index, pair in enumerate(diagnostic_pairs, start=1):
        node_i, node_j, pair_dof = pair
        columns.append(
            'Pair'
            + str(index)
            + '_'
            + str(int(node_i))
            + '_'
            + str(int(node_j))
            + '_DOF'
            + str(int(pair_dof))
            + '_rel_disp'
        )
    output.writerow(columns)

def _write_diagnostic_pair_row(output, baseline_pair_strokes):
    row = [float(ops.getTime())]
    for index, pair in enumerate(diagnostic_pairs):
        row.append(_pair_relative_disp(pair) - baseline_pair_strokes[index])
    output.writerow(row)

def _write_tower_girder_relative_header(output):
    columns = ['time']
    for index, pair in enumerate(damper_placement_pairs, start=1):
        node_i, node_j, pair_dof = pair
        prefix = (
            'Pair'
            + str(index)
            + '_'
            + str(int(node_i))
            + '_'
            + str(int(node_j))
            + '_DOF'
            + str(int(pair_dof))
        )
        columns.extend([prefix + '_rel_disp', prefix + '_rel_vel', prefix + '_damper_force'])
    output.writerow(columns)

def _write_tower_girder_relative_row(output, baseline_placement_strokes):
    row = [float(ops.getTime())]
    for index, pair in enumerate(damper_placement_pairs):
        row.append(_pair_relative_disp(pair) - baseline_placement_strokes[index])
        row.append(_pair_relative_velocity(pair))
        row.append(_damper_element_force(index))
    output.writerow(row)

transient_algorithm_fallback_counts = {}

def _configure_default_transient_algorithm():
    ops.test('NormDispIncr', 1.0e-8, 20)
    ops.algorithm('Newton')

def _try_transient_algorithm(name, test_args, algorithm_args, dt):
    ops.test(*test_args)
    ops.algorithm(*algorithm_args)
    if ops.analyze(1, dt) == 0:
        transient_algorithm_fallback_counts[name] = transient_algorithm_fallback_counts.get(name, 0) + 1
        _configure_default_transient_algorithm()
        return True
    return False

def _analyze_transient_step(dt):
    if ops.analyze(1, dt) == 0:
        return
    attempts = (
        ('newton_60', ('NormDispIncr', 1.0e-8, 60), ('Newton',)),
        ('newton_line_search', ('NormDispIncr', 1.0e-7, 80), ('NewtonLineSearch',)),
        ('modified_newton', ('NormDispIncr', 1.0e-7, 100), ('ModifiedNewton',)),
    )
    for name, test_args, algorithm_args in attempts:
        if _try_transient_algorithm(name, test_args, algorithm_args, dt):
            return
    _configure_default_transient_algorithm()
    raise RuntimeError("OpenSees transient analysis failed")

ops.wipeAnalysis()
ops.constraints('Plain')
ops.numberer('RCM')
ops.system(opensees_system, *opensees_system_args)
_configure_default_transient_algorithm()
ops.integrator('Newmark', 0.5, 0.25)
rayleigh_damping = _apply_rayleigh_damping()
ops.analysis('Transient')
ops.reactions('-dynamic', '-rayleigh')
baseline_response_displacements = _response_displacements()
baseline_placement_strokes = _placement_strokes(damper_placement_pairs, response_dof)
baseline_diagnostic_pair_strokes = [_pair_relative_disp(pair) for pair in diagnostic_pairs]
baseline_tower_base_shears, baseline_tower_base_moments = _tower_base_section_resultants()
baseline_tower_base_shear_inertia_values = _tower_base_shear_inertia_values(_ground_motion_values()[2])
analysis_steps = int(round(analysis_duration / analysis_dt))
if analysis_steps <= 0:
    raise ValueError("OpenSees transient analysis requires at least one time step")
timeseries_path = Path(__file__).with_name('timeseries.csv')
shear_components_path = Path(__file__).with_name('tower_base_shear_components.csv')
tower_girder_relative_path = Path(__file__).with_name('tower_girder_relative_response.csv')
diagnostics_nodes_handle = None
diagnostics_pairs_handle = None
tower_girder_relative_handle = None
try:
    diagnostics_nodes_writer = None
    diagnostics_pairs_writer = None
    tower_girder_relative_writer = None
    if diagnostic_nodes:
        diagnostics_nodes_handle = Path(__file__).with_name('diagnostics_nodes.csv').open('w', encoding='utf-8', newline='')
        diagnostics_nodes_writer = writer(diagnostics_nodes_handle)
        _write_diagnostic_node_header(diagnostics_nodes_writer)
    if diagnostic_pairs:
        diagnostics_pairs_handle = Path(__file__).with_name('diagnostics_pairs.csv').open('w', encoding='utf-8', newline='')
        diagnostics_pairs_writer = writer(diagnostics_pairs_handle)
        _write_diagnostic_pair_header(diagnostics_pairs_writer)
    if damper_placement_pairs:
        tower_girder_relative_handle = tower_girder_relative_path.open('w', encoding='utf-8', newline='')
        tower_girder_relative_writer = writer(tower_girder_relative_handle)
        _write_tower_girder_relative_header(tower_girder_relative_writer)
    with timeseries_path.open('w', encoding='utf-8', newline='') as handle:
        timeseries_writer = writer(handle)
        timeseries_writer.writerow([
            'time',
            'displacement',
            'displacement_increment',
            'absolute_displacement',
            'acceleration',
            'tower_base_moment',
            'tower_base_shear',
            'damper_force',
            'damper_stroke',
            'ground_displacement',
            'ground_velocity',
            'ground_acceleration',
        ])
        with shear_components_path.open('w', encoding='utf-8', newline='') as shear_components_handle:
            shear_components_writer = writer(shear_components_handle)
            _write_shear_component_header(shear_components_writer)
            _write_response_row(timeseries_writer, shear_components_writer)
            if diagnostics_nodes_writer is not None:
                _write_diagnostic_node_row(diagnostics_nodes_writer)
            if diagnostics_pairs_writer is not None:
                _write_diagnostic_pair_row(diagnostics_pairs_writer, baseline_diagnostic_pair_strokes)
            if tower_girder_relative_writer is not None:
                _write_tower_girder_relative_row(tower_girder_relative_writer, baseline_placement_strokes)
            for _ in range(analysis_steps):
                _analyze_transient_step(analysis_dt)
                _write_response_row(timeseries_writer, shear_components_writer)
                if diagnostics_nodes_writer is not None:
                    _write_diagnostic_node_row(diagnostics_nodes_writer)
                if diagnostics_pairs_writer is not None:
                    _write_diagnostic_pair_row(diagnostics_pairs_writer, baseline_diagnostic_pair_strokes)
                if tower_girder_relative_writer is not None:
                    _write_tower_girder_relative_row(tower_girder_relative_writer, baseline_placement_strokes)
finally:
    if diagnostics_nodes_handle is not None:
        diagnostics_nodes_handle.close()
    if diagnostics_pairs_handle is not None:
        diagnostics_pairs_handle.close()
    if tower_girder_relative_handle is not None:
        tower_girder_relative_handle.close()
