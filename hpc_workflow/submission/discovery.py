"""只读发现任务、核对活动作业与输入关联；不修改旧前处理产物。"""

import os

from ..common.errors import IntegrityError
from ..common.paths import from_relative, join_local, to_relative
from .models import ACTIVE_SLURM_STATES
from .slurm import query_jobs
from .state import list_plans, load_plan, read_jobs, read_progress


def checked_task_directory(work_root: str, relative: str) -> str:
    """校验任务及路径祖先没有软链接，且实际路径仍在工作根之内。"""
    directory = from_relative(work_root, relative)
    real_root = os.path.realpath(work_root)
    try:
        contained = os.path.commonpath([real_root, os.path.realpath(directory)]) == real_root
    except ValueError:
        contained = False
    if not contained:
        raise IntegrityError(f"任务实际路径越出 work_root：{relative}")
    current = directory
    while True:
        if os.path.islink(current):
            raise IntegrityError(f"任务路径不接受符号链接：{relative}")
        if os.path.normcase(current) == os.path.normcase(os.path.normpath(work_root)):
            break
        parent = os.path.dirname(current)
        if parent == current:
            raise IntegrityError("任务祖先不能定位到 work_root")
        current = parent
    return directory


def task_directories(work_root: str) -> list[str]:
    """发现工作根的直接任务子目录，返回稳定排序的 POSIX 相对路径。"""
    if not os.path.isdir(work_root):
        raise IntegrityError(f"任务根目录不存在：{work_root}")
    result = []
    for name in sorted(os.listdir(work_root)):
        path = join_local(work_root, name)
        if name.startswith(".") or ".staging-" in name or not os.path.isdir(path):
            continue
        # 旧散装目录仅供输入失败报告；staging 永不当作正式任务，不扫描任意深层目录。
        if os.path.isfile(join_local(path, "task_manifest.json")) or os.path.isfile(join_local(path, "STRU")):
            result.append(to_relative(path, work_root))
    return result


def previous_tasks(work_root: str, stage: str, config: dict | None = None) -> dict[str, dict]:
    """按运行先后读取每个结构最近关联的输入声明版本和已结束状态。"""
    result = {}
    for plan_path in list_plans(work_root, stage):
        plan = load_plan(plan_path)
        if plan.get("schema_version") != 2 or plan.get("workflow_version") != "0.0.2":
            continue
        # 同一任务不同计算子目录分别关联；旧协议记录从不取得当前版本复用资格。
        for task in plan["tasks"]:
            if config is not None and task.get("calculation_subdir") != config.get(stage + "_subdir", "cell-relax" if stage == "relax" else "zstar"):
                continue
            if not task.get("input_revision_id") and result.get(task["path"], {}).get("input_revision_id"):
                continue
            result[task["path"]] = dict(task)
            result[task["path"]]["_execution"] = plan["execution"]
        for group in plan["groups"]:
            progress = read_progress(plan_path, group["batch"])
            if progress.get("schema_version") != 2 or progress.get("workflow_version") != "0.0.2":
                continue
            from ..calculations.relax import REVISION_FIELDS
            for task in progress.get("finished", []):
                path = task.get("path")
                if path in result and all(task.get(key) == result[path].get(key) for key in REVISION_FIELDS):
                    result[path].update(task)
    return result


def active_tasks(work_root: str, stage: str, config: dict, exclude_plan: str | None = None) -> dict[str, dict]:
    """找出已入队或受理情况未知的结构，防止重复提交；未知不能当作未提交。"""
    result = {}
    for plan_path in list_plans(work_root, stage):
        if exclude_plan and os.path.normcase(os.path.abspath(plan_path)) == os.path.normcase(os.path.abspath(exclude_plan)):
            continue
        plan = load_plan(plan_path)
        rows = read_jobs(plan_path)
        states = query_jobs([row["job_id"] for row in rows], config)
        groups = {str(item["batch"]): item for item in plan["groups"]}
        for row in rows:
            group = groups.get(str(row["batch"]))
            if not group:
                raise IntegrityError("提交台账引用不存在的批次")
            status = row["submit_status"]
            if status not in {"SUBMITTED", "SUBMITTING", "UNKNOWN"}:
                continue
            scheduler = states.get(row["job_id"], {"state": "UNKNOWN"})
            if status == "SUBMITTED" and scheduler["state"] not in ACTIVE_SLURM_STATES | {"UNKNOWN"}:
                continue
            progress = read_progress(plan_path, group["batch"])
            from ..calculations.relax import REVISION_FIELDS
            planned = {item["path"]: item for item in plan["tasks"]}
            current_protocol = (plan.get("schema_version") == 2 and plan.get("workflow_version") == "0.0.2"
                                and progress.get("schema_version") == 2 and progress.get("workflow_version") == "0.0.2")
            finished = {task["path"] for task in progress.get("finished", [])
                        if current_protocol and task["path"] in planned
                        and all(task.get(key) == planned[task["path"]].get(key) for key in REVISION_FIELDS)}
            # 某结构结束后不因后续成员还在跑而占据活动状态；只保护尚未遍历完的成员。
            for path in group["tasks"]:
                if path not in finished:
                    result[path] = {"job_id": row["job_id"], "state": scheduler["state"],
                                    "batch": group["batch"], "plan": to_relative(plan_path, work_root)}
    return result
