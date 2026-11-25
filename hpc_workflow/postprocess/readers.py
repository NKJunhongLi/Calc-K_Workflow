"""只读解析 zstar 物性；各来源独立失败，保留能够确认有效的部分结果。"""

import json
import math
import os
import re
import stat
from collections.abc import Iterator, Sequence

import numpy as np

from ..common.paths import from_relative, join_local
from ..inputs.models import ELEMENTS
from .models import (COMPONENTS, BornChargeRecord, DataValidationError,
                     DielectricValues, Matrix3)


# 只接受普通十进制和 E/D 指数；拒绝 bool、NaN、Infinity、下划线数字等宽松转换。
NUMBER_PATTERN = r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eEdD][+-]?[0-9]+)?"
NUMBER_RE = re.compile(NUMBER_PATTERN)
BAND_LABEL = "Band gap (eV):"
BAND_RE = re.compile(r"\s*Band gap \(eV\):\s*(" + NUMBER_PATTERN + r")\s*")
_EXPECTED = (DataValidationError, OSError, UnicodeError)


def parse_finite_float(text: str, context: str) -> float:
    """严格解析一个有限文本数值，允许 Fortran D 指数。"""
    if not isinstance(text, str) or not NUMBER_RE.fullmatch(text.strip()):
        raise DataValidationError(f"{context}：不是有效数值 {text!r}")
    number = float(text.strip().replace("D", "E").replace("d", "e"))
    if not math.isfinite(number):
        raise DataValidationError(f"{context}：数值不是有限值 {text!r}")
    return number


def _signature(info: os.stat_result) -> tuple[int, int, int, int]:
    """取读取一致性所需的文件身份、长度与纳秒时间戳，不读取额外产物。"""
    # Windows 的 path stat 与 fstat 在部分 Python 版本对 ctime 含义不同，不能跨 API 比较。
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _product_path(zstar_dir: str, relative: str) -> str:
    """只定位当前计算根内部的直接文件树，拒绝目录链接借入其他任务的产物。"""
    path = from_relative(zstar_dir, relative)
    current = zstar_dir
    # final 文件链接由 _read_text 检查；这里连祖先目录的链接与 Windows junction 一并拒绝。
    for piece in [""] + relative.split("/")[:-1]:
        if piece:
            current = join_local(current, piece)
        if os.path.islink(current) or getattr(os.path, "isjunction", lambda item: False)(current):
            raise DataValidationError(f"{current}：产物目录不能是符号链接或 junction。")
    root_real, path_real = os.path.realpath(zstar_dir), os.path.realpath(path)
    try:
        contained = os.path.normcase(os.path.commonpath((root_real, path_real))) == os.path.normcase(root_real)
    except ValueError:
        # Windows 跨盘符链接没有 commonpath；它同样属于越界，而非程序错误。
        contained = False
    if not contained:
        raise DataValidationError(f"{path}：产物实际位置越出当前 zstar 根目录。")
    return path


def _check_nonempty(path: str) -> None:
    """只验证大型 band.dat 非空及读取时稳定，不将整份能带矩阵载入内存。"""
    before = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_size == 0:
        raise DataValidationError(f"{path}：不是非空普通文件。")
    with open(path, "rb") as handle:
        if _signature(os.fstat(handle.fileno())) != _signature(before) or not handle.read(1):
            raise DataValidationError(f"{path}：读取前文件变化或内容为空。")
        after = os.fstat(handle.fileno())
    if _signature(after) != _signature(before) or _signature(os.stat(path, follow_symlinks=False)) != _signature(before):
        raise DataValidationError(f"{path}：读取过程中产物发生变化，本轮不采纳。")


