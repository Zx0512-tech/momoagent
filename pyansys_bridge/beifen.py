"""
斜拉桥阻尼器参数化地震响应分析 - 使用 PyMAPDL
功能：批量修改阻尼器参数，运行 ANSYS 分析，由 APDL 宏自动导出结果
"""

import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from ansys.mapdl.core import launch_mapdl

# ==================== 用户配置区 ====================

# 原始 APDL 文件路径
PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASE_APDL = str(PROJECT_ROOT / "bridge_models" / "stbridge_ansys" / "STbridge_apdl_model.txt")

# 后处理宏文件路径
POST_MACRO = str(PROJECT_ROOT / "parametric_analysis" / "post_process.mac")

# 工作目录
WORK_DIR = str(PROJECT_ROOT / "parametric_analysis" / "runs")
OUTPUT_DIR = str(PROJECT_ROOT / "parametric_analysis" / "results")

# 阻尼器参数范围
DAMP_C_VALUES = [1000000]
DAMP_ALPHA_VALUES = [-0.5]

# 地震波加速度时程文件
ACCE_FILE = str(PROJECT_ROOT / "analysis_data" / "earthquake_inputs" / "earthquake_acceleration_record.txt")

# ==================== 代码区 ====================

def run_analysis_with_pymapdl(damp_c, damp_alpha, job_name, work_dir):
    """
    使用 PyMAPDL 运行参数化分析并提取结果
    """
    start_time = time.time()

    job_dir = os.path.join(work_dir, job_name)
    os.makedirs(job_dir, exist_ok=True)

    # 复制依赖文件到作业目录
    if os.path.exists(ACCE_FILE):
        shutil.copy(ACCE_FILE, os.path.join(job_dir, "ACCE.txt"))
    if os.path.exists(POST_MACRO):
        shutil.copy(POST_MACRO, os.path.join(job_dir, "post_process.mac"))

    print(f"\n{'='*60}")
    print(f"运行作业：{job_name}")
    print(f"阻尼系数 C = {damp_c:.2E}, 速度指数 α = {damp_alpha}")
    print(f"开始时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # 启动 MAPDL（不清理文件）
    mapdl = launch_mapdl(run_location=job_dir, jobname=job_name, nproc=1,
                          override=True, cleanup_on_exit=False)

    try:
        # 读取原始 APDL 文件
        with open(BASE_APDL, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()

        # 清理特殊字符（保留空行以维持 *VREAD 等格式行的位置）
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

        # ---- 组装参数化的阻尼器 APDL 命令 ----
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

        # ---- 拼接完整的 APDL 文件内容 ----
        full_apdl_parts = []

        # 第一部分：阻尼器定义之前的所有命令
        if damper_line_idx is not None:
            full_apdl_parts.extend(cleaned_lines[:damper_line_idx])
        else:
            full_apdl_parts.extend(cleaned_lines)

        # 第二部分：参数化阻尼器命令
        full_apdl_parts.extend(damper_apdl_lines)

        # 第三部分：阻尼器定义之后的命令
        if post_damper_idx is not None:
            full_apdl_parts.extend(cleaned_lines[post_damper_idx:])

        full_apdl_content = ''.join(full_apdl_parts)

        # ---- 保存完整 APDL 文件 ----
        apdl_filename = f"{job_name}_final.txt"
        apdl_output_path = os.path.join(job_dir, apdl_filename)
        with open(apdl_output_path, 'w', encoding='ascii', errors='ignore') as f:
            f.write(full_apdl_content)
        print(f"完整 APDL 文件已保存到：{apdl_output_path}")

        # ---- 通过文件方式执行完整的 APDL ----
        # 使用 mapdl.input() 从文件读取执行，可正确处理 *CREAT/*END、*VREAD 等宏命令
        # APDL 末尾会自动调用 post_process.mac 导出 CSV 结果
        mapdl.input(apdl_filename)



        # 计算运行时间
        elapsed_time = time.time() - start_time
        hours = int(elapsed_time // 3600)
        minutes = int((elapsed_time % 3600) // 60)
        seconds = int(elapsed_time % 60)

        print(f"分析完成，结果 CSV 已由 APDL 宏导出至：{job_dir}")
        print(f"结束时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"计算耗时：{hours}小时 {minutes}分钟 {seconds}秒")

        return {
            'job_name': job_name,
            'damp_c': damp_c,
            'damp_alpha': damp_alpha,
            'status': 'completed',
            'elapsed_time_seconds': elapsed_time,
            'job_dir': job_dir
        }

    except Exception as e:
        print(f"分析失败：{e}")
        elapsed_time = time.time() - start_time
        print(f"失败前耗时：{elapsed_time:.1f}秒")
        return None
    finally:
        mapdl.exit()


def main():
    """
    主函数：执行参数化分析
    """
    os.makedirs(WORK_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    all_results = []

    # 生成参数组合
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

    # 执行分析
    for params in param_combinations:
        results = run_analysis_with_pymapdl(
            params['damp_c'],
            params['damp_alpha'],
            params['job_name'],
            WORK_DIR
        )

        if results:
            all_results.append(results)

    # 打印汇总
    print(f"\n{'='*60}")
    print(f"全部计算完成，共 {len(all_results)}/{len(param_combinations)} 组成功")
    for r in all_results:
        print(f"  {r['job_name']}: {r['status']}, 耗时 {r['elapsed_time_seconds']:.0f}s")
    print(f"结果目录：{WORK_DIR}")


if __name__ == "__main__":
    main()
