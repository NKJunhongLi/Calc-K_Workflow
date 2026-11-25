"""从新版前处理封存记录读取 MP/MJM 身份；不推测编号、不导入旧散装 CSV。"""

import math
import os
import re

from ..common.errors import IntegrityError
from ..common.paths import join_local
from ..inputs.models import ELEMENTS
from ..preprocess.writer import inspect_existing


NUMBER_RE = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eEdD][+-]?[0-9]+)?")


def _optional_integer(value: object, label: str, positive: bool = False) -> int | str:
    """把可空编号转成整数；缺省返回空字符串，非法形式抛 IntegrityError。"""
    # 编号不能用 float() 猜测：1.0、指数、负数和布尔值都不是有效源记录编号。
    if value is None or value == "":
        return ""
    text = str(value)
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", text):
        raise IntegrityError(f"{label} 必须是 ASCII 十进制整数或空值")
    result = int(text)
    if positive and result == 0:
        raise IntegrityError(f"{label} 必须大于零")
    return result


def _nominal_values(source_metadata: dict) -> tuple[str, float | str]:
    """读取 nominal k 可用性和有限数值；未知、False、True 三种语义分别保留。"""
    available = source_metadata.get("nominal_k_available")
    value = source_metadata.get("nominal_k_value")
    missing_value = value is None or value == ""
    # 没有提供预测值与明确声明不可用不同；CSV 不用 False 冒充未知。
    if available is None or available == "":
        if not missing_value:
            raise IntegrityError("nominal_k_value 非空，但 nominal_k_available 缺失")
        return "", ""
    if isinstance(available, bool):
        flag = available
    elif isinstance(available, str) and available.strip().casefold() in ("true", "false"):
        flag = available.strip().casefold() == "true"
    else:
        raise IntegrityError("nominal_k_available 必须是 True/False 或空值")
    if not flag:
        if not missing_value:
            raise IntegrityError("nominal_k_available=False，但 nominal_k_value 非空")
        return "False", ""
    # 接受 CSV 常见科学计数法，但不接受 bool、NaN、Infinity 或没有数值的 True。
    if missing_value or isinstance(value, bool):
        raise IntegrityError("nominal_k_available=True，必须提供有限 nominal_k_value")
    text = str(value).strip()
    if not NUMBER_RE.fullmatch(text):
        raise IntegrityError("nominal_k_value 不是有效十进制或科学计数法数值")
    try:
        number = float(text.replace("D", "e").replace("d", "e"))
    except (TypeError, ValueError, OverflowError) as exc:
        raise IntegrityError("nominal_k_value 不是有效数值") from exc
    if not math.isfinite(number):
        raise IntegrityError("nominal_k_value 必须是有限数值")
    return "True", number


def resolve_metadata(task_dir: str) -> dict:
    """验证新版任务封存记录，返回可直接用于 CSV 的元数据；失败抛 IntegrityError。"""
    path = join_local(task_dir, "task_manifest.json")
    if os.path.islink(path):
        raise IntegrityError("task_manifest.json 不接受符号链接")
    # 复用当前协议的记录与三份普通非空产物检查；不重建几何映射或读取资源文件。
    # None 表示核对记录自身，不将任意当前模板当成该历史任务的输入说明。
    manifest = inspect_existing(task_dir, None, os.path.basename(task_dir))
    if manifest is None:
        raise IntegrityError("正式前处理任务不存在")
    if manifest["task_id"] != os.path.basename(task_dir):
        raise IntegrityError("正式任务目录名与封存 task_id 不一致")
    source = manifest["input_source"]
    if not manifest["dataset_namespace"] or not manifest["source_record_id"]:
        raise IntegrityError("数据集命名空间和源记录 ID 不能留空")
    formula = manifest.get("formula")
    elements = manifest.get("elements")
    if not isinstance(formula, str) or not formula.strip():
        raise IntegrityError("前处理记录缺少非空 formula")
    if (not isinstance(elements, list) or not elements
            or any(not isinstance(item, str) or item not in ELEMENTS for item in elements)
            or len(elements) != len(set(elements))):
        raise IntegrityError("前处理记录 elements 必须是无重复的真实元素列表")
    source_metadata = manifest.get("source_metadata", {})
    if not isinstance(source_metadata, dict):
        raise IntegrityError("source_metadata 必须是对象")
    available, nominal = _nominal_values(source_metadata)
    priority = _optional_integer(manifest.get("dft_priority"), "dft_priority", positive=True)
    # MP 本来没有 global_sample_id；即使记录里有意外值，也绝不能从 MP ID 推导或写入。
    sample = "" if source == "MP" else _optional_integer(manifest.get("global_sample_id"), "global_sample_id")
    if source == "MJM" and (sample == "" or priority == ""):
        raise IntegrityError("MJM 任务必须保留 global_sample_id 和 dft_priority")
    return {
        "dft_priority": priority, "global_sample_id": sample,
        "formula": formula, "elements": "-".join(elements),
        "task_name": manifest["task_id"], "nominal_k_available": available,
        "nominal_k_value": nominal, "input_source": source,
        "dataset_namespace": manifest["dataset_namespace"],
        "source_record_id": manifest["source_record_id"],
    }
