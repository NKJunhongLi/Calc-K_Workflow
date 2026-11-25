"""单层发现本地文件，按来源和完整文件名规划稳定任务。"""

import os
from urllib.parse import quote

from ..common.errors import ConfigError
from ..common.paths import join_local
from ..inputs.models import InputRecord


def discover_inputs(structures_root: str) -> tuple[list[InputRecord], list[dict]]:
    """枚举直接普通文件，不过滤名称、不递归；返回来源记录和跳过项。"""
    records, skipped = [], []
    # 先完成单层枚举并排序，再处理候选；写出不会改变本轮候选集合。
    with os.scandir(structures_root) as entries:
        ordered = sorted(entries, key=lambda entry: entry.name)
    for entry in ordered:
        # 不跟随文件链接和 Windows 联接，避免借读指定父目录之外的结构。
        if entry.is_symlink() or getattr(os.path, "isjunction", lambda path: False)(entry.path):
            skipped.append({"name": entry.name, "reason": "link"})
        elif entry.is_file(follow_symlinks=False):
            records.append(InputRecord("LOCAL", entry.name, entry.name))
        else:
            skipped.append({"name": entry.name, "reason": "directory_or_special_file"})
    return records, skipped


def task_id_for(record: object, namespace: str) -> str:
    """返回可逆转义的稳定目录名；过长或非法身份明确报错。"""
    # 保留旧记录的命名校验能力供既有下游夹具复用；新入口只生成 LOCAL。
    if record.input_source not in {"LOCAL", "MP", "MJM"} or not isinstance(namespace, str) or not namespace.strip():
        raise ConfigError("任务来源和数据集命名空间无效")
    if not isinstance(record.source_record_id, str) or not record.source_record_id:
        raise ConfigError("源记录 ID 必须为非空字符串")
    # 下划线避免拼接碰撞，点号避免合法来源被识别成 staging；不使用内容摘要。
    parts = [quote(value, safe="-").replace("_", "%5F").replace(".", "%2E")
             for value in (namespace, record.source_record_id)]
    name = record.input_source.lower() + "__" + "__".join(parts)
    if len(name) > 180:
        raise ConfigError("任务身份编码过长，请缩短 dataset_namespace 或源文件名")
    return name


def build_plans(records: list, config: dict) -> list[dict]:
    """生成平铺任务路径；大小写等价身份整批拒绝，不创建目录。"""
    plans, used = [], set()
    # 文件名保留扩展名，不按化学式或遍历序号重新编号。
    for record in records:
        try:
            task_id = task_id_for(record, config["dataset_namespace"])
            plan = {"task_id": task_id, "record": record,
                    "task_directory": join_local(config["work_root"], task_id),
                    "source_file": join_local(config["structures_root"], record.source_file)}
        except ConfigError as exc:
            # 编码过长或不能安全拼接的名字只影响该文件；错误条目不再重复拼接坏路径。
            plans.append({"task_id": "", "record": record, "planning_error": str(exc)})
            continue
        if task_id.casefold() in used:
            raise ConfigError(f"任务身份重复：{task_id}")
        used.add(task_id.casefold())
        plans.append(plan)
    return plans
