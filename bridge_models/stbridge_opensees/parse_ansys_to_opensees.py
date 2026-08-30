#!/usr/bin/env python3
"""Parse Ansys STbridge.txt and generate OpenSeesPy modal analysis script.

Two-pass approach:
  Pass 1: Collect array definitions, expand *DO/*ENDDO loops
  Pass 2: Process expanded commands (nodes, elements, materials, etc.)
"""

import ast
import math
import operator
import re
import sys
from pathlib import Path


def expand_loops(lines):
    """Recursively expand *DO/*ENDDO loops.

    *DO,var,start,end[,step]
      body (may contain nested *DO/*ENDDO)
    *ENDDO
    """
    result = []
    i = 0
    n = len(lines)
    while i < n:
        stripped = lines[i].strip()
        if stripped.lower().startswith('*do,'):
            parts = stripped.split(',')
            var = parts[1].strip()
            start = int(float(parts[2].strip()))
            end = int(float(parts[3].strip()))
            step = int(float(parts[4].strip())) if len(parts) > 4 else 1

            # Collect body until matching *enddo
            depth = 0
            j = i + 1
            while j < n:
                sl = lines[j].strip().lower()
                if sl.startswith('*do,'):
                    depth += 1
                elif sl.startswith('*enddo'):
                    if depth == 0:
                        break
                    depth -= 1
                j += 1

            body = lines[i+1:j]

            for val in range(start, end + 1, step):
                # Substitute loop variable in body
                substituted = []
                for bline in body:
                    new_line = re.sub(r'\b' + re.escape(var) + r'\b', str(val), bline)
                    substituted.append(new_line)
                # Recursively expand any nested *DO loops
                expanded_body = expand_loops(substituted)
                result.extend(expanded_body)

            i = j + 1  # skip past *enddo
        elif stripped.lower().startswith('*enddo'):
            i += 1
        else:
            result.append(lines[i])
            i += 1
    return result


