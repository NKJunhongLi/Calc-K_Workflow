"""以 relaxed 正式空间群理想化原胞；保留容差诊断及独立晶格质量检查。"""

import io
import math
import re

from ..common.errors import IntegrityError
from ..preprocess.validation import validate_geometry


SYMPREC = 1e-3


def read_structure(path: str) -> object:
    """读取 ABACUS STRU 为 ASE Atoms；不访问资源路径，也不调用 ASE 写出器。"""
    from ase.io import read

    # 一次读取固定输入快照，避免解析与磁盘内容不一致；错误由调用者归为输入失败。
    with open(path, encoding="utf-8-sig") as handle:
        return read(io.StringIO(handle.read()), format="abacus")


def symmetry_group(atoms: object, symprec: float = SYMPREC) -> int:
    """返回指定 Å 容差识别的空间群编号；识别失败抛 IntegrityError。"""
    import spglib

    # 不传初始群作为约束；识别出的 relaxed 群决定正式理想化目标。
    try:
        dataset = spglib.get_symmetry_dataset(
            (atoms.cell.array, atoms.get_scaled_positions(wrap=True), atoms.numbers), symprec=symprec)
    except Exception as exc:
        raise IntegrityError("spacegroup_identification_failed:" + str(exc)) from exc
    if dataset is None:
        raise IntegrityError("spacegroup_identification_failed")
    return int(dataset.number)


