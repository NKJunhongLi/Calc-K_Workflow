#!/bin/bash
#SBATCH --job-name=HPC_cell-relax
#SBATCH --partition=hfacnormal01
#SBATCH --nodes=1
#SBATCH --ntasks=32
#SBATCH --ntasks-per-node=32
#SBATCH --cpus-per-task=1
#SBATCH --mem=57G
#SBATCH --output=job.%j.out
#SBATCH --error=job.%j.err

# 用户在这里填写自己的 partition/account/time/mem 等资源申请。
# 提交器只控制作业数量，不改写资源数；队列决定各作业何时获得资源。

echo "========================================"
echo "Job ID      : $SLURM_JOB_ID"
echo "Node        : $SLURM_JOB_NODELIST"
echo "Working dir : $SLURM_SUBMIT_DIR"
echo "MPI ranks   : $SLURM_NTASKS"
echo "CPU/task    : $SLURM_CPUS_PER_TASK"
echo "Begin Time  : $(date)"
echo "========================================"

cd "$SLURM_SUBMIT_DIR" || exit 1

# 用户在这里加载模块、Conda、MPI 和 ABACUS 环境

### * * * TASK RUNNING ENVIRONMENT SET FROM HERE * * * ###

source /public/home/ustc_ljh/setenvs/setup_local-abacus_module-conda_all_env.sh || exit 1

# source /public/home/ustc_ljh/setenvs/local_abacus-lts-3.10.1.sh || exit 1

export OMP_NUM_THREADS=$SLURM_CPUS_PER_TASK

### * * * TASK RUNNING ENVIRONMENT ENDS AT HERE * * * ###

# 每个初始化命令都应检查返回值，避免环境未加载却继续计算。
ulimit -s unlimited

# 运行前确认实际 Python 和关键依赖
python3 - <<'PY' || exit 1
import os
import sys

expected = "/public/home/ustc_ljh/.conda/envs/deepmd"
print("Python executable:", sys.executable, flush=True)
print("Python prefix    :", sys.prefix, flush=True)

if os.path.realpath(sys.prefix) != os.path.realpath(expected):
    raise RuntimeError("当前 Python 不属于预期的 deepmd 环境")

import pymatgen.core
from ase.io import read, write
import zstar

print("环境与关键依赖检查通过", flush=True)
PY

echo
echo "===== TASK START ====="

# 一份作业只启动一个 Python worker；worker 串行遍历该批全部结构。
# 不启用 nounset，兼容用户 bashrc；Python 的 argv 由提交器逐项传入。
if [[ "$#" -eq 0 ]]; then
    echo "错误：请用 python3 cli.py run relax 提交此脚本。" >&2
    exit 2
fi
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
exec "$@"
