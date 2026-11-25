"""计算入口编排；扫描、计划、提交的写入边界明确分开。"""

import os

from ..common.errors import ConfigError, ConflictError
from ..common.paths import join_local
from .discovery import active_tasks
from .models import short_reason, utc_now
from .planner import create_plan, scan_tasks
from .slurm import reconcile_submission, submit_batch
from .state import RuntimeLock, batch_directory, load_plan, read_jobs, work_root_for_plan, write_jobs


def submit_plan(stage: str, config: dict, plan_path: str) -> dict:
    """在外层控制器锁内提交固定计划，先记 SUBMITTING 再调用 sbatch。"""
    plan_path = os.path.abspath(plan_path)
    plan = load_plan(plan_path)
    if plan.get("schema_version") != 2 or plan.get("workflow_version") != "0.0.2":
        raise ConfigError("旧协议计划仅可查看，请在新阶段目录重新规划")
    if plan.get("status") == "prepared_not_submitted":
        raise ConfigError("该计划仅保留已准备输入，分组未成功；修正 parallel_jobs 后重新 plan/resume")
    root = work_root_for_plan(plan_path)
    if (plan["stage"] != stage
            or os.path.normcase(os.path.normpath(config["work_root"])) != os.path.normcase(root)):
        raise ConfigError("计划阶段或任务根与本次配置不一致")
    existing = active_tasks(root, stage, config, exclude_plan=plan_path)
    conflicts = set(existing).intersection(path for group in plan["groups"] for path in group["tasks"])
    if conflicts:
        raise ConflictError("计划包含其他作业正在处理或受理待确认的结构：" + ", ".join(sorted(conflicts)))
    rows = read_jobs(plan_path)
    by_batch = {str(row["batch"]): row for row in rows}
    for group in plan["groups"]:
        batch = group["batch"]
        previous = by_batch.get(str(batch))
        if previous and previous["submit_status"] == "SUBMITTED":
            # 同一份已受理计划永不重复 sbatch；失败续算应创建下一轮独立计划。
            continue
        if previous and previous["submit_status"] in {"SUBMITTING", "UNKNOWN"}:
            found = reconcile_submission(plan, batch, config)
            if found:
                previous.update(job_id=found, submit_status="SUBMITTED", reason="按唯一作业名确认已受理")
                previous["stdout"] = previous["stdout"].replace("%j", found)
                previous["stderr"] = previous["stderr"].replace("%j", found)
            else:
                previous.update(submit_status="UNKNOWN", reason="无法确认前次 sbatch 是否受理；保留现场，不自动重复提交")
            write_jobs(plan_path, rows)
            continue
        directory = batch_directory(plan_path, batch)
        os.makedirs(directory, exist_ok=True)
        # 受理动作前持久化意图；进程即使在 sbatch 返回后崩溃，也会留下可按名称核对的状态。
        pending = {"batch": str(batch), "job_id": "", "submit_status": "SUBMITTING", "submitted_at": utc_now(),
                   "stdout": f"batches/{batch:04d}/slurm-%j.out", "stderr": f"batches/{batch:04d}/slurm-%j.err", "reason": ""}
        if previous:
            rows[rows.index(previous)] = pending
        else:
            rows.append(pending)
        by_batch[str(batch)] = pending
        write_jobs(plan_path, rows)
        result = submit_batch(plan_path, plan, batch, config)
        rows[rows.index(pending)] = result
        by_batch[str(batch)] = result
        write_jobs(plan_path, rows)
    failed = [row for row in rows if row["submit_status"] != "SUBMITTED"]
    return {"stage": stage, "status": "partial_failed" if failed else "success", "plan": plan_path,
            "tasks": len(plan["tasks"]), "batches": len(plan["groups"]),
            "submitted": sum(row["submit_status"] == "SUBMITTED" for row in rows), "jobs": rows}


def run(stage: str, action: str, config: dict, plan_path: str | None = None) -> dict:
    """扫描只读；plan 生成输入；run/resume 生成新计划并提交；submit 消费指定计划。"""
    if action == "scan":
        tasks = scan_tasks(stage, config, prepare=False)
        return {"stage": stage, "status": "success", "work_root": config["work_root"], "tasks": tasks}
    if action not in {"plan", "run", "resume", "submit"}:
        raise ConfigError(f"未知计算动作：{action}")
    if action == "submit" and not plan_path:
        raise ConfigError("submit 必须通过 --plan 指定已生成的 plan.json")
    # 控制器锁只串行化计划/台账写入；真正的计算始终由 Slurm 作业自行排队分配资源。
    with RuntimeLock(join_local(config["work_root"], ".hpc_runs", f".{stage}.controller.lock")):
        if action != "submit":
            plan_path, plan = create_plan(stage, config)
            if action == "plan":
                return {"stage": stage, "status": "success", "plan": plan_path, "tasks": len(plan["tasks"]),
                        "batches": len(plan["groups"]), "parallel_jobs": plan["parallel_jobs"]}
        return submit_plan(stage, config, plan_path)
