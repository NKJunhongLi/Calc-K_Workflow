"""本地结构前处理配置；只需填写structures_root，其余设置可沿用默认值。"""

from hpc_workflow.common.paths import join_local, project_root


CONFIG = {
    # 必填：当前主机可读的结构父目录绝对路径；仅处理该目录的直接普通文件。
    # 不递归、不按文件名过滤，依次尝试CIF、ABACUS、自动识别；请事先整理好目录。
    "structures_root": None,
    # 独立批次根；与relax/zstar/post默认配置一致，不复用旧工作流的计算现场。
    "work_root": join_local(project_root(), "work"),
    # 与源文件名共同决定永久任务身份；同一批结构的子集重跑不要修改命名空间。
    "dataset_namespace": "local",

    # 既有非磁性INPUT模板原样使用；自定义时填写绝对路径，缺失或为空明确失败。
    "input_file": None,
    # True复用当前协议的完成记录和非空产物；False遇到已占用任务目录时报冲突。
    "reuse_existing": True,

    # 每份结构只调用一次所选原胞转换；不建立原子映射、不做输出重复对称性识别。
    "primitive_method": "find_primitive",  # find_primitive / standardize_cell。
    "symprec": 1e-3,                       # Å，spglib位置容差，必须为有限正数。
    "angle_tolerance": -1.0,               # -1委托spglib；其他值须为有限正角度。
    "standardize_no_idealize": True,       # 仅standardize_cell使用，True不理想化晶格。
    # spglib返回None时默认拒绝；use_input_cell显式沿用输入晶胞，并在记录中标记。
    "primitive_failure_policy": "reject", # reject / use_input_cell。
    "minimum_distance": 0.1,              # Å，短程重叠阈值，不代替材料物理合理性检查。
    "writer_timeout": 120.0,              # 秒，常驻隔离writer每次响应的等待上限。

    # 资源始终是Linux路径文本；不按Windows路径转换，也不探测实际赝势或轨道文件。
    # 官方库主目录由此配置；私人逐元素路径继续在resources/abacus.py原表中维护。
    "dojo_root": "/public/home/ustc_ljh/abacus/ABACUS-orbitals-main/Dojo-NC-FR",
}
