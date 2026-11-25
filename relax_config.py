"""cell-relax 提交模板；资源申请和软件环境由 slurm 脚本设置。"""

from hpc_workflow.common.paths import join_local, project_root


CONFIG = {
    # 本机真实工作根；整棵 work 迁移后只需修改这里，不依赖旧机器绝对路径。
    "work_root": join_local(project_root(), "work"),
    "slurm_script": join_local(project_root(), "slurm", "run_cell-relax.slurm"),
    # 这是提交作业数，不是每批结构数：0 每结构一作业，1 全串行，大于待算数报错。
    "parallel_jobs": 40,
    "python_command": ["python3"],
    "sbatch_command": ["sbatch"],
    "squeue_command": ["squeue"],
    "sacct_command": ["sacct"],
    "command_timeout": 30.0,             # 仅队列查询/提交超时；不限制科学计算时长。

    # 子目录都位于原 pre 任务内；原始 STRU/INPUT/manifest 不会被改写。
    "relax_subdir": "cell-relax",
    "zstar_subdir": "zstar",
    "relax_input_file": None,            # 留空复制每个 pre 任务自己的 INPUT。
    "relax_max_steps": None,             # 留空从 INPUT 的 relax_nmax 读取。
    # 每个 argv 元素独立传递，不使用 shell 拼接；资源数从实际 Slurm 环境获取。
    "abacus_command": ["/public/home/ustc_ljh/abacus/abacus-LTS-3.10.1_gcc-openmpi/toolchain/install/openmpi-5.0.10/bin/mpirun", "-n", "{ntasks}", "abacus"],
    "minimum_distance": 0.1,             # Å，仅拒绝明显重叠。
}
