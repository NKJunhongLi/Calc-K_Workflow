"""保持旧39列，追加来源/版本与备注；完整写出后以不覆盖方式发布单一结果。"""

import csv
import math
import os
import uuid

from ..common.errors import ConflictError, IntegrityError
from ..common.paths import ensure_host_path


COMPONENTS = ("xx", "xy", "xz", "yx", "yy", "yz", "zx", "zy", "zz")
EXTRA_FIELDS = ("input_source", "dataset_namespace", "source_record_id", "zstar_relative_path",
                "zstar_input_revision_id", "provenance_status", "remarks")
METADATA_FIELDS = ("dft_priority", "global_sample_id", "formula", "elements", "task_name",
                   "nominal_k_available", "nominal_k_value")


def build_fieldnames() -> tuple[str, ...]:
    """构造46个唯一列名；旧39列及原分量顺序不变，remarks 永远最后。"""
    fields = ("dft_priority", "global_sample_id", "formula", "elements", "task_name",
              "band_gap_ev", "nominal_k_available", "nominal_k_value",
              "dielectric_ionic_trace_mean", "dielectric_total_trace_mean")
    fields += tuple(f"dielectric_{kind}_{component}" for kind in ("total", "ionic", "electronic") for component in COMPONENTS)
    fields += ("bec_inequivalent_atom_count", "born_effective_charges_json") + EXTRA_FIELDS
    if len(fields) != 46 or len(set(fields)) != 46:
        raise RuntimeError("内部错误：后处理 CSV 必须恰好有46个唯一字段。")
    return fields


def build_csv_row(task: dict, values: dict, remarks: str = "") -> dict:
    """合并可信元数据与独立物性；没有结果留空，不用0、None或NaN冒充。"""
    row = {key: "" for key in build_fieldnames()}
    metadata = task.get("metadata", {})
    for key in row:
        if key in metadata and metadata[key] is not None:
            row[key] = metadata[key]
    unknown = set(values) - set(row)
    protected = set(values).intersection((*EXTRA_FIELDS, *METADATA_FIELDS))
    if unknown or protected:
        raise IntegrityError(f"物性解析器返回未知列或覆盖元数据字段：{sorted(unknown or protected)}")
    row.update(values)
    # task_name 是实际稳定目录名；源ID仍单独保留，不从名称中的数字猜样本编号。
    row["task_name"] = task["task_name"]
    row["zstar_input_revision_id"] = task.get("expected_input_revision_id") or ""
    row["provenance_status"] = task.get("provenance_status") or "unavailable"
    row["remarks"] = str(remarks)
    if row["input_source"] == "MP":
        row["global_sample_id"] = ""
    if isinstance(row["nominal_k_available"], bool):
        row["nominal_k_available"] = "True" if row["nominal_k_available"] else "False"
    return row


def _sort_key(row: dict) -> tuple:
    """按有限总介电trace均值降序，缺失置底；相同值按稳定身份确定顺序。"""
    value = row["dielectric_total_trace_mean"]
    identity = tuple(str(row.get(key, "")) for key in ("input_source", "dataset_namespace", "source_record_id", "task_name"))
    if value == "":
        return (1, 0.0, *identity)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise IntegrityError("总介电trace平均值必须为有限数值或空单元格。")
    return (0, -value, *identity)


def _safe_output_parent(path: str) -> None:
    """拒绝输出祖先或目标链接；仅在确认发布时创建真实父目录。"""
    current = os.path.abspath(path)
    while True:
        if os.path.islink(current) or (hasattr(os.path, "isjunction") and os.path.isjunction(current)):
            raise IntegrityError(f"CSV 输出路径不接受符号链接或目录联接：{current}")
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    os.makedirs(os.path.dirname(path), exist_ok=True)


def write_results_csv(output_path: str, rows: list[dict]) -> int:
    """排序并独占发布完整UTF-8 BOM CSV；目标存在或发布失败不覆盖原文件。"""
    path = ensure_host_path(output_path)
    if os.path.lexists(path):
        raise ConflictError(f"结果CSV已存在，拒绝覆盖：{path}")
    fields = build_fieldnames()
    # 在创建任何文件前验证整表列和非有限值，内部错误不能留下一张貌似有效的半表。
    if any(set(row) != set(fields) for row in rows):
        raise IntegrityError("结果行与46列表头不一致。")
    for row in rows:
        for value in row.values():
            if value is None or (isinstance(value, float) and not math.isfinite(value)):
                raise IntegrityError("结果CSV含None或非有限数值；缺失数据应为空字符串。")
    ordered = sorted(rows, key=_sort_key)
    _safe_output_parent(path)
    temporary = os.path.join(os.path.dirname(path), "." + os.path.basename(path) + "." + uuid.uuid4().hex + ".tmp")
    try:
        with open(temporary, "x", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
            writer.writeheader()
            writer.writerows(ordered)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows 的 rename 是原子且目标存在时失败；不依赖可能被策略禁用的硬链接。
        # POSIX rename 会覆盖目标，因此使用同目录硬链接独占发布，绝不降级为覆盖。
        try:
            if os.name == "nt":
                os.rename(temporary, path)
            else:
                os.link(temporary, path)
        except FileExistsError as exc:
            raise ConflictError(f"发布期间CSV已被另一进程创建，保留已有结果：{path}") from exc
    finally:
        if os.path.lexists(temporary):
            os.unlink(temporary)
    return len(ordered)