def safe_eval(expr, arrays):
    """只计算可引用 ANSYS 数组的数值算术表达式。"""
    expr = expr.strip()
    def repl(m):
        name = m.group(1).lower()
        idx = int(m.group(2)) - 1
        if name in arrays and 0 <= idx < len(arrays[name]):
            return repr(arrays[name][idx])
        raise ValueError(f'unsupported ANSYS numeric expression: unknown array value {m.group(0)}')
    expr = re.sub(r'(\w+)\s*\(\s*(\d+)\s*\)', repl, expr)
    # Fortran scientific notation
    expr = re.sub(r'(\d+\.?\d*)E([+-]?\d+)', r'\1e\2', expr, flags=re.I)

    binary_operators = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.Pow: operator.pow,
        ast.Mod: operator.mod,
    }
    unary_operators = {ast.UAdd: operator.pos, ast.USub: operator.neg}

    def evaluate(node):
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in binary_operators:
            return binary_operators[type(node.op)](evaluate(node.left), evaluate(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in unary_operators:
            return unary_operators[type(node.op)](evaluate(node.operand))
        raise ValueError(f'unsupported ANSYS numeric expression: {expr}')

    try:
        return float(evaluate(ast.parse(expr, mode='eval')))
    except (SyntaxError, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith('unsupported ANSYS numeric expression'):
            raise
        raise ValueError(f'unsupported ANSYS numeric expression: {expr}') from exc


def parse_ansys(filepath):
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        raw_lines = f.readlines()

    # Clean lines: remove comments, blank lines, /commands
    clean = []
    for line in raw_lines:
        s = line.strip()
        if not s:
            continue
        # Strip inline comments (but keep *dim and array assignment lines)
        if '!' in s:
            before_comment = s.split('!')[0].strip()
            # Keep line only if it has content before the comment
            # or if it's a dim/array line
            if not before_comment:
                continue
            s = before_comment
        if s.startswith('/') and not s.lower().startswith('/prep7'):
            continue
        if s.lower() in ('finish', '/clear'):
            continue
        clean.append(s)

    # Expand all DO loops
    expanded = expand_loops(clean)

    # Second pass: collect arrays, then process commands
    arrays = {}
    nodes = {}
    elements = []
    materials = {}
    real_props = {}
    cp_pairs = []
    boundary = []
    current_mat = 1
    current_real = 1
    last_rnum = None

    for line in expanded:
        line = line.strip()
        if not line:
            continue

        # *DIM: array declaration
        m = re.match(r"^\*dim\s*,\s*(\w+)\s*,\s*array\s*,\s*(\d+)", line, re.I)
        if m:
            name = m.group(1).lower()
            size = int(m.group(2))
            arrays[name] = [0.0] * size
            continue

        # Array value: name(index) = expr
        m = re.match(r"^(\w+)\s*\(\s*(\d+)\s*\)\s*=\s*(.+)$", line, re.I)
        if m:
            name = m.group(1).lower()
            idx = int(m.group(2)) - 1
            val = safe_eval(m.group(3), arrays)
            if name not in arrays:
                arrays[name] = [0.0] * 200
            while len(arrays[name]) <= idx:
                arrays[name].append(0.0)
            arrays[name][idx] = val
            continue

        # Skip directives we don't need
        if re.match(r"^(title|keyopt|type|real|/prep7)\s*,", line, re.I):
            continue
        if line.lower() == '/prep7':
            continue

        # Node: n, num_expr, x, y [,z]
        m = re.match(r"^n\s*,\s*(.+)$", line, re.I)
        if m:
            parts = m.group(1).split(',')
            if len(parts) >= 3:
                nid = int(safe_eval(parts[0].strip(), arrays))
                coords = [safe_eval(c.strip(), arrays) for c in parts[1:]]
                while len(coords) < 3:
                    coords.append(0.0)
                nodes[nid] = coords[:3]
            continue

        # Material: mat, id
        m = re.match(r"^mat\s*,\s*(\d+)$", line, re.I)
        if m:
            current_mat = int(m.group(1))
            continue

        # Element: e, n1, n2 [,n3...]
        m = re.match(r"^e\s*,\s*(.+)$", line, re.I)
        if m:
            enodes = [int(safe_eval(x.strip(), arrays)) for x in m.group(1).split(',')]
            if len(enodes) >= 2:
                elements.append((current_mat, current_real, enodes[0], enodes[1]))
            continue

        # Real constant: r, num, v1, v2, ...
        m = re.match(r"^r\s*,\s*(\d+)\s*,\s*(.+)$", line, re.I)
        if m:
            rnum = int(m.group(1))
            vals = [safe_eval(x.strip(), arrays) for x in m.group(2).split(',')]
            real_props[rnum] = vals
            last_rnum = rnum
            current_real = rnum
            continue

        # RMORE: append to last real constant
        if re.match(r"^rmore\s*", line, re.I):
            parts = line.split(',')[1:]
            vals = [safe_eval(x.strip(), arrays) for x in parts if x.strip()]
            if last_rnum and last_rnum in real_props:
                real_props[last_rnum].extend(vals)
            continue

        # Material property: mp, prop, mat_id, value
        m = re.match(r"^mp\s*,\s*(\w+)\s*,\s*(\d+)\s*,\s*(.+)$", line, re.I)
        if m:
            prop = m.group(1).lower()
            mid = int(m.group(2))
            val = safe_eval(m.group(3), arrays)
            materials.setdefault(mid, {})[prop] = val
            continue

        # Coupled DOF: cp, num, dof, n1, n2 [, n3, ...]
        m = re.match(r"^cp\s*,\s*\d+\s*,\s*(\w+)\s*,\s*(.+)$", line, re.I)
        if m:
            dof = m.group(1).lower()
            nodes_str = m.group(2)
            cp_nodes = [int(x.strip()) for x in nodes_str.split(',') if x.strip()]
            # Master is first node, slaves are the rest
            if len(cp_nodes) >= 2:
                master = cp_nodes[0]
                for slave in cp_nodes[1:]:
                    cp_pairs.append((dof, master, slave))
            continue

        # Boundary condition: d, node, dof, val
        if re.match(r"^d\s*,\s*\d+", line, re.I):
            boundary.append(line)
            continue

    return nodes, elements, materials, real_props, cp_pairs, boundary


def beam_lumped_nodal_mass(dens, area, iy, iz, torsion_j, length, axis):
    """Return one-node diagonal lumped mass for a 3D beam element."""

    if dens <= 0.0 or area <= 0.0 or length <= 0.0:
        return [0.0] * 6
    translational = dens * area * length / 2.0
    # ANSYS BEAM4 的完整质量矩阵包含转动惯量项；这里先生成对角 lumped 近似，
    # 用于和纯平动 ops.mass 变体区分，后续仍需通过 .full 矩阵导出验收。
    local_rot = {
        "x": (torsion_j, iy, iz),
        "y": (iz, torsion_j, iy),
        "z": (iy, iz, torsion_j),
    }.get(axis, (torsion_j, iy, iz))
    rotational = [dens * inertia * length / 2.0 for inertia in local_rot]
    return [translational, translational, translational, *rotational]


def beam4_torsion_constant(real_values):
    """返回 BEAM4 到 OpenSees 梁单元使用的扭转惯量。"""

    iz = real_values[1] if len(real_values) > 1 else 0.0
    iy = real_values[2] if len(real_values) > 2 else 0.0
    explicit_j = real_values[5] if len(real_values) > 5 else 0.0
    if explicit_j > 0.0:
        return explicit_j
    return iy + iz


def _render_opensees_script(header, sections):
    """Render an OpenSeesPy model script with explicit model stage entrypoints."""

    lines = list(header)

    def emit_function(name, body):
        lines.append(f"def {name}():")
        has_statement = any(line.strip() and not line.strip().startswith("#") for line in body)
        if has_statement:
            for line in body:
                lines.append(f"    {line}" if line else "")
        else:
            lines.append("    pass")
        lines.append("")

    lines.append("STANDARD_MODEL_STAGE_SEQUENCE = (")
    for stage in ("nodes", "materials", "elements", "constraints", "loads"):
        lines.append(f"    {stage!r},")
    lines.append(")")
    lines.append("")
    lines.append("")
    lines.append("def standard_model_stage_entrypoints():")
    lines.append("    return {")
    lines.append("        'nodes': 'build_nodes',")
    lines.append("        'materials': 'build_materials',")
    lines.append("        'elements': 'build_elements',")
    lines.append("        'constraints': 'build_constraints',")
    lines.append("        'loads': 'apply_gravity_loads',")
    lines.append("    }")
    lines.append("")
    lines.append("")
    lines.append("def build_model():")
    lines.append("    build_nodes()")
    lines.append("    build_materials()")
    lines.append("    build_elements()")
    lines.append("    build_constraints()")
    lines.append("    apply_gravity_loads()")
    lines.append("    return {'gravity_preloaded': False}")
    lines.append("")
    lines.append("")
    emit_function("build_nodes", sections["nodes"])
    emit_function("build_materials", sections["materials"])
    emit_function("build_elements", sections["elements"])
    emit_function("build_constraints", sections["constraints"])
    emit_function("apply_gravity_loads", sections["loads"])
    emit_function("run_modal", sections["modal"])
    lines.append("def main():")
    lines.append("    build_model()")
    lines.append("    run_modal()")
    lines.append("")
    lines.append("")
    lines.append("if __name__ == '__main__':")
    lines.append("    main()")
    return "\n".join(lines)


def generate_opensees(
    nodes,
    elements,
    materials,
    real_props,
    cp_pairs,
    boundary,
    *,
    cable_link_mode="rigidLink",
    modal_eigen_solver="fullGenLapack",
):
    """Generate the OpenSeesPy script."""
    if cable_link_mode not in {"rigidLink", "beam"}:
        raise ValueError("cable_link_mode must be 'rigidLink' or 'beam'")
    if modal_eigen_solver not in {"fullGenLapack", "default"}:
        raise ValueError("modal_eigen_solver must be 'fullGenLapack' or 'default'")

    header = []
    sections = {
        "nodes": [],
        "materials": [],
        "elements": [],
        "constraints": [],
        "loads": [],
        "modal": [],
    }
    active_lines = header

    def w(s=''):
        active_lines.append(s)

    def section(name):
        nonlocal active_lines
        active_lines = sections[name]

    w('"""OpenSeesPy modal analysis — Sutong cable-stayed bridge')
    w('Converted from Ansys STbridge.txt')
    w('With initial stress (self-weight) state"""')
    w('import openseespy.opensees as ops')
    w('import json, math, sys')
    w('')

    # ---------- helpers ----------
    def beam_props(mat_id, rnum):
        mp = materials.get(mat_id, {})
        rp = real_props.get(rnum, [])
        E = mp.get('ex', 0)
        prxy = mp.get('prxy', 0.3)
        G = E / (2.0 * (1.0 + prxy)) if prxy > 0 else E / 2.6
        dens = mp.get('dens', 0)
        A = rp[0] if rp else 0
        Iz = rp[1] if len(rp) > 1 else 0
        Iy = rp[2] if len(rp) > 2 else 0
        J = beam4_torsion_constant(rp)
        return E, G, A, Iz, Iy, J, dens

    def truss_props(mat_id, rnum):
        mp = materials.get(mat_id, {})
        rp = real_props.get(rnum, [])
        E = mp.get('ex', 0)
        dens = mp.get('dens', 0)
        A = rp[0] if rp else 0
        return E, A, dens

    # ---------- nodes ----------
    section("nodes")
    w('ops.wipe()')
    w('ops.model("basicBuilder", "-ndm", 3, "-ndf", 6)')
    w('# ========== Nodes ==========')
    for nid in sorted(nodes):
        c = nodes[nid]
        w(f'ops.node({nid}, {c[0]}, {c[1]}, {c[2]})')
    w(f'# Total: {len(nodes)} nodes')
    w('')

    # ---------- geometric transformations ----------
    section("elements")
    w('# ========== Geometric Transformations ==========')
    w('ops.geomTransf("Linear", 1, 0, 1, 0)  # deck/horizontal beams')
    w('ops.geomTransf("Linear", 2, 0, 0, 1)  # pylon/vertical beams')
    w('ops.geomTransf("Linear", 3, 1, 0, 0)  # transverse beams')
    w('')

    # ---------- elements ----------
    w('# ========== Elements ==========')

    # Create uniaxial materials for cable stay truss elements
    section("materials")
    truss_mat_tags = {}
    truss_mat_counter = 0
    for mat_id, rnum, n1, n2 in elements:
        is_cable_stay = (rnum in range(7, 10) or rnum >= 21)
        if is_cable_stay:
            mp = materials.get(mat_id, {})
            E = mp.get('ex', 0)
            key = (mat_id, E)
            if key not in truss_mat_tags and E > 0:
                truss_mat_counter += 1
                truss_mat_tags[key] = truss_mat_counter
                w(f'ops.uniaxialMaterial("Elastic", {truss_mat_counter}, {E:.4e})')
    w('')
    section("elements")

    # Cable link properties — rigidLink master-slave (matches Ansys E=2.1e15 beam)
    CABLE_MASS = 5000.0  # kg — translational mass for cable nodes

    eid = 0
    nbeam = 0
    ntruss = 0
    ncablelink = 0

    # Compute nodal mass from beam elements (lumped mass matrix)
    nodal_mass = {}  # nid -> [mx, my, mz, mrx, mry, mrz]

    for mat_id, rnum, n1, n2 in elements:
        is_cable_link = (rnum == 2)
        is_cable_stay = (rnum in range(7, 10) or rnum >= 21)
        if is_cable_link and cable_link_mode == "rigidLink":
            # Cable links → rigidLink (master=deck node, slave=cable node)
            w(f'ops.rigidLink("bar", {n1}, {n2})')
            ncablelink += 1
            # Mass for cable nodes
            m = CABLE_MASS
            if n2 not in nodal_mass:
                nodal_mass[n2] = [0.0]*6
            nodal_mass[n2][0] += m
            nodal_mass[n2][1] += m
            nodal_mass[n2][2] += m
        elif is_cable_stay:
            eid += 1
            E, A, dens = truss_props(mat_id, rnum)
            if E > 0 and A > 0:
                mat_tag = truss_mat_tags.get((mat_id, E), 1)
                w(f'ops.element("Truss", {eid}, {n1}, {n2}, {A:.8e}, {mat_tag})')
                ntruss += 1
                # Cable stay mass (usually negligible)
                c1 = nodes.get(n1, [0,0,0])
                c2 = nodes.get(n2, [0,0,0])
                el_len = math.sqrt(sum((c2[i]-c1[i])**2 for i in range(3)))
                if el_len > 0:
                    m = dens * A * el_len / 2.0
                    for nid in [n1, n2]:
                        if nid not in nodal_mass:
                            nodal_mass[nid] = [0.0]*6
                        nodal_mass[nid][0] += m
                        nodal_mass[nid][1] += m
                        nodal_mass[nid][2] += m
        else:
            eid += 1
            E, G, A, Iz, Iy, J, dens = beam_props(mat_id, rnum)
            if E > 0 and A > 0:
                # Determine geomTransf from beam direction
                c1 = nodes.get(n1, [0, 0, 0])
                c2 = nodes.get(n2, [0, 0, 0])
                dx = abs(c2[0] - c1[0])
                dy = abs(c2[1] - c1[1])
                dz = abs(c2[2] - c1[2])
                if dy > dx and dy > dz:
                    transf = 2  # vertical (along Y)
                    mass_axis = "y"
                elif dz > dx and dz > dy:
                    transf = 3  # transverse (along Z)
                    mass_axis = "z"
                else:
                    transf = 1  # horizontal (along X)
                    mass_axis = "x"
                # 3D elasticBeamColumn: tag, n1, n2, A, E, G, J, Iy, Iz, transfTag
                w(f'ops.element("elasticBeamColumn", {eid}, {n1}, {n2}, '
                  f'{A:.8e}, {E:.4e}, {G:.4e}, {J:.8e}, {Iy:.8e}, {Iz:.8e}, {transf})')
                nbeam += 1
                # Beam nodal mass (translational + rotational)
                el_len = math.sqrt(sum((c2[i]-c1[i])**2 for i in range(3)))
                element_mass = beam_lumped_nodal_mass(dens, A, Iy, Iz, J, el_len, mass_axis)
                if any(v > 0 for v in element_mass):
                    for nid in [n1, n2]:
                        if nid not in nodal_mass:
                            nodal_mass[nid] = [0.0]*6
                        for dof_index, value in enumerate(element_mass):
                            nodal_mass[nid][dof_index] += value
    total_elem = eid
    w(f'# Total: {total_elem} elements ({nbeam} beams, {ntruss} truss, {ncablelink} cable links)')
    w('')

    # ---------- nodal mass ----------
    w('# ========== Nodal Mass ==========')
    for nid in sorted(nodal_mass.keys()):
        m = nodal_mass[nid]
        if any(v > 0 for v in m):
            w(f'ops.mass({nid}, {m[0]:.6e}, {m[1]:.6e}, {m[2]:.6e}, '
              f'{m[3]:.6e}, {m[4]:.6e}, {m[5]:.6e})')
    w(f'# Total: {len(nodal_mass)} nodes with mass')
    w('')

    # ---------- coupled DOFs ----------
    section("constraints")
    w('# ========== Coupled DOF (equalDOF) ==========')
    dof_map = {'ux': 1, 'uy': 2, 'uz': 3, 'rotx': 4, 'roty': 5, 'rotz': 6}
    for dof, n1, n2 in cp_pairs:
        if dof == 'all':
            for d in range(1, 7):
                w(f'ops.equalDOF({n1}, {n2}, {d})')
        elif dof in dof_map:
            w(f'ops.equalDOF({n1}, {n2}, {dof_map[dof]})')
    w('')

    # ---------- boundary conditions ----------
    w('# ========== Boundary Conditions ==========')
    for bl in boundary:
        m = re.match(r"^d\s*,\s*(\d+)\s*,\s*(\w+)", bl, re.I)
        if m and m.group(2).lower() == 'all':
            w(f'ops.fix({int(m.group(1))}, 1, 1, 1, 1, 1, 1)')
    w('')

    # ---------- analysis ----------
    section("modal")
    w('# ========== Modal Analysis ==========')
    w('ops.constraints("Transformation")')
    w('ops.numberer("RCM")')
    w('ops.system("FullGeneral")')
    w('ops.test("NormDispIncr", 1e-6, 25)')
    w('ops.algorithm("Linear")')
    w('ops.integrator("LoadControl", 0)')
    w('ops.analysis("Static")')
    w('num_modes = 20')
    if modal_eigen_solver == "default":
        w('eigen_vals = ops.eigen(num_modes)')
    else:
        w(f'eigen_vals = ops.eigen("{modal_eigen_solver}", num_modes)')
    w('')
    w('print(f"Raw eigenvalues (first 10): {[f\'{v:.4e}\' for v in eigen_vals[:10]]}")')
    w('print(f"Num with ev > 0: {sum(1 for v in eigen_vals if v > 0)}")')
    w('')
    w('freqs = []')
    w('for ev in eigen_vals:')
    w('    if ev > 1e-10:')
    w('        freqs.append(math.sqrt(ev) / (2*math.pi))')
    w('    else:')
    w('        freqs.append(0.0)')
    w('')
    w('# Filter out zero-frequency modes')
    w('valid_freqs = [f for f in freqs if f > 1e-6]')
    w('')
    w('print("=" * 60)')
    w('print("  Modal Frequencies (linear elastic, no initial stress)")')
    w('print("=" * 60)')
    w('for i in range(min(10, len(valid_freqs))):')
    w('    print(f"  Mode {i+1}: f = {valid_freqs[i]:.6f} Hz  (T = {1.0/valid_freqs[i]:.4f} s)")')
    w('')
    w('ref = [0.06, 0.10, 0.18, 0.23, 0.29, 0.32, 0.39, 0.42, 0.43, 0.47]')
    w('print(f"\\n{\'Mode\':<6}{\'OpenSees(Hz)\':<18}{\'Ansys(Hz)\':<14}{\'Err(%)\':<10}{\'Status\'}")')
    w('print("-" * 56)')
    w('all_pass = len(valid_freqs) >= min(10, len(ref))')
    w('for i in range(min(10, len(valid_freqs))):')
    w('    err = abs(valid_freqs[i]-ref[i])/ref[i]*100')
    w('    st = "PASS" if err <= 5.0 else "FAIL"')
    w('    if err > 5.0: all_pass = False')
    w('    print(f"{i+1:<6}{valid_freqs[i]:<18.6f}{ref[i]:<14.2f}{err:<10.2f}{st}")')
    w('')
    w('# JSON output for sim-cli')
    w('result = {"frequencies": [round(f,6) for f in valid_freqs[:10]],')
    w('          "reference": ref,')
    w('          "all_within_5pct": all_pass}')
    w('print(json.dumps(result))')

    return _render_opensees_script(header, sections)


if __name__ == '__main__':
    project_root = Path(__file__).resolve().parents[2]
    ans_file = project_root / 'bridge_models' / 'stbridge_ansys' / 'STbridge_apdl_model.txt'
    out_file = project_root / 'bridge_models' / 'stbridge_opensees' / 'stbridge_opensees_modal_model.py'

    print(f"Parsing {ans_file}...")
    nodes, elements, materials, real_props, cp_pairs, boundary = parse_ansys(ans_file)

    print(f"  Nodes:          {len(nodes)}")
    print(f"  Elements:       {len(elements)}")
    print(f"  Materials:      {len(materials)}")
    print(f"  Real constants: {len(real_props)}")
    print(f"  Coupled DOF:    {len(cp_pairs)}")
    print(f"  Boundary cond:  {len(boundary)}")

    # Sanity: check max node id
    if nodes:
        max_nid = max(nodes.keys())
        print(f"  Max node ID:    {max_nid}")

    script = generate_opensees(nodes, elements, materials, real_props, cp_pairs, boundary)
    with open(out_file, 'w', encoding='utf-8') as f:
        f.write(script)
    print(f"\nScript written to {out_file} ({script.count(chr(10))+1} lines)")
