"""信任 spglib 的一次原胞转换，只检查返回几何和组成的基本合法性。"""

from collections import Counter
from dataclasses import dataclass

from ..common.errors import IntegrityError
from .validation import validate_geometry


@dataclass(frozen=True)
class PrimitiveResult:
    """一次转换的结构、显式回退标志和源胞原子数；不包含原子映射。"""

    atoms: object
    used_input_cell_fallback: bool
    source_nsites: int


def make_primitive(atoms: object, config: dict) -> PrimitiveResult:
    """一次调用配置指定的 spglib 方法，返回 PrimitiveResult，失败抛完整性错误。"""
    import numpy as np
    import spglib
    from ase import Atoms

    cell = (atoms.cell.array, atoms.get_scaled_positions(wrap=True), atoms.numbers)
    if config["primitive_method"] == "find_primitive":
        result = spglib.find_primitive(cell, symprec=config["symprec"], angle_tolerance=config["angle_tolerance"])
    elif config["primitive_method"] == "standardize_cell":
        result = spglib.standardize_cell(cell, to_primitive=True, no_idealize=config["standardize_no_idealize"],
                                        symprec=config["symprec"], angle_tolerance=config["angle_tolerance"])
    else:
        raise IntegrityError("unsupported_primitive_method")
    # 返回 None 仍遵循用户明确的失败策略，不静默换另一套转换方法。
    fallback = result is None
    if fallback:
        if config["primitive_failure_policy"] != "use_input_cell":
            raise IntegrityError("primitive_cell_not_found")
        primitive = atoms.copy()
    else:
        if not isinstance(result, (tuple, list)) or len(result) != 3:
            raise IntegrityError("invalid_primitive_result")
        lattice, positions, numbers = map(np.asarray, result)
        if (lattice.shape != (3, 3) or numbers.ndim != 1 or not len(numbers)
                or positions.shape != (len(numbers), 3) or not np.issubdtype(numbers.dtype, np.integer)
                or not np.isfinite(lattice).all() or not np.isfinite(positions).all()
                or (numbers < 1).any() or (numbers > 118).any()):
            raise IntegrityError("invalid_primitive_arrays")
        primitive = Atoms(numbers=numbers, scaled_positions=positions, cell=lattice, pbc=True)
        primitive.wrap()
    # 化学计量允许整倍数缩胞，不做源位点匹配、仿射变换或第二次对称性识别。
    source_counts, output_counts = Counter(atoms.numbers), Counter(primitive.numbers)
    if (not len(primitive) or len(atoms) % len(primitive)
            or set(source_counts) != set(output_counts)
            or any(source_counts[z] * len(primitive) != output_counts[z] * len(atoms) for z in source_counts)):
        raise IntegrityError("primitive_composition_mismatch")
    validate_geometry(primitive, config["minimum_distance"])
    return PrimitiveResult(primitive, fallback, len(atoms))
