# Slurm 环境与资源模板

本目录没有 Python 类或函数。两个脚本只声明 Slurm 资源、加载用户环境并执行提交器传入的完整 argv；结构遍历、失败归因和记录写出全部在 Python 中完成。

| 文件 | 输入 | 输出/返回 | 责任 |
|---|---|---|---|
| run_cell-relax.slurm | `$@`: Python 命令前缀、cli.py 绝对地址、run relax worker、--plan 地址、--batch 整数 | worker 的 stdout/stderr 原样进入 Slurm 原始日志；返回 worker 退出码；无参数返回2 | 用户填写资源和 ABACUS 环境，每作业一个串行 worker |
| run_zstar.slurm | `$@`: 同上，阶段为 zstar | 同上 | 用户填写 ABACUS/PYATB/zstar 环境，资源数由 Slurm 环境传递 |

`#SBATCH` 示例仅指定单节点32 MPI任务、每任务1线程。partition、account、内存和时限依集群填写；当前模板保留用户已配置的 `~/setenvs/setup_local-abacus_module-conda_all_env.sh` 并检查返回码；迁移集群时由用户修改对应环境，不由 Python 猜测或替换 MPI 二进制。不用 `set -u`，以兼容 bashrc 中的交互变量。

提交器使用 `sbatch --output/--error` 指定每批独立日志，覆盖模板的默认日志名。输出只保留这一份，Python 的任务/命令分隔标记同时进入 stdout 和 stderr，原生 zstar 自己的 `.zstar` 日志保留原处。模板 `exec "$@"` 保持空格参数边界，不使用 eval，不进行后台分发。`SLURM_NTASKS` 与 `SLURM_CPUS_PER_TASK` 由 Python launcher 模板读取，不由本工作流手工分配节点。

## 拆分项目约定

两个Slurm脚本原样保留；控制器从当前新项目定位cli.py，默认work_root统一为新项目/work。现有环境脚本、MPI/conda前缀与native工具依赖不在本次改造中修改。资源与队列配置完整说明见[根API.md](../API.md)。

