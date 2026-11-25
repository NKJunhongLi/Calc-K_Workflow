"""根据有效待算结构数生成精确数量的非空串行批次。"""

import os
import uuid

from ..common.atomic import atomic_write_json
from ..common.errors import ConfigError
from ..common.paths import join_local
from .discovery import active_tasks, checked_task_directory, previous_tasks, task_directories
from .models import short_reason, utc_now


EXECUTION_KEYS = (
    "relax_subdir", "zstar_subdir", "abacus_command", "pyatb_command", "zstar_command", "zstar_commands",
    "xc", "kspacing", "symprec", "minimum_distance", "angle_tolerance", "relax_input_file",
    "relax_max_steps", "max_symmetry_strain", "symmetry_scan_tolerances",
)


def balanced_groups(paths: list[str], parallel_jobs: int) -> list[dict]:
    """以并行作业数平均分组：0 每结构一组，1 全串行，超出总数报错。"""
    if isinstance(parallel_jobs, bool) or not isinstance(parallel_jobs, int) or parallel_jobs < 0:
        raise ConfigError("parallel_jobs 必须为非负整数")
    count = len(paths)
    if parallel_jobs > count:
        raise ConfigError(f"parallel_jobs={parallel_jobs} 大于有效待算结构数 {count}，未提交任何任务")
    if not count:
        return []
    jobs = parallel_jobs or count
    quotient, remainder = divmod(count, jobs)
    groups = []
    start = 0
    for index in range(jobs):
        size = quotient + (index < remainder)
        groups.append({"batch": index + 1, "tasks": paths[start:start + size]})
        start += size
    return groups


def calculations_for(stage: str):
    """延迟导入对应计算适配器，避免扫描 CLI 时加载科学计算依赖。"""
    if stage == "relax":
        from ..calculations.relax import prepare_relax, inspect_relax, run_relax
        return prepare_relax, inspect_relax, run_relax
    if stage == "zstar":
        from ..calculations.zstar import prepare_zstar, inspect_zstar, run_zstar
        return prepare_zstar, inspect_zstar, run_zstar
    raise ConfigError(f"未知计算阶段：{stage}")


def scan_tasks(stage: str, config: dict, prepare: bool = False) -> list[dict]:
    """发现并检查所有结构；prepare=True 时仅在独立阶段子目录内准备新输入。"""
    prepare_task, inspect_task, _ = calculations_for(stage)
    root = config["work_root"]
    if stage == "zstar":
        from ..calculations.relax import build_relax_revision_index
        config = {**config, "_relax_revision_index": build_relax_revision_index(root, config.get("relax_subdir", "cell-relax"))}
    active = active_tasks(root, stage, config)
    previous = previous_tasks(root, stage, config)
    tasks = []
    for path in task_directories(root):
        entry = {"path": path, "status": "pending", "input_revision_id": ""}
        try:
            directory = checked_task_directory(root, path)
            if path in active:
                entry.update(status="active", reason="已有活动作业或提交受理情况待确认", **active[path])
            else:
                from ..calculations.relax import revision_matches, read_task_revision_id, REVISION_FIELDS
                read_task_revision_id(directory)
                known = previous.get(path, {})
                if known and not revision_matches(directory, config, stage, known):
                    entry.update(status="input_failed", reason="同一计算目录的输入声明或科学设置已改变，请使用新的阶段目录")
                    entry.update({key: known[key] for key in REVISION_FIELDS if key in known})
                else:
                    associated = bool(known.get("input_revision_id"))
                    inspected = inspect_task(directory, config, revision_entry=known if associated else None)
                    status = inspected.get("status", "pending")
                    if associated and known.get("status") == "physical_rejected":
                        entry.update({key: known[key] for key in ("status", "reason", "evidence", "step", *REVISION_FIELDS) if key in known})
                    elif associated and status in {"success", "physical_rejected"} and known.get("status") == status:
                        entry.update(inspected)
                        entry.update({key: known[key] for key in REVISION_FIELDS if key in known})
                    elif prepare:
                        prepared = prepare_task(directory, {**config, "_revision_entry": known if associated else None})
                        entry.update(prepared)
                        if prepared.get("status") == "pending":
                            entry["status"] = "planned"
                    else:
                        entry.update(inspected)
                        if associated:
                            entry.update({key: known[key] for key in REVISION_FIELDS if key in known})
                        elif status in {"success", "physical_rejected"}:
                            entry.update(status="pending", reason="既有产物没有当前协议版本关联，不能自动沿用")
        except Exception as exc:
            # 单结构输入失败保留条目继续检查其他结构；不存在隐式的整批提前退出。
            entry.update(status="input_failed", reason=short_reason(f"{type(exc).__name__}: {exc}"))
        if not entry.get("symmetry") and previous.get(path, {}).get("symmetry"):
            entry["symmetry"] = previous[path]["symmetry"]
        entry["path"] = path
        tasks.append(entry)
    return tasks


def create_plan(stage: str, config: dict) -> tuple[str, dict]:
    """准备输入并冻结版本；分组错误仍保留不可提交计划，避免输入失去关联。"""
    tasks = scan_tasks(stage, config, prepare=True)
    paths = [task["path"] for task in tasks if task.get("status") == "planned"]
    grouping_error = None
    try:
        groups = balanced_groups(paths, config["parallel_jobs"])
    except ConfigError as exc:
        # 无有效任务时仍向屏幕报告每个输入失败的原始短原因，不能让数量错误遮掉来源诊断。
        failures = [f"{task['path']}: {task.get('reason', '输入处理失败')}"
                    for task in tasks if task.get("status") == "input_failed"]
        grouping_error = str(exc) + ("\n输入处理失败：\n" + "\n".join(failures) if failures else "")
        groups = []
        for task in tasks:
            if task["status"] == "planned":
                task["status"] = "pending"
    created = utc_now()
    run_id = created.replace("-", "").replace(":", "").replace("+00:00", "Z").replace(".", "") + "-" + uuid.uuid4().hex[:8]
    directory = join_local(config["work_root"], ".hpc_runs", stage, run_id)
    # 只冻结 worker 必须使用的少量命令和科学参数，绝不复制完整配置、API Key 或原生状态树。
    execution = {key: config[key] for key in EXECUTION_KEYS if key in config}
    plan = {"schema_version": 2, "workflow_version": "0.0.2",
            "status": "prepared_not_submitted" if grouping_error else "ready", "stage": stage, "run_id": run_id, "task_root": "../../..",
            "created_at": created, "parallel_jobs": config["parallel_jobs"], "execution": execution,
            "tasks": tasks, "groups": groups}
    os.makedirs(directory, exist_ok=False)
    path = join_local(directory, "plan.json")
    if grouping_error:
        plan["planning_error"] = grouping_error
    atomic_write_json(path, plan)
    if grouping_error:
        raise ConfigError(grouping_error + "\n已保留准备输入的不可提交计划：" + path)
    return path, plan
