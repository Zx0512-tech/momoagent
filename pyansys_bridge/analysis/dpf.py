"""
斜拉桥阻尼器参数化地震响应分析 — PyMAPDL 求解 + pydpf-core 后处理
=====================================================================
功能：
  1. 使用 PyMAPDL 启动 ANSYS gRPC 服务，参数化修改阻尼器参数并执行有限元分析
  2. 使用 pydpf-core（Data Processing Framework）离线读取 .rst 结果文件
  3. 将位移、单元力、反力等时程数据提取到 NumPy 数组 / pandas DataFrame
  4. 导出为 CSV 文件

依赖：
  pip install numpy pandas ansys-mapdl-core ansys-dpf-core
"""

import os
import shutil
import time
from pathlib import Path
import numpy as np
import pandas as pd
from datetime import datetime
from ansys.mapdl.core import launch_mapdl
import ansys.dpf.core as dpf

# ==================== 用户配置区 ====================

# 原始 APDL 文件路径
PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_APDL = str(PROJECT_ROOT / "bridge_models" / "stbridge_ansys" / "STbridge_apdl_model.txt")

# 工作目录
WORK_DIR = str(PROJECT_ROOT / "parametric_analysis" / "runs")
OUTPUT_DIR = str(PROJECT_ROOT / "parametric_analysis" / "results")

# 阻尼器参数范围
DAMP_C_VALUES = [1000000]
DAMP_ALPHA_VALUES = [-0.5]

# 地震波加速度时程文件
ACCE_FILE = str(PROJECT_ROOT / "analysis_data" / "earthquake_inputs" / "earthquake_acceleration_record.txt")

# ==================== DPF 后处理配置 ====================

# 关注的节点和单元
GIRDER_NODES = [36, 107]           # 主梁端部节点
DAMPER_DISP_NODES = [517, 518, 520, 521]  # 阻尼器位移节点
TOWER_BASE_NODE = 525              # 塔底反力节点
DAMPER_ELEMENTS = [2000, 2010, 3000, 3010]  # 阻尼器单元

# ==================== 求解模块 ====================