def _read_text(path: str) -> str:
    """读取非空普通文件，并拒绝符号链接和读取过程中发生的更换/写入。"""
    before = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or before.st_size == 0:
        raise DataValidationError(f"{path}：不是非空普通文件。")
    # 打开后的 fstat 再核对一次，避免 stat 与 open 之间切换到另一文件。
    with open(path, encoding="utf-8-sig") as handle:
        if _signature(os.fstat(handle.fileno())) != _signature(before):
            raise DataValidationError(f"{path}：读取前文件发生变化。")
        content = handle.read()
        after = os.fstat(handle.fileno())
    if _signature(after) != _signature(before) or _signature(os.stat(path, follow_symlinks=False)) != _signature(before):
        raise DataValidationError(f"{path}：读取过程中产物发生变化，本轮不采纳。")
    if not content.strip():
        raise DataValidationError(f"{path}：文件只有空白内容。")
    return content


def iter_data_lines(path: str) -> Iterator[tuple[int, str]]:
    """从稳定文本中逐行去掉空行及 # 注释；返回原始一基行号和有效内容。"""
    for number, line in enumerate(_read_text(path).splitlines(), start=1):
        content = line.split("#", 1)[0].strip()
        if content:
            yield number, content


def parse_band_gap(path: str) -> float:
    """提取且只允许一个 Band gap (eV): 字段，不添加物理筛选阈值。"""
    found = None
    for number, line in enumerate(_read_text(path).splitlines(), start=1):
        if BAND_LABEL not in line:
            continue
        if found is not None:
            raise DataValidationError(f"{path}：第 {number} 行出现重复带隙字段。")
        match = BAND_RE.fullmatch(line)
        if match is None:
            raise DataValidationError(f"{path}：第 {number} 行带隙字段格式错误。")
        found = parse_finite_float(match.group(1), f"{path} 第 {number} 行")
    if found is None:
        raise DataValidationError(f"{path}：未找到 {BAND_LABEL}")
    return found


def matrix_from_tokens(tokens: Sequence[str], context: str) -> Matrix3:
    """把九个有限文本数值按行转换为只读 3×3 矩阵。"""
    if len(tokens) != 9:
        raise DataValidationError(f"{context}：应有九个分量，实际有 {len(tokens)} 个。")
    values = [parse_finite_float(value, f"{context} 分量 {index}")
              for index, value in enumerate(tokens, start=1)]
    matrix = np.array(values, dtype=np.float64).reshape((3, 3))
    matrix.setflags(write=False)
    return matrix


def validate_matrix3(value: object, context: str) -> Matrix3:
    """校验 JSON 数值类型、3×3 形状及有限性，禁止字符串和布尔分量。"""
    try:
        raw = np.asarray(value, dtype=object)
    except (TypeError, ValueError) as exc:
        raise DataValidationError(f"{context}：矩阵结构无效。") from exc
    if raw.shape != (3, 3):
        raise DataValidationError(f"{context}：应为 3×3 矩阵，实际为 {raw.shape}。")
    if any(isinstance(item, (bool, np.bool_))
           or not isinstance(item, (int, float, np.integer, np.floating)) for item in raw.flat):
        raise DataValidationError(f"{context}：分量必须为数值，不能是字符串或布尔值。")
    try:
        matrix = np.array(raw, dtype=np.float64, copy=True)
    except (ValueError, TypeError, OverflowError) as exc:
        raise DataValidationError(f"{context}：无法转换为 float64 矩阵。") from exc
    if not np.isfinite(matrix).all():
        raise DataValidationError(f"{context}：矩阵包含非有限数值。")
    matrix.setflags(write=False)
    return matrix


def parse_electronic_tensor(path: str) -> Matrix3:
    """读取 BORN 的电子张量：支持可选单位因子、九分量行或三行三分量。"""
    lines = list(iter_data_lines(path))
    if not lines:
        raise DataValidationError(f"{path}：缺少电子介电张量。")
    first = 0
    # Phonopy 可在数据前写一个单位换算因子。它不乘电子介电张量。
    if len(lines[0][1].split()) == 1:
        parse_finite_float(lines[0][1], f"{path} 单位换算因子")
        first = 1
    if first >= len(lines):
        raise DataValidationError(f"{path}：单位因子后缺少电子介电张量。")
    tokens = lines[first][1].split()
    if len(tokens) == 3:
        rows = lines[first:first + 3]
        if len(rows) != 3 or any(len(line.split()) != 3 for _, line in rows):
            raise DataValidationError(f"{path}：电子介电张量必须是完整的三行三分量。")
        tokens = [value for _, line in rows for value in line.split()]
    return matrix_from_tokens(tokens, f"{path} 电子介电张量")