def sanitize_structure(initial: object, relaxed: object, config: dict) -> tuple:
    """返回 (理想化 ASE Atoms, 简明摘要 dict)，硬 QC 失败则拒绝输入。"""
    from collections import Counter
    import numpy as np
    import spglib
    from ase import Atoms

    # 首先检查真实 relaxed 输入；不能让理想化算法替坏坐标掩盖错误。
    minimum = config.get("minimum_distance", 0.1)
    validate_geometry(initial, minimum)
    validate_geometry(relaxed, minimum)
    if sorted(initial.numbers.tolist()) != sorted(relaxed.numbers.tolist()):
        raise IntegrityError("initial_relaxed_composition_mismatch")
    # 初始群只用于比较；诊断识别失败不能代替 relaxed 正式群作决定。
    try:
        initial_group = symmetry_group(initial)
    except IntegrityError:
        initial_group = None
    scan, scan_errors = {}, {}
    tolerances = sorted(set((1e-5, 1e-4, SYMPREC, 1e-2, 1e-1))
                        | set(config.get("symmetry_scan_tolerances", ())))
    for tolerance in tolerances:
        try:
            scan[str(tolerance)] = symmetry_group(relaxed, tolerance)
        except IntegrityError as exc:
            scan[str(tolerance)] = None
            scan_errors[str(tolerance)] = str(exc)
    # 正式值可复用本次扫描结果；宽松扫描绝不改变实际标准化容差。
    relaxed_group = scan[str(SYMPREC)]
    if relaxed_group is None:
        raise IntegrityError("formal_spacegroup_identification_failed")
    cell = (relaxed.cell.array, relaxed.get_scaled_positions(wrap=True), relaxed.numbers)
    standardized = spglib.standardize_cell(cell, to_primitive=True, no_idealize=False,
                                          symprec=SYMPREC, angle_tolerance=-1)
    if standardized is None:
        raise IntegrityError("symmetry_standardization_failed")
    lattice, positions, numbers = map(np.asarray, standardized)
    if (lattice.shape != (3, 3) or numbers.ndim != 1 or not len(numbers)
            or positions.shape != (len(numbers), 3)
            or not np.isfinite(lattice).all() or not np.isfinite(positions).all()
            or numbers.dtype.kind == "b" or not np.isfinite(numbers).all() or not np.equal(numbers, np.floor(numbers)).all()
            or np.any(numbers < 1) or np.any(numbers > 118)):
        raise IntegrityError("invalid_standardized_cell_arrays")
    source_counts, output_counts = Counter(relaxed.numbers), Counter(numbers)
    if (len(relaxed) % len(numbers) or set(source_counts) != set(output_counts)
            or any(source_counts[key] != output_counts[key] * (len(relaxed) // len(numbers)) for key in source_counts)):
        raise IntegrityError("standardized_composition_ratio_mismatch")
    output = Atoms(numbers=numbers, scaled_positions=positions, cell=lattice, pbc=True)
    validate_geometry(output, minimum)
    # 目标群来自 relaxed 的正式识别；输出不再次识别群或重新确认原胞。
    output_group = relaxed_group

    # 独立求同一标准基底下“不理想化”的原胞，比较理想化前后晶格；不能用几何映射
    # 的 affine 代替：其 Cartesian 直接投影候选可能恒等，从而把真实晶格修正隐藏掉。
    raw_primitive = spglib.standardize_cell(cell, to_primitive=True, no_idealize=True,
                                          symprec=SYMPREC, angle_tolerance=-1)
    if raw_primitive is None or len(raw_primitive[2]) != len(output):
        raise IntegrityError("cannot_compare_idealized_primitive_lattices")
    raw_lattice = np.asarray(raw_primitive[0])
    if raw_lattice.shape != (3, 3) or not np.isfinite(raw_lattice).all():
        raise IntegrityError("invalid_strain_reference_lattice")
    deformation = np.linalg.inv(raw_lattice) @ output.cell.array
    singular = np.linalg.svd(deformation, compute_uv=False)
    strain = float(np.max(np.abs(singular - 1)))
    # 基矢换向与旋转不能被当成应变；奇异值消除纯旋转后再检查真实形变量。
    if not np.isfinite(strain) or strain > config.get("max_symmetry_strain", 0.01):
        raise IntegrityError("symmetry_lattice_correction_exceeds_qc_limit")
    # 改群及宽松容差恢复初始群都只是 FLAG，保留全部扫描后继续正式群计算。
    changed = initial_group is not None and initial_group != relaxed_group
    restored = [float(value) for value, group in scan.items()
                if float(value) > SYMPREC and initial_group is not None and group == initial_group]
    flags = []
    if initial_group is None:
        flags.append("initial_group_unavailable")
    if changed:
        flags.append("symmetry_changed")
    if changed and restored:
        flags.append("loose_tolerance_restores_initial_group")
    if scan_errors:
        flags.append("diagnostic_scan_failed")
    summary = {"initial_group": initial_group, "relaxed_group": relaxed_group,
               "output_group": output_group, "output_group_basis": "declared_formal_target",
               "symprec": SYMPREC,
               "flag": "symmetry_changed" if changed else None, "flags": flags,
               "qc": "passed", "max_strain": strain, "symmetry_scan": scan,
               "loose_matches_initial": restored if changed else [],
               "diagnostic_errors": scan_errors}
    return output, summary


def _normalized_site_fields(fields: list[str], default_magnetism: float) -> list[str]:
    """解析并规范化单个位点附加列；保留移动标志及可无损传递的标量磁矩。"""
    # ABACUS 允许省略整个移动字段（默认自由移动），也允许仅省略 m 前缀。
    # 显式 000 必须原样保留；这里不把受约束结构强制改成全方向自由移动。
    movement = ["1", "1", "1"]
    magnetism = default_magnetism
    cursor = 0
    movement_seen = magnetism_seen = False
    if fields and fields[0] in ("0", "1"):
        movement = fields[:3]
        if len(movement) != 3 or any(value not in ("0", "1") for value in movement):
            raise IntegrityError("invalid_atomic_movement_flags")
        cursor = 3
        movement_seen = True

    # 按关键字划定各字段边界，不把 mag 后的移动/速度字段误算成磁矩分量。
    # 识别别名只用于正确理解语义，不意味着本轮增加速度或非共线属性支持。
    keywords = {"m", "v", "vel", "velocity", "mag", "magmom", "angle1", "angle2"}
    while cursor < len(fields):
        keyword = fields[cursor].casefold()
        if keyword in {"v", "vel", "velocity", "angle1", "angle2"}:
            raise IntegrityError("unsupported_vector_site_metadata")
        if keyword not in {"m", "mag", "magmom"}:
            raise IntegrityError("unsupported_site_metadata:" + fields[cursor])
        end = cursor + 1
        while end < len(fields) and fields[end].casefold() not in keywords:
            end += 1
        values = fields[cursor + 1:end]
        if keyword == "m":
            if movement_seen or len(values) != 3 or any(value not in ("0", "1") for value in values):
                raise IntegrityError("invalid_atomic_movement_flags")
            movement = values
            movement_seen = True
        else:
            # 三分量（包括全零）仍按非共线字段拒绝，不截取第一分量伪装成标量。
            if len(values) == 3:
                raise IntegrityError("unsupported_vector_magnetism")
            if magnetism_seen or len(values) != 1:
                raise IntegrityError("invalid_scalar_magnetism")
            magnetism = float(values[0])
            if not math.isfinite(magnetism):
                raise IntegrityError("nonfinite_scalar_magnetism")
            magnetism_seen = True
        cursor = end

    # 原规则只接受三个方向同开关；混合方向不能直接搬到标准化后的新晶胞基底。
    if len(set(movement)) > 1:
        raise IntegrityError("directional_constraint_requires_frame_mapping")
    normalized = ["m", *movement]
    # 同元素行按含义比较：磁矩别名、磁矩数值的1/1.0、显式默认值与省略应等价。
    # 移动标志仍只接受字面0/1，不据此扩展移动字段的数值语法。
    # 默认磁矩仍由元素块保存，只有逐位点覆盖值才需要额外写出 mag。
    if magnetism != default_magnetism:
        normalized.extend(("mag", format(magnetism, ".17g")))
    return normalized


def _site_metadata(text: str) -> dict:
    """读取元素顺序、默认磁矩及规范化位点字段，不复验晶格、坐标或原胞转换。"""
    # 去掉普通注释后按已有元素块读取；资源头及结构几何由各自既有流程处理。
    clean = re.sub(r"#.*|//.*", "", text)
    tail = clean.split("ATOMIC_POSITIONS", 1)[1]
    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    index = 1
    metadata = {}
    while index < len(lines):
        symbol, magnetism, count = lines[index], lines[index + 1], int(lines[index + 2])
        atom_lines = lines[index + 3:index + 3 + count]
        if len(atom_lines) != count:
            raise IntegrityError("invalid_atomic_position_metadata")
        # 元素块默认值参与逐位点覆盖计算，非有限标量不能传递给后续 DFT。
        default_magnetism = float(magnetism)
        if not math.isfinite(default_magnetism):
            raise IntegrityError("nonfinite_scalar_magnetism")
        rows = [_normalized_site_fields(line.split()[3:], default_magnetism) for line in atom_lines]
        metadata[symbol] = (format(default_magnetism, ".17g"), rows)
        index += 3 + count
    return metadata


def sanitized_stru_text(output: object, resource_text: str, relaxed_text: str, summary: dict) -> str:
    """按原资源元素顺序序列化 STRU 字符串，保持绝对资源路径原样且不探测文件。"""
    from ase.units import Bohr

    # 资源区段逐字保留，包含官方或私人 PP/轨道文本；不重新拼接、更不访问它们。
    prefix = re.split(r"(?m)^\s*LATTICE_CONSTANT\s*(?:#.*)?$", resource_text, maxsplit=1)[0]
    if "ATOMIC_SPECIES" not in prefix or len(prefix) == len(resource_text):
        raise IntegrityError("missing_atomic_resources_or_lattice_constant")
    initial_meta = _site_metadata(resource_text)
    relaxed_meta = _site_metadata(relaxed_text)
    order = list(initial_meta)
    if set(order) != set(output.get_chemical_symbols()):
        raise IntegrityError("resource_species_mismatch")
    # 字段已经规范化，按元素比较实际移动/标量覆盖设置；无逐原子映射时，
    # 不同位点属性仍不能按旧行号猜测或取第一条记录强行覆盖。
    extras = {}
    for symbol, (_, rows) in relaxed_meta.items():
        if not rows or any(row != rows[0] for row in rows[1:]):
            raise IntegrityError("site_dependent_metadata_requires_mapping")
        extras[symbol] = rows[0]
    symbols = output.get_chemical_symbols()
    scaled_positions = output.get_scaled_positions(wrap=True)
    written_order = [index for symbol in order for index, value in enumerate(symbols) if value == symbol]
    lines = [prefix.rstrip(), "", "LATTICE_CONSTANT", format(1 / Bohr, ".17g"), "", "LATTICE_VECTORS"]
    lines.extend(" ".join(format(float(value), ".17g") for value in row) for row in output.cell.array)
    lines.extend(["", "ATOMIC_POSITIONS", "Direct", ""])
    for symbol in order:
        indices = [index for index in written_order if symbols[index] == symbol]
        lines.extend([symbol, relaxed_meta[symbol][0], str(len(indices))])
        for output_index in indices:
            extra = extras[symbol]
            coordinate = scaled_positions[output_index]
            lines.append(" ".join([*(format(float(value), ".17g") for value in coordinate), *extra]))
        lines.append("")
    text = "\n".join(lines) + "\n"

    return text
