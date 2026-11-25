"""zstar 提交模板；本工作流不修改 zstar 源码或替代其内部断点续算。"""

from hpc_workflow.common.paths import join_local, project_root


CONFIG = {
    # 真实 I/O 根按本机语法填写；计划和批次内部引用保持相对，支持整目录迁移。
    "work_root": join_local(project_root(), "work"),
    "slurm_script": join_local(project_root(), "slurm", "run_zstar.slurm"),
    "parallel_jobs": 40,
    "python_command": ["python3"],
    "sbatch_command": ["sbatch"],
    "squeue_command": ["squeue"],
    "sacct_command": ["sacct"],
    "command_timeout": 30.0,
    "relax_subdir": "cell-relax",
    "zstar_subdir": "zstar",

    # 固定正式容差 1e-3 Å。扫描只用于 FLAG/诊断，不会采用宽松容差强行恢复原群。
    "symprec": 1e-3,
    "angle_tolerance": -1.0,
    "symmetry_scan_tolerances": [1e-5, 1e-4, 1e-3, 1e-2, 1e-1],
    "minimum_distance": 0.1,
    "max_symmetry_strain": 0.01,          # 无量纲，去除基变换/刚体转动后的相对形变。
    "relax_max_steps": None,

    # zstar 原生接收两个 launcher 字符串；这里先由 argv 模板生成并正确引用。
    "abacus_command": ["/public/home/ustc_ljh/abacus/abacus-LTS-3.10.1_gcc-openmpi/toolchain/install/openmpi-5.0.10/bin/mpirun", "-n", "{ntasks}", "abacus"],
    "pyatb_command": ["/public/home/ustc_ljh/.conda/envs/deepmd/bin/mpirun", "-n", "{ntasks}", "pyatb"],
    "zstar_command": ["zstar"],
    "xc": "pbesol",
    "kspacing": 0.1,
    # 留空使用 pre(--force)/run/post/static 四条默认命令，全部逐条执行。
    # 自定义时提供四个键 pre/run/post/static，各值为 argv 列表；可用占位符
    # {symprec}/{ntasks}/{omp_threads}/{abacus_command}/{pyatb_command}。
    "zstar_commands": {
    # 准备输入；已有任务如何处理仍交给 zstar。
    "pre": [
        "zstar", "bec", "pre",
        "--stru", "STRU",
        "--input", "/public/home/ustc_ljh/tasks/MP_HPC_database_work/work_v0.0.2/INPUT-scf",
        "--force",
    ],

    # 执行计算；两个 launcher 分别作为完整的单个参数传给 zstar。
    "run": [
        "zstar", "bec", "run",
        "--root", ".",
        "--abacus-command", "{abacus_command}",
        "--pyatb-command", "{pyatb_command}"
    ],

    # 后处理；使用与结构对称化相同的正式容差。
    "post": [
        "zstar", "bec", "post",
        "--root", "."
    ],

    # Gamma 点声子及静态介电响应。
    "static": [
        "zstar", "dielectric", "static",
    ],
},
}