def _reject_constant(value: str) -> None:
    """拒绝 JSON 标准以外的 NaN、Infinity 常量。"""
    raise DataValidationError(f"JSON 包含非有限常量：{value}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    """拒绝 JSON 重复键，防止同一 tensor/单位字段被后值静默覆盖。"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise DataValidationError(f"JSON 包含重复键：{key}")
        result[key] = value
    return result


def parse_total_tensor(path: str) -> Matrix3:
    """提取零频、三维、已含电子项的总相对介电张量，不重加电子贡献。"""
    try:
        payload = json.loads(_read_text(path), parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as exc:
        raise DataValidationError(f"{path}：第 {exc.lineno} 行 JSON 格式错误：{exc.msg}") from exc
    if not isinstance(payload, dict) or "tensor" not in payload:
        raise DataValidationError(f"{path}：缺少顶层 tensor 键。")
    # 只接受当前方案定义的物理量，避免将二维极化率、有限频率或离子项误记为总介电。
    frequency = payload.get("frequency_cm-1")
    if isinstance(frequency, bool) or not isinstance(frequency, (int, float)) or frequency != 0:
        raise DataValidationError(f"{path}：静态响应必须明确给出数值零频 frequency_cm-1。")
    expected = {"response_kind": "total relative dielectric tensor", "response_unit": "1",
                "response_convention": "relative permittivity"}
    for key, value in expected.items():
        if payload.get(key) != value:
            raise DataValidationError(f"{path}：{key} 必须为 {value!r}。")
    if payload.get("electronic_response_included") is not True:
        raise DataValidationError(f"{path}：电子响应必须已包含在总静态张量中。")
    # 原生当前输出没有 dimensionality 键；若未来携带，必须与约定的三维响应一致。
    if "dimensionality" in payload and (type(payload["dimensionality"]) is not int or payload["dimensionality"] != 3):
        raise DataValidationError(f"{path}：只支持三维总相对介电响应。")
    return validate_matrix3(payload["tensor"], f"{path} tensor")


def parse_insulation(path: str) -> bool:
    """读取原生绝缘检查结论，不另设带隙阈值，也不把 False 当作格式错误。"""
    try:
        payload = json.loads(_read_text(path), parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    except json.JSONDecodeError as exc:
        raise DataValidationError(f"{path}：第 {exc.lineno} 行 JSON 格式错误：{exc.msg}") from exc
    if not isinstance(payload, dict) or type(payload.get("insulating")) is not bool:
        raise DataValidationError(f"{path}：insulating 必须是明确的 JSON 布尔值。")
    # 原生可附加带隙和阈值，若存在仅检查有限数值，不重新进行物理判定。
    for key in ("gap_eV", "threshold_eV"):
        if key in payload:
            value = payload[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise DataValidationError(f"{path}：{key} 必须是有限数值。")
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise DataValidationError(f"{path}：{key} 必须是有限数值。")
    return payload["insulating"]


def parse_born_charges(path: str) -> tuple[BornChargeRecord, ...]:
    """解析 shared/legacy BEC，保留不连续编号、原顺序与位移行/极化列约定。"""
    records = []
    seen = set()
    for number, content in iter_data_lines(path):
        # legacy 的 No. Atom 是明确表头；不得把任意非数字行当注释忽略。
        if re.match(r"^No\.\s+Atom(?:\s|$)", content, flags=re.IGNORECASE):
            if records:
                raise DataValidationError(f"{path}：第 {number} 行表头出现在数据之后。")
            continue
        match = re.fullmatch(r"\*?\s*([0-9]+)\s+([A-Z][a-z]?)\s+(.+)", content)
        if match is None:
            raise DataValidationError(f"{path}：第 {number} 行不是编号、元素和九分量 BEC。")
        atom, species = int(match.group(1)), match.group(2)
        if atom <= 0 or atom in seen:
            raise DataValidationError(f"{path}：第 {number} 行原子编号必须为唯一正整数。")
        if species not in ELEMENTS:
            raise DataValidationError(f"{path}：第 {number} 行元素符号不存在：{species}")
        matrix = matrix_from_tokens(match.group(3).split(), f"{path} 第 {number} 行")
        records.append(BornChargeRecord(atom, species, matrix))
        seen.add(atom)
    if not records:
        raise DataValidationError(f"{path}：没有不等价原子数据。")
    return tuple(records)


def trace_mean(matrix: Matrix3, context: str) -> float:
    """计算迹除以三；先逐项除三再求和，避免有限大数不必要地溢出。"""
    tensor = validate_matrix3(matrix, context)
    try:
        # fsum 保留抵消精度，并在最终结果真正溢出时明确失败。
        value = math.fsum(float(tensor[index, index]) / 3.0 for index in range(3))
    except (OverflowError, ValueError) as exc:
        raise DataValidationError(f"{context}：迹平均计算溢出。") from exc
    if not math.isfinite(value):
        raise DataValidationError(f"{context}：迹平均不是有限值。")
    return value


def derive_dielectric_values(total: Matrix3, electronic: Matrix3) -> DielectricValues:
    """计算 total-electronic 及迹平均，保留非对称、负号和全部非对角分量。"""
    total = validate_matrix3(total, "总介电张量")
    electronic = validate_matrix3(electronic, "电子介电张量")
    try:
        with np.errstate(over="raise", invalid="raise"):
            ionic = np.subtract(total, electronic)
    except FloatingPointError as exc:
        raise DataValidationError("总介电张量减电子张量发生数值溢出。") from exc
    ionic.setflags(write=False)
    return DielectricValues(total, electronic, ionic, trace_mean(ionic, "离子介电张量"),
                            trace_mean(total, "总介电张量"))


def serialize_born_charges(records: tuple[BornChargeRecord, ...]) -> str:
    """将全部不等价原子写为单个紧凑 JSON 单元格，不改变张量方向。"""
    objects = [{"atom_index": record.atom_index, "species": record.species,
                "z_displacement_polarization": record.z_displacement_polarization.tolist()}
               for record in records]
    return json.dumps(objects, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _band_info_path(zstar_dir: str, config: dict) -> tuple[str, str]:
    """在配置的 Out 范围内定位唯一同目录文件对；单独 info 可保留失败任务带隙。"""
    root = _product_path(zstar_dir, config.get("band_relative_root", "0.no-move/pyatb-band/Out") + "/band_info.dat")
    root = os.path.dirname(root)
    infos = []
    if os.path.isdir(root) and not os.path.islink(root):
        for directory, subdirs, files in os.walk(root, followlinks=False):
            # 不追随符号链接目录，不能从当前任务借入别处的结果。
            subdirs[:] = sorted(name for name in subdirs
                                if not os.path.islink(join_local(directory, name))
                                and not getattr(os.path, "isjunction", lambda item: False)(join_local(directory, name)))
            if "band_info.dat" in files:
                infos.append(join_local(directory, "band_info.dat"))
    pairs = [path for path in infos if os.path.isfile(join_local(os.path.dirname(path), "band.dat"))
             and not os.path.islink(path) and not os.path.islink(join_local(os.path.dirname(path), "band.dat"))
             and os.path.getsize(path) > 0 and os.path.getsize(join_local(os.path.dirname(path), "band.dat")) > 0]
    if len(pairs) > 1 or (not pairs and len(infos) > 1):
        raise DataValidationError(f"{root}：存在多套无法唯一定位的能带结果。")
    if pairs:
        path = pairs[0]
    elif infos:
        path = infos[0]
    else:
        # 返回默认位置以得到明确、可追溯的缺文件诊断。
        path = join_local(root, "Band_Structure", "band_info.dat")
    return path, join_local(os.path.dirname(path), "band.dat")


def _matrix_columns(kind: str, matrix: Matrix3) -> dict[str, float]:
    """将矩阵按旧版固定行优先列名展平为普通 float。"""
    return {f"dielectric_{kind}_{name}": float(value)
            for name, value in zip(COMPONENTS, matrix.ravel(order="C"))}


def read_task_data(zstar_dir: str, config: dict) -> dict:
    """逐来源采集部分结果和绝缘检查结论；不决定任务终态或修改产物。"""
    values, errors, evidence = {}, [], []
    insulation = None
    # 每个字段来源独立处理：虚频等终止可能缺总静态响应，但既有带隙/BEC 不应丢失。
    try:
        band_info, band_data = _band_info_path(zstar_dir, config)
        evidence.extend((band_info, band_data))
        try:
            values["band_gap_ev"] = parse_band_gap(band_info)
        except _EXPECTED as exc:
            errors.append("带隙读取失败：" + str(exc))
        try:
            _check_nonempty(band_data)
        except _EXPECTED as exc:
            errors.append("能带文件不完整：" + str(exc))
    except _EXPECTED as exc:
        errors.append("能带定位失败：" + str(exc))

    # 原生绝缘结论独立读取；False 是正常的物理拒绝证据，不制造另一条格式错误。
    path = join_local(zstar_dir, "0.no-move", "zstar_insulation.json")
    evidence.append(path)
    try:
        insulation = parse_insulation(_product_path(zstar_dir, "0.no-move/zstar_insulation.json"))
    except _EXPECTED as exc:
        errors.append("绝缘检查结论读取失败：" + str(exc))

    electronic = total = None
    path = join_local(zstar_dir, "BORN")
    evidence.append(path)
    try:
        electronic = parse_electronic_tensor(_product_path(zstar_dir, "BORN"))
        values.update(_matrix_columns("electronic", electronic))
    except _EXPECTED as exc:
        errors.append("电子介电读取失败：" + str(exc))
    path = join_local(zstar_dir, "dielectric_response", "static_response.json")
    evidence.append(path)
    try:
        total = parse_total_tensor(_product_path(zstar_dir, "dielectric_response/static_response.json"))
        values.update(_matrix_columns("total", total))
        # 总迹平均独立于电子项，电子数据缺失时仍然能够正确排序。
        values["dielectric_total_trace_mean"] = trace_mean(total, "总介电张量")
    except _EXPECTED as exc:
        errors.append("总静态介电读取失败：" + str(exc))
    if total is not None and electronic is not None:
        try:
            dielectric = derive_dielectric_values(total, electronic)
            values.update(_matrix_columns("ionic", dielectric.ionic))
            values["dielectric_ionic_trace_mean"] = dielectric.ionic_trace_mean
        except DataValidationError as exc:
            errors.append("离子介电计算失败：" + str(exc))

    # BEC.dat 是成功完整性的必要产物；数据输出使用全部不等价原子的 BEC.rep.dat。
    for name in ("BEC.dat", "BEC.rep.dat"):
        path = join_local(zstar_dir, name)
        evidence.append(path)
        try:
            records = parse_born_charges(_product_path(zstar_dir, name))
            if name == "BEC.rep.dat":
                values["bec_inequivalent_atom_count"] = len(records)
                values["born_effective_charges_json"] = serialize_born_charges(records)
        except _EXPECTED as exc:
            errors.append(f"{name} 读取失败：{exc}")
    return {"values": values, "errors": errors, "evidence": evidence, "insulation": insulation}