def solve_with_pymapdl(damp_c, damp_alpha, job_name, work_dir):
    """
    使用 PyMAPDL 启动 ANSYS，参数化替换阻尼器定义并执行分析。
    分析完成后不进入后处理，返回 .rst 文件路径供 DPF 读取。

    Parameters
    ----------
    damp_c : float
        阻尼系数 C
    damp_alpha : float
        速度指数 α
    job_name : str
        作业名称
    work_dir : str
        工作根目录

    Returns
    -------
    dict or None
        包含 job_dir, rst_path, elapsed_time 等的字典；失败返回 None
    """
    start_time = time.time()

    job_dir = os.path.join(work_dir, job_name)
    os.makedirs(job_dir, exist_ok=True)

    # 复制地震波文件到作业目录
    if os.path.exists(ACCE_FILE):
        shutil.copy(ACCE_FILE, os.path.join(job_dir, "ACCE.txt"))

    print(f"\n{'='*60}")
    print(f"[求解] 作业：{job_name}")
    print(f"  阻尼系数 C = {damp_c:.2E}, 速度指数 α = {damp_alpha}")
    print(f"  开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    mapdl = launch_mapdl(run_location=job_dir, jobname=job_name, nproc=10,
                         override=True, cleanup_on_exit=False)

    try:
        # ---- 读取并清理原始 APDL 文件 ----
        with open(BASE_APDL, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()

        cleaned_lines = []
        for line in lines:
            cleaned_line = line.encode('ascii', errors='ignore').decode('ascii')
            cleaned_lines.append(cleaned_line)

        # 找到阻尼器定义的位置（et,12,combin37）
        damper_line_idx = None
        for i, line in enumerate(cleaned_lines):
            if 'et,12,combin37' in line.lower():
                damper_line_idx = i
                break

        # 找到阻尼器定义结束的位置（en,3010 所在行）
        post_damper_idx = None
        if damper_line_idx is not None:
            for i in range(damper_line_idx, len(cleaned_lines)):
                if 'en,3010' in cleaned_lines[i].lower():
                    post_damper_idx = i + 1
                    break

        # 参数化阻尼器 APDL 命令
        damper_apdl_lines = [
            "ET,12,COMBIN37\n",
            "KEYOPT,12,1,2\n",
            "KEYOPT,12,2,1\n",
            "KEYOPT,12,3,1\n",
            "KEYOPT,12,6,2\n",
            "KEYOPT,12,9,0\n",
            "R,100,0,0,0,0,0,\n",
            f"RMORE, , ,{damp_c},{damp_alpha}\n",
            "TYPE,12\n",
            "REAL,100\n",
            "EN,2000,36,517,36,517\n",
            "EN,2010,36,518,36,518\n",
            "EN,3000,107,520,107,520\n",
            "EN,3010,107,521,107,521\n",
        ]

        # 拼接完整 APDL（去掉末尾的 post_process.mac 调用）
        full_apdl_parts = []
        if damper_line_idx is not None:
            full_apdl_parts.extend(cleaned_lines[:damper_line_idx])
        else:
            full_apdl_parts.extend(cleaned_lines)
        full_apdl_parts.extend(damper_apdl_lines)
        if post_damper_idx is not None:
            for line in cleaned_lines[post_damper_idx:]:
                if 'post_process' in line.lower():
                    continue
                full_apdl_parts.append(line)

        full_apdl_content = ''.join(full_apdl_parts)

        # 保存并执行 APDL
        apdl_filename = f"{job_name}_final.txt"
        apdl_output_path = os.path.join(job_dir, apdl_filename)
        with open(apdl_output_path, 'w', encoding='ascii', errors='ignore') as f:
            f.write(full_apdl_content)
        print(f"  完整 APDL 文件已保存到：{apdl_output_path}")

        mapdl.input(apdl_filename)

        elapsed_time = time.time() - start_time
        hours = int(elapsed_time // 3600)
        minutes = int((elapsed_time % 3600) // 60)
        seconds = int(elapsed_time % 60)

        # 找到 .rst 文件
        rst_path = os.path.join(job_dir, f"{job_name}.rst")
        if not os.path.exists(rst_path):
            # 尝试常见的默认文件名
            for candidate in ["file.rst", f"{job_name}0.rst"]:
                p = os.path.join(job_dir, candidate)
                if os.path.exists(p):
                    rst_path = p
                    break

        print(f"  求解完成！耗时：{hours}小时 {minutes}分钟 {seconds}秒")
        print(f"  RST 文件：{rst_path}")

        return {
            'job_name': job_name,
            'damp_c': damp_c,
            'damp_alpha': damp_alpha,
            'job_dir': job_dir,
            'rst_path': rst_path,
            'elapsed_time_seconds': elapsed_time,
        }

    except Exception as e:
        print(f"  求解失败：{e}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        mapdl.exit()


# ==================== DPF 后处理模块 ====================


def extract_results_with_dpf(rst_path, job_name):
    """
    使用 pydpf-core 读取 .rst 文件，提取时程数据到 Python 数据结构。

    提取内容：
      1. 主梁端部节点位移（Node 36, 107 → UX）
      2. 阻尼器位移（Node 517, 518, 520, 521 → UX）
      3. 阻尼器单元力（Element 2000, 2010, 3000, 3010 → FX）
      4. 塔底反力（Node 525 → FX, FY, MY, MZ）

    Parameters
    ----------
    rst_path : str
        .rst 文件的完整路径
    job_name : str
        作业名称，用于 CSV 文件命名

    Returns
    -------
    dict
        包含所有提取数据的 DataFrame 字典
    """
    if not os.path.exists(rst_path):
        print(f"  错误：RST 文件不存在 → {rst_path}")
        return {}

    print(f"\n{'='*60}")
    print(f"[DPF 后处理] 读取结果文件：{rst_path}")
    print(f"  文件大小：{os.path.getsize(rst_path) / 1024 / 1024:.1f} MB")

    # ---- 1. 打开 RST 模型 ----
    model = dpf.Model(rst_path)

    # 打印模型概要
    print(f"\n  ▎模型概要：")
    result_info = model.metadata.result_info
    print(f"    分析类型：{result_info.analysis_type}")
    print(f"    可用结果数量：{result_info.n_results}")
    print(f"    时间步数量：{len(model.metadata.time_freq_support.time_frequencies)}")

    time_frequencies = model.metadata.time_freq_support.time_frequencies.data
    print(f"    时间范围：{time_frequencies[0]:.4f} ~ {time_frequencies[-1]:.4f} s")
    print(f"    共 {len(time_frequencies)} 个时间步")

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    prefix = os.path.join(OUTPUT_DIR, job_name)
    result_dataframes = {}

    # ---- 2. 提取主梁节点位移 (UX) ----
    print(f"\n  ▎提取主梁位移（节点 {GIRDER_NODES}）...")
    girder_data = _extract_nodal_displacement(model, GIRDER_NODES, time_frequencies)
    if girder_data:
        df = pd.DataFrame(girder_data)
        df.to_csv(f"{prefix}_Girder_Disp.csv", index=False)
        print(f"    → 已导出：{prefix}_Girder_Disp.csv")
        result_dataframes['Girder_Disp'] = df

    # ---- 3. 提取主梁节点加速度 (AX) ----
    print(f"\n  ▎提取主梁加速度（节点 {GIRDER_NODES}）...")
    girder_accel_data = _extract_nodal_acceleration(model, GIRDER_NODES, time_frequencies)
    if girder_accel_data:
        df = pd.DataFrame(girder_accel_data)
        df.to_csv(f"{prefix}_Girder_Accel.csv", index=False)
        print(f"    → 已导出：{prefix}_Girder_Accel.csv")
        result_dataframes['Girder_Accel'] = df

    # ---- 4. 提取阻尼器节点位移 (UX) ----
    print(f"\n  ▎提取阻尼器位移（节点 {DAMPER_DISP_NODES}）...")
    damper_disp_data = _extract_nodal_displacement(
        model, DAMPER_DISP_NODES, time_frequencies
    )
    if damper_disp_data:
        df = pd.DataFrame(damper_disp_data)
        df.to_csv(f"{prefix}_Damper_Disp.csv", index=False)
        print(f"    → 已导出：{prefix}_Damper_Disp.csv")
        result_dataframes['Damper_Disp'] = df

    # ---- 5. 提取阻尼器单元力 (FX) ----
    print(f"\n  ▎提取阻尼器出力（单元 {DAMPER_ELEMENTS}）...")
    # NOTE: 使用 PyMAPDL (POST26) 补全提取 COMBIN37 单元的阻尼力
    damper_force_data = _extract_element_nodal_forces(
        rst_path, DAMPER_ELEMENTS, time_frequencies
    )
    if damper_force_data:
        df = pd.DataFrame(damper_force_data)
        df.to_csv(f"{prefix}_Damper_Force.csv", index=False)
        print(f"    → 已导出：{prefix}_Damper_Force.csv")
        result_dataframes['Damper_Force'] = df

    # ---- 6. 提取塔底反力 ----
    print(f"\n  ▎提取塔底反力（节点 {TOWER_BASE_NODE}）...")
    tower_force_data = _extract_reaction_forces(
        model, TOWER_BASE_NODE, time_frequencies
    )
    if tower_force_data:
        df = pd.DataFrame(tower_force_data)
        df.to_csv(f"{prefix}_Tower_Force.csv", index=False)
        print(f"    → 已导出：{prefix}_Tower_Force.csv")
        result_dataframes['Tower_Force'] = df

    print(f"\n  ▎DPF 后处理完成，共导出 {len(result_dataframes)} 个 CSV 文件")
    return result_dataframes


def _extract_nodal_displacement(model, node_ids, time_frequencies):
    """
    使用 DPF 逐时间步提取指定节点的 X 向位移。
    Time 列从 fields_container 的 label space 获取，确保与数据列长度一致。

    Returns
    -------
    dict
        {'Time': [...], 'NodeXX_UX': [...], ...}
    """
    data = {'Time': []}

    # 创建 Scoping：限定目标节点
    node_scoping = dpf.Scoping(ids=node_ids, location=dpf.locations.nodal)

    # 获取位移结果算子
    disp_op = model.results.displacement()
    disp_op.inputs.mesh_scoping(node_scoping)
    
    # 获取所有时间步
    time_scoping = dpf.time_freq_scoping_factory.scoping_on_all_time_freqs(model)
    disp_op.inputs.time_scoping(time_scoping)

    # 一次性获取所有时间步的结果（FieldsContainer）
    fields_container = disp_op.outputs.fields_container()

    # 初始化每个节点的列
    for nid in node_ids:
        data[f'Node{nid}_UX'] = []

    # 遍历每个时间步的 field，从 label space 获取时间
    for i, field in enumerate(fields_container):
        # 从 label space 获取时间步 ID（1-indexed），再查找实际时间值
        label_space = fields_container.get_label_space(i)
        time_id = label_space.get('time', i + 1)  # 1-indexed
        if time_id - 1 < len(time_frequencies):
            t = float(time_frequencies[time_id - 1])
        else:
            t = float(time_id)
        data['Time'].append(t)

        for nid in node_ids:
            try:
                # 获取该节点在当前 field 中的分量数据
                idx = field.scoping.ids.tolist().index(nid)
                # 第0个分量 = X 方向
                ux = field.data[idx][0]
                data[f'Node{nid}_UX'].append(float(ux))
            except (ValueError, IndexError):
                data[f'Node{nid}_UX'].append(0.0)

    return data


def _extract_nodal_acceleration(model, node_ids, time_frequencies):
    """
    使用 DPF 逐时间步提取指定节点的 X 向加速度。

    Returns
    -------
    dict
        {'Time': [...], 'NodeXX_AX': [...], ...}
    """
    data = {'Time': []}

    node_scoping = dpf.Scoping(ids=node_ids, location=dpf.locations.nodal)

    accel_op = model.results.acceleration()
    accel_op.inputs.mesh_scoping(node_scoping)

    time_scoping = dpf.time_freq_scoping_factory.scoping_on_all_time_freqs(model)
    accel_op.inputs.time_scoping(time_scoping)

    fields_container = accel_op.outputs.fields_container()

    for nid in node_ids:
        data[f'Node{nid}_AX'] = []

    for i, field in enumerate(fields_container):
        label_space = fields_container.get_label_space(i)
        time_id = label_space.get('time', i + 1)
        if time_id - 1 < len(time_frequencies):
            t = float(time_frequencies[time_id - 1])
        else:
            t = float(time_id)
        data['Time'].append(t)

        for nid in node_ids:
            try:
                idx = field.scoping.ids.tolist().index(nid)
                ax = field.data[idx][0]
                data[f'Node{nid}_AX'].append(float(ax))
            except (ValueError, IndexError):
                data[f'Node{nid}_AX'].append(0.0)

    return data


def _extract_element_nodal_forces(rst_path, elem_ids, time_frequencies):
    """
    使用 PyMAPDL (POST26) 从 RST 中离线计算和提取 COMBIN37 单元的阻尼力 (X 方向)。
    背景：DPF 对于 1D 的非线性弹簧/阻尼器控制单元 (如 COMBIN37) 默认不记录传统的 Node Nodal Forces，
          因此 DPF 内置的 element_nodal_forces 对此类单元返回为空。
          最可靠的方案是离线启动 MAPDL 读取 RST 并使用 POST26 内核 (ESOL) 动态算出这些阻尼力。

    Returns
    -------
    dict
        {'Time': [...], 'ElemXXXX_FX': [...], ...}
    """
    data = {'Time': time_frequencies.tolist()}
    for eid in elem_ids:
        data[f'Elem{eid}_FX'] = []

    print("    [提示] 检测到阻尼力提取，正在后台离线启动 PyMAPDL (POST26) 提取 COMBIN37 受力...")
    from ansys.mapdl.core import launch_mapdl
    import os
    
    # 获取 RST 所在路径作为 MAPDL 临时打补丁运行的目录
    run_dir = os.path.dirname(os.path.abspath(rst_path))
    job_name = os.path.basename(rst_path).replace('.rst', '')
    
    mapdl = None
    try:
        # 静默拉起 MAPDL
        mapdl = launch_mapdl(run_location=run_dir, jobname=job_name, 
                             override=True, cleanup_on_exit=False, loglevel='ERROR')
        mapdl.post26()
        mapdl.numvar(200) # 增大变量容量

        # 根据 APDL 流定义：
        # Element 2000 锚定主梁 Node 36
        # Element 2010 锚定主梁 Node 36
        # Element 3000 锚定主梁 Node 107
        # Element 3010 锚定主梁 Node 107
        node_map = {2000: 36, 2010: 36, 3000: 107, 3010: 107}
        
        var_start = 10
        for i, eid in enumerate(elem_ids):
            nid = node_map.get(eid, 36) # 默认 36 防错
            var_id = var_start + i
            # ESOL, var, ELEM, NODE, Item, Comp
            mapdl.esol(var_id, eid, nid, "F", "X")
            
            # 取回变量的数据数组
            forces = mapdl.get_variable(var_id)
            if forces is not None:
                # 记录数据，如果某些时间步长度不完全对应，我们以 Time Frequencies 的长度为准对齐
                data[f'Elem{eid}_FX'] = forces.tolist()
            else:
                data[f'Elem{eid}_FX'] = [0.0] * len(time_frequencies)

    except Exception as e:
        print(f"    PyMAPDL 补偿提取失败: {e}")
        for eid in elem_ids:
            if not data[f'Elem{eid}_FX']:
                data[f'Elem{eid}_FX'] = [0.0] * len(time_frequencies)
    finally:
        if mapdl is not None:
            try:
                mapdl.exit() # 确保后台 MAPDL 退出，释放内存和锁
            except Exception:
                pass

    # 由于可能会有一两个步的细微长度偏差（比如末尾阶段阶段中止不同步等），确保长度等比安全
    max_len = len(data['Time'])
    for eid in elem_ids:
        arr = data[f'Elem{eid}_FX']
        if len(arr) > max_len:
            data[f'Elem{eid}_FX'] = arr[:max_len]
        elif len(arr) < max_len:
            data[f'Elem{eid}_FX'] = arr + [0.0] * (max_len - len(arr))

    return data


def _extract_reaction_forces(model, node_id, time_frequencies):
    """
    使用 DPF 逐时间步提取指定节点的反力（FX, FY, MY, MZ）。
    Time 列从 fields_container 的 label space 获取，确保与数据列长度一致。

    Returns
    -------
    dict
        {'Time': [...], 'Tower_FX': [...], 'Tower_FY': [...], ...}
    """
    data = {
        'Time': [],
        'Tower_FX': [],
        'Tower_FY': [],
        'Tower_MY': [],
        'Tower_MZ': [],
    }

    # 反力节点 scoping
    node_scoping = dpf.Scoping(ids=[node_id], location=dpf.locations.nodal)

    # 使用 reaction_force 算子
    rf_op = model.results.reaction_force()
    rf_op.inputs.mesh_scoping(node_scoping)
    
    # 获取所有时间步
    time_scoping = dpf.time_freq_scoping_factory.scoping_on_all_time_freqs(model)
    rf_op.inputs.time_scoping(time_scoping)

    fields_container = rf_op.outputs.fields_container()

    # 分量索引：0=FX, 1=FY, 2=FZ (对于 3D)
    # 力矩需要通过 structural_force 或其他方式获取
    for i, field in enumerate(fields_container):
        # 从 label space 获取时间
        label_space = fields_container.get_label_space(i)
        time_id = label_space.get('time', i + 1)
        if time_id - 1 < len(time_frequencies):
            t = float(time_frequencies[time_id - 1])
        else:
            t = float(time_id)
        data['Time'].append(t)

        try:
            idx = field.scoping.ids.tolist().index(node_id)
            node_data = field.data[idx]
            data['Tower_FX'].append(float(node_data[0]) if len(node_data) > 0 else 0.0)
            data['Tower_FY'].append(float(node_data[1]) if len(node_data) > 1 else 0.0)
            # 对于 BEAM4 单元，反力可能包含力矩分量
            data['Tower_MY'].append(float(node_data[4]) if len(node_data) > 4 else 0.0)
            data['Tower_MZ'].append(float(node_data[5]) if len(node_data) > 5 else 0.0)
        except (ValueError, IndexError):
            data['Tower_FX'].append(0.0)
            data['Tower_FY'].append(0.0)
            data['Tower_MY'].append(0.0)
            data['Tower_MZ'].append(0.0)

    return data


# ==================== 独立 DPF 测试入口 ====================


def test_dpf_only(rst_path):
    """
    仅使用 DPF 读取指定 RST 文件并打印概要信息（无需 ANSYS 求解）。
    用于快速测试 DPF 是否能正常读取已有结果。

    用法：
        python run_analysis_with_dpf.py --dpf-only d:\\pyansys\\file.rst
    """
    print(f"[DPF 独立测试] 读取文件：{rst_path}")
    model = dpf.Model(rst_path)

    # 打印元数据
    print(f"\n{'='*50}")
    print("模型元数据：")
    result_info = model.metadata.result_info
    print(f"  分析类型    ：{result_info.analysis_type}")
    print(f"  物理类型    ：{result_info.physics_type}")
    print(f"  可用结果数量：{result_info.n_results}")
    print(f"  单元类型数  ：{result_info.n_results}")

    # 列出可用结果
    print(f"\n  可用结果列表：")
    for i in range(result_info.n_results):
        res = result_info.available_results[i]
        print(f"    [{i}] {res.name} ({res.n_components} 分量)")

    # 时间步信息
    tf = model.metadata.time_freq_support
    times = tf.time_frequencies.data
    print(f"\n  时间步数量  ：{len(times)}")
    print(f"  时间范围    ：{times[0]:.4f} ~ {times[-1]:.4f} s")

    # 网格信息
    mesh = model.metadata.meshed_region
    print(f"\n  节点数      ：{mesh.nodes.n_nodes}")
    print(f"  单元数      ：{mesh.elements.n_elements}")

    # 快速提取第一个和最后一个时间步的位移
    print(f"\n{'='*50}")
    print("快速验证 — 提取位移数据（第一步和最后一步）：")

    disp_op = model.results.displacement()
    node_scoping = dpf.Scoping(ids=GIRDER_NODES, location=dpf.locations.nodal)
    disp_op.inputs.mesh_scoping(node_scoping)

    fc = disp_op.outputs.fields_container()

    for step_idx in [0, len(fc) - 1]:
        field = fc[step_idx]
        t = times[step_idx] if step_idx < len(times) else times[-1]
        print(f"\n  时间 = {t:.4f} s (步 {step_idx + 1}/{len(fc)})：")
        for nid in GIRDER_NODES:
            try:
                idx = field.scoping.ids.tolist().index(nid)
                vals = field.data[idx]
                print(f"    Node {nid}: UX={vals[0]:.6e}, UY={vals[1]:.6e}, UZ={vals[2]:.6e}")
            except (ValueError, IndexError):
                print(f"    Node {nid}: 没有找到数据")

    print(f"\n[DPF 独立测试] 完成！DPF 读取正常。")
    return model


# ==================== 主流程 ====================


def run_full_analysis(damp_c, damp_alpha, job_name, work_dir):
    """
    完整流程：PyMAPDL 求解 → DPF 结果提取 → CSV 导出
    """
    # 步骤 1：PyMAPDL 求解
    solve_result = solve_with_pymapdl(damp_c, damp_alpha, job_name, work_dir)
    if solve_result is None:
        return None

    # 步骤 2：DPF 后处理
    rst_path = solve_result['rst_path']
    result_dfs = extract_results_with_dpf(rst_path, job_name)

    return {
        **solve_result,
        'status': 'completed',
        'result_dataframes': result_dfs,
    }


def main():
    """主函数：参数化分析 + DPF 后处理"""
    import sys

    # 支持 --dpf-only 模式：仅测试 DPF 读取
    if len(sys.argv) >= 3 and sys.argv[1] == '--dpf-only':
        test_dpf_only(sys.argv[2])
        return

    # 支持 --dpf-extract 模式：仅对已有 RST 执行 DPF 提取
    if len(sys.argv) >= 4 and sys.argv[1] == '--dpf-extract':
        rst_file = sys.argv[2]
        job_label = sys.argv[3]
        extract_results_with_dpf(rst_file, job_label)
        return

    # 默认模式：完整流程（求解 + DPF 提取）
    os.makedirs(WORK_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_results = []

    param_combinations = []
    for c in DAMP_C_VALUES:
        for alpha in DAMP_ALPHA_VALUES:
            param_combinations.append({
                'damp_c': c,
                'damp_alpha': alpha,
                'job_name': f"damper_C{int(c)}_A{alpha}"
            })

    print(f"共 {len(param_combinations)} 组参数需要计算")
    print(f"阻尼系数：{DAMP_C_VALUES}")
    print(f"速度指数：{DAMP_ALPHA_VALUES}")
    print(f"后处理方式：pydpf-core（离线读取 .rst）")

    for params in param_combinations:
        result = run_full_analysis(
            params['damp_c'],
            params['damp_alpha'],
            params['job_name'],
            WORK_DIR,
        )
        if result:
            all_results.append(result)

    print(f"\n{'='*60}")
    print(f"全部计算完成，共 {len(all_results)}/{len(param_combinations)} 组成功")
    for r in all_results:
        print(f"  {r['job_name']}: {r['status']}, 耗时 {r['elapsed_time_seconds']:.0f}s")
    print(f"结果目录：{OUTPUT_DIR}")


if __name__ == "__main__":
    main()
