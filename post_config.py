"""一个独立 work_root 对应一个计算批次；仅发布整批最终结果 CSV。"""

from hpc_workflow.common.paths import join_local, project_root


CONFIG = {
    # 填写当前主机真实批次根；LOCAL身份来自封存记录，旧样本/nominal列保持空值。
    "work_root": join_local(project_root(), "work"),
    # 留空输出 work_root/post/zstar_results.csv。明确指定时可使用本机绝对路径，
    # 或相对本配置文件的路径。已有输出一律拒绝覆盖，不产生多份自动时间戳快照。
    "output_csv": None,
    # 有历史计划时以该计划冻结的目录为准；此默认名称仅用于定位尚无计划的任务。
    "zstar_subdir": "zstar",
    # 只在该受限相对目录树中定位同目录的 band.dat / band_info.dat；多套报歧义。
    "band_relative_root": "0.no-move/pyatb-band/Out",
    # 最终CSV发布后，清理已确认成功/带隙拒绝/明显虚频拒绝结构的指定CSR及电荷restart。
    # 技术失败、输入冲突、后处理读取失败始终保留。False仅关闭自动清理，post clean仍可显式执行。
    # post validate只读显示预计清理量，绝不删除，也不创建锁文件。
    "cleanup_completed": True,
}
