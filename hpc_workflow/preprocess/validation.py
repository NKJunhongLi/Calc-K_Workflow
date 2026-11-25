"""本地目录、三段结构读取与必要物理检查；不读取交接表，不建立业务产物。"""

import math
import os

from ..common.errors import ConfigError, IntegrityError
from ..common.paths import ensure_host_path, path_style
from ..inputs.models import ELEMENTS
from ..resources.abacus import build_abacus_resources


def validate_config(config: dict, check_inputs: bool = True) -> dict:
    """验证本地 pre 配置并返回副本；不创建目录，不检查资源文件本体。"""
    checked = dict(config)
    # 直接调用也拒绝旧来源设置；CLI 加载器同时负责全部未知键检查。
    removed = {"input_source", "mp_input_mode", "screening_handoff", "state_root", "metadata_csv",
               "structure_variant", "cif_suffix", "mapping_tolerance_factor"}.intersection(checked)
    if removed:
        raise ConfigError("本地 pre 不接受旧配置项：" + ", ".join(sorted(removed)))
    for key, allowed in {
        "primitive_method": {"find_primitive", "standardize_cell"},
        "primitive_failure_policy": {"reject", "use_input_cell"},
    }.items():
        if not isinstance(checked.get(key), str) or checked[key] not in allowed:
            raise ConfigError(f"{key} 必须为 {sorted(allowed)}")
    # 稳定命名、开关和科学容差保持旧契约，字符串与 bool 不可冒充数值。
    if not isinstance(checked.get("dataset_namespace"), str) or not checked["dataset_namespace"].strip():
        raise ConfigError("dataset_namespace 必须为非空字符串")
    for key in ("reuse_existing", "standardize_no_idealize"):
        if type(checked.get(key)) is not bool:
            raise ConfigError(f"{key} 必须为 bool")
    for key in ("symprec", "minimum_distance", "writer_timeout"):
        value = checked.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ConfigError(f"{key} 必须为有限正数")
    angle = checked.get("angle_tolerance")
    if isinstance(angle, bool) or not isinstance(angle, (int, float)) or not math.isfinite(angle) or (angle != -1 and angle <= 0):
        raise ConfigError("angle_tolerance 必须为 -1 或有限正数")

    # 结构根必须原本就是本机绝对路径，不能随命令启动目录改变输入来源。
    ensure_host_path(checked["work_root"])
    root = checked.get("structures_root")
    if root is not None:
        ensure_host_path(root)
    elif check_inputs:
        raise ConfigError("请在 pre 配置中指定绝对 structures_root")
    if check_inputs and (not os.path.isdir(root) or os.path.islink(root)
                         or getattr(os.path, "isjunction", lambda path: False)(root)):
        raise ConfigError(f"结构父目录不存在、不是普通目录或为链接：{root}")
    # 输入与输出树隔离，防止重跑时把自己的 INPUT/CSV 当成源结构。
    if root is not None:
        # Windows 目录大小写等价；比较前归一大小写，避免祖先路径仅因拼写不同漏判。
        source_real = os.path.normcase(os.path.realpath(root))
        work_real = os.path.normcase(os.path.realpath(checked["work_root"]))
        try:
            overlap = os.path.commonpath((source_real, work_real)) in (source_real, work_real)
        except ValueError:
            overlap = False
        if overlap:
            raise ConfigError("structures_root 与 work_root 必须是互不包含的独立目录")
    custom_input = checked.get("input_file")
    # INPUT 保持用户原字节；明确指定的坏路径或空文件不能退回内置模板。
    if custom_input not in (None, ""):
        path_style(custom_input)
        ensure_host_path(custom_input)
        if not os.path.isfile(custom_input):
            raise ConfigError(f"自定义 INPUT 不存在：{custom_input}")
        with open(custom_input, "rb") as handle:
            if not handle.read().strip():
                raise ConfigError("自定义 INPUT 不能为空")
    build_abacus_resources(checked)
    return checked


def validate_geometry(atoms: object, minimum_distance: float = 0.1) -> None:
    """检查非空有限几何及短程重叠；不建立全原子对距离矩阵。"""
    import numpy as np
    from ase.geometry.minkowski_reduction import minkowski_reduce
    from ase.neighborlist import neighbor_list

    if not len(atoms) or not np.isfinite(atoms.cell.array).all() or not np.isfinite(atoms.positions).all():
        raise IntegrityError("nonfinite_or_empty_geometry")
    if abs(float(np.linalg.det(atoms.cell.array))) < 1e-10:
        raise IntegrityError("nonfinite_or_degenerate_geometry")
    atoms.pbc = True
    # 先拒绝与自身周期镜像重叠的小晶胞，避免近邻枚举在异常小晶格中膨胀。
    reduced, _ = minkowski_reduce(atoms.cell.array, pbc=True)
    if float(np.linalg.norm(reduced, axis=1).min()) < minimum_distance:
        raise IntegrityError("overlapping_periodic_images")
    distances = neighbor_list("d", atoms, minimum_distance, self_interaction=False)
    if len(distances) and bool(np.any(distances < minimum_distance)):
        raise IntegrityError("overlapping_atoms")


def read_structure_safely(file_path: str) -> object:
    """按 CIF、ABACUS、自动识别解析；三次失败时完整报告原因。"""
    from ase.io import read

    ensure_host_path(file_path)
    errors = []
    # 这里只捕获解析异常；成功后的物理检查不能通过换解析器绕过。
    for label, format_name in (("CIF", "cif"), ("ABACUS", "abacus"), ("自动识别", None)):
        try:
            # 文件名中的 @ 是普通字符，不把它拆成 ASE 的帧选择表达式。
            return read(file_path, format=format_name, do_not_split_by_at_sign=True)
        except Exception as exc:
            errors.append(f"{label} 格式读取失败：{type(exc).__name__}: {exc}")
    raise IntegrityError(f"读取结构文件失败：{file_path}\n" + "\n".join(errors))


def read_and_validate_structure(path: str, config: dict) -> object:
    """读取单个结构并检查占位、元素和几何；源约束/磁矩按固定政策舍弃。"""
    atoms = read_structure_safely(path)
    # 占位必须在重建前检查，防止重建后失去原始占位信息而放行坏结构。
    occupancy = atoms.info.get("occupancy", {})
    for species in occupancy.values():
        if len(species) != 1 or any(not math.isfinite(float(value)) or abs(float(value) - 1.0) > 1e-8
                                    for value in species.values()):
            raise IntegrityError("partial_occupancy_not_supported")
    if any(symbol not in ELEMENTS for symbol in atoms.get_chemical_symbols()):
        raise IntegrityError("invalid_chemical_element")
    validate_geometry(atoms, config["minimum_distance"])
    # 本工作流只采用源几何；显式清除约束和磁矩，也覆盖 use_input_cell 回退路径。
    # 后续 writer 仍按旧机制重建 Atoms，不继承源计算器、速度或逐位点附加属性。
    atoms.set_constraint()
    # ASE-ABACUS 可把标量磁矩读成 (N, 1) 数组；先移除旧形状，再建立标准 (N,) 零数组。
    atoms.set_initial_magnetic_moments(None)
    atoms.set_initial_magnetic_moments([0.0] * len(atoms))
    return atoms
