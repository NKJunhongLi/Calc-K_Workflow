"""只读展示已有运行记录与调度状态；不重新检查科学输入或计算产物。"""

from collections import Counter
from datetime import datetime
import glob
import os

from ..common.atomic import read_json
from ..common.errors import ConfigError, WorkflowError
from ..common.paths import from_relative, join_local, resolve_local


ACTIVE_STATES = {"RUNNING", "COMPLETING", "CONFIGURING", "SUSPENDED", "RESIZING", "SIGNALING", "STAGE_OUT"}
QUEUED_STATES = {"PENDING", "REQUEUED", "REQUEUE_FED", "REQUEUE_HOLD"}
FAILED_STATES = {"FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL", "DEADLINE", "PREEMPTED", "REVOKED"}
LABELS = {
    "success": "完成", "physical_rejected": "物理原因终止（无需自动续算）",
    "technical_failed": "技术失败", "input_failed": "输入处理失败", "running": "计算中",
    "waiting_in_batch": "批内等待", "queued": "排队中", "planned": "待提交",
    "unknown": "状态待核实", "pending": "待计算", "active": "已被其他作业占用",
}


def _timestamp(value: object) -> float:
    """把 ISO 时间转为秒；旧记录或损坏字段返回0，仅用于防止显示陈旧内层状态。"""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, OverflowError):
        return 0.0


def _native_step(task_dir: str, config: dict, progress: dict) -> str:
    """直接读取当前 zstar stage JSON 的轻量步骤，不实例化会 mkdir 的原生状态类。"""
    outer = progress.get("current_command") or "准备输入"
    # pre/post/static 时旧 disp 状态不能冒充当前执行；run 才进一步显示内部进度。
    if "run" not in str(outer).lower():
        return str(outer)
    stage_root = join_local(task_dir, config.get("zstar_subdir", "zstar"), ".zstar", "stages")
    candidates = []
    started = _timestamp(progress.get("current_started_at", progress.get("started_at")))
    for path in glob.glob(os.path.join(stage_root, "*.json")):
        try:
            # 原生 path 可能是迁移前绝对路径；只使用受限 name 展示，不依赖 path 定位。
            if os.path.getmtime(path) + 1.0 < started:
                continue
            state = read_json(path)
            if not isinstance(state, dict) or state.get("status") != "running":
                continue
            name = state.get("name", "")
            if not isinstance(name, str) or not name or name.startswith(("/", "\\")) or ".." in name.split("/"):
                continue
            part = next((label for key, label in (("scf", "SCF"), ("band", "能带/绝缘检查"), ("pyatb", "PYATB"), ("dielectric", "介电响应")) if state.get(key) == "running"), "阶段运行")
            candidates.append((os.path.getmtime(path), f"{outer} → {name} → {part}"))
        except (OSError, ValueError, TypeError, WorkflowError):
            continue
    return max(candidates)[1] if candidates else str(outer)


def _job_state(row: dict, queue: dict) -> tuple[str, str]:
    """结合提交回执和实时队列状态；未知回执不擅自视为未提交。"""
    job_id = str(row.get("job_id") or "")
    value = queue.get(job_id, {})
    state = str(value.get("state", "UNKNOWN")).split("+", 1)[0].split(" ", 1)[0].upper()
    reason = str(value.get("reason") or row.get("reason") or "")
    if not job_id:
        state = str(row.get("submit_status") or "NOT_SUBMITTED").upper()
    return state, reason


def _evidence_paths(value: object, task_dir: str, root: str, plan_path: str) -> list[str]:
    """按约定的相对基准重建屏幕证据地址；不探测、复制或改写原文件。"""
    values = value if isinstance(value, list) else [value]
    result = []
    for item in values:
        if not isinstance(item, str) or not item:
            continue
        # worker 证据以 .hpc_runs 起始并相对 work；作业表以 batches 起始并相对计划；
        # 适配器原生证据相对任务。记录继续保持相对，只在屏幕上展开当前机器地址。
        anchor = root if item.startswith(".hpc_runs/") else os.path.dirname(plan_path) if item.startswith("batches/") else task_dir
        try:
            result.append(from_relative(anchor, item))
        except (ValueError, WorkflowError):
            result.append(item)
    return result


def collect_status(stage: str, config: dict, plan_path: str | None = None) -> dict:
    """汇总已有记录、实时队列和当前原生步骤；已记录终态不做临场复验。"""
    # 仅复用版本字段名称；关联比较使用 plan/progress 的内存记录，不读 manifest。
    from ..calculations.relax import REVISION_FIELDS
    from .slurm import query_jobs
    from .state import load_plan, read_jobs, read_progress, work_root_for_plan

    root = config["work_root"]
    warnings = []
    # 缓存只存活于本次查询。计划按完整路径区分，进度按 (计划路径, 批次) 区分，
    # 防止不同轮次中同名结构、同编号批次互相覆盖，也不生成任何磁盘缓存文件。
    plan_records = {}
    batch_records = {}

    def _plan_records(path: str) -> dict:
        """每个计划只读一次，返回 plan/rows 及任务、分组、作业的路径索引。"""
        if path not in plan_records:
            plan = load_plan(path)
            rows = read_jobs(path)
            # 所有值直接引用已读记录；不复制科学参数，不访问任务输入与产物目录。
            plan_records[path] = {
                "plan": plan, "rows": rows,
                "tasks": {entry["path"]: entry for entry in plan["tasks"]},
                "groups": {relative: group for group in plan["groups"] for relative in group["tasks"]},
                "jobs": {int(row["batch"]): row for row in rows},
            }
        return plan_records[path]

    def _batch_records(path: str, batch: int) -> tuple[dict, dict]:
        """每批只读一次 progress，并一次建立任务相对路径到原 finished 记录的索引。"""
        key = (path, batch)
        if key not in batch_records:
            try:
                progress = read_progress(path, batch) or {}
            except (OSError, ValueError, TypeError, WorkflowError) as exc:
                # 损坏状态明确提示；空记录同样缓存，避免每个结构重复读取并重复报警。
                progress = {}
                warnings.append(f"批次进度不可读 {path}#{batch}: {exc}")
            finished = {record["path"]: record for record in progress.get("finished", [])}
            batch_records[key] = (progress, finished)
        return batch_records[key]

    if plan_path:
        paths = [resolve_local(plan_path, os.getcwd())]
    else:
        paths = sorted(glob.glob(os.path.join(root, ".hpc_runs", stage, "*", "plan.json")))
    loaded = []
    for path in paths:
        try:
            records = _plan_records(path)
            plan = records["plan"]
            if plan.get("stage") != stage:
                raise ConfigError("计划阶段与命令不一致。")
            if os.path.normcase(work_root_for_plan(path)) != os.path.normcase(root):
                raise ConfigError("计划必须位于当前 work_root 的 .hpc_runs/阶段/轮次 内。")
            loaded.append((str(plan.get("created_at", "")), path, plan, records["rows"]))
        except (OSError, ValueError, TypeError, WorkflowError) as exc:
            if plan_path:
                raise
            warnings.append(f"{path}: {type(exc).__name__}: {exc}")
    # 按计划建立时间覆盖同路径旧状态；重复提交历史仍保留在轮次列表，不重复计结构。
    loaded.sort(key=lambda item: (item[0], item[1]))
    job_ids = sorted({str(row.get("job_id")) for _, _, _, rows in loaded for row in rows if row.get("job_id")} |
                     {str(entry["job_id"]) for _, _, plan, _ in loaded for entry in plan["tasks"] if entry.get("status") == "active" and entry.get("job_id")})
    queue = query_jobs(job_ids, config) if job_ids else {}
    tasks, plans = {}, []
    for _, path, plan, rows in loaded:
        groups = plan.get("groups", [])
        row_by_batch = _plan_records(path)["jobs"]
        grouped = {}
        for group in groups:
            batch = int(group["batch"])
            progress, finished = _batch_records(path, batch)
            row = row_by_batch.get(batch, {})
            state, reason = _job_state(row, queue)
            for relative in group.get("tasks", []):
                grouped[relative] = (batch, progress, finished, row, state, reason)
        plans.append({"plan": path, "parallel_jobs": plan.get("parallel_jobs"), "batches": len(groups),
                      "batch_sizes": [len(group.get("tasks", [])) for group in groups],
                      "submitted": sum(bool(row.get("job_id")) for row in rows),
                      "jobs": [{**row, "queue_state": _job_state(row, queue)[0]} for row in rows]})
        for entry in plan.get("tasks", []):
            if (not plan_path and plan.get("schema_version") == 2 and entry.get("calculation_subdir")
                    and entry["calculation_subdir"] != config.get(stage + "_subdir", "cell-relax" if stage == "relax" else "zstar")):
                continue
            relative = entry["path"]
            task_dir = from_relative(root, relative)
            frozen = {**config, **plan.get("execution", {})}
            associated_entry = entry
            associated_plan = plan
            evidence_plan = path
            # 新一轮扫描会记录“被旧作业占用”。沿其相对计划引用读取原批次，不能把
            # 旧批次中正在算的结构和排队结构笼统打印成 active，或丢失真实步骤。
            if relative not in grouped and entry.get("status") == "active" and entry.get("plan"):
                try:
                    source_path = from_relative(root, entry["plan"])
                    # 多个 active 条目常指向同一原计划和批次，直接复用本次查询索引。
                    source = _plan_records(source_path)
                    source_plan = source["plan"]
                    source_group = source["groups"].get(relative)
                    source_entry = source["tasks"].get(relative)
                    if source_group is None or source_entry is None:
                        raise ConfigError("活动任务来源计划缺少对应任务或分组。")
                    source_row = source["jobs"].get(source_group["batch"], {})
                    source_progress, source_finished = _batch_records(source_path, source_group["batch"])
                    state, reason = _job_state(source_row, queue)
                    grouped[relative] = (source_group["batch"], source_progress, source_finished, source_row, state, reason)
                    frozen = {**config, **source_plan.get("execution", {})}
                    associated_entry = source_entry
                    associated_plan = source_plan
                    evidence_plan = source_path
                except (OSError, ValueError, TypeError, WorkflowError) as exc:
                    warnings.append(f"活动任务来源不可读 {relative}: {exc}")
            item = {"path": relative, "directory": task_dir, "status": entry.get("status", "planned"),
                    "step": "", "reason": entry.get("reason", ""), "evidence": entry.get("evidence", ""),
                    "plan": path, "job_id": "", "batch": None, "symmetry": associated_entry.get("symmetry")}
            if relative in grouped:
                batch, progress, finished, row, state, reason = grouped[relative]
                item.update(batch=batch, job_id=str(row.get("job_id") or ""))
                current_protocol = (associated_plan.get("schema_version") == 2
                                    and associated_plan.get("workflow_version") == "0.0.2")
                # 索引每批只建一次；此处只查当前结构，再比较它的五个声明版本字段。
                # 这只是状态记录关联，不读取当前输入，也不证明计算产物仍然存在。
                record = finished.get(relative)
                record_matches = record is not None and (not current_protocol or (
                    progress.get("schema_version") == 2
                    and progress.get("workflow_version") == "0.0.2"
                    and all(record.get(key) == associated_entry.get(key) for key in REVISION_FIELDS)))
                current = progress.get("current_task") == relative
                # 已结束结构的结果由 worker 确认；父 Slurm 失败不污染同批先前成功任务。
                if record_matches:
                    item.update({k: v for k, v in record.items() if k in {"status", "reason", "evidence", "step", "supplementary"}})
                elif state in ACTIVE_STATES and current:
                    item.update(status="running", step=_native_step(task_dir, frozen, progress) if stage == "zstar" else str(progress.get("current_command") or "cell-relax 准备/运行"))
                elif state in ACTIVE_STATES:
                    item.update(status="waiting_in_batch", step="同批前序结构尚未结束")
                elif state in QUEUED_STATES:
                    item.update(status="queued", step=reason)
                elif state in {"FAILED", "SUBMIT_FAILED"} and not row.get("job_id"):
                    item.update(status="technical_failed", reason=reason or "sbatch 提交失败")
                elif state in FAILED_STATES or state == "COMPLETED":
                    if current:
                        label = {"OUT_OF_MEMORY": "内存超限", "TIMEOUT": "Slurm 时限终止", "NODE_FAIL": "节点故障", "CANCELLED": "Slurm 作业被取消"}.get(state, "作业退出，当前结构未写出终态")
                        item.update(status="technical_failed", reason=label, evidence=row.get("stderr") or row.get("stdout") or "", step=str(progress.get("current_command") or ""))
                    else:
                        item.update(status="waiting_in_batch", step="批次已结束，本结构尚未执行")
                elif row:
                    item.update(status="unknown", reason=reason or "队列状态/提交回执待核实")
                else:
                    item.update(status="planned", step="已分组，尚未提交")
            # 保留旧协议历史展示规则；当前协议终态按已关联记录原样显示，绝不
            # 因当前输入/日志/产物缺失或变化，在状态查询中重新判定计算成败。
            legacy = associated_plan.get("schema_version") != 2 or associated_plan.get("workflow_version") != "0.0.2"
            if legacy and item["status"] in {"success", "physical_rejected"}:
                item.update(status="unknown", reason="旧协议终态仅供历史查看，不具备当前版本关联")
            item["evidence_locations"] = _evidence_paths(item.get("evidence"), task_dir, root, evidence_plan)
            item["supplementary"] = [{**detail, "evidence_locations": _evidence_paths(detail.get("evidence"), task_dir, root, evidence_plan)} for detail in item.get("supplementary", [])]
            tasks[relative] = item
    counts = Counter(item["status"] for item in tasks.values())
    return {"status": "success", "stage": stage, "work_root": root, "plans": plans,
            "total": len(tasks), "counts": dict(counts), "tasks": list(tasks.values()), "warnings": warnings}


def format_status(report: dict) -> str:
    """将全部结构分组打印为清晰中文报告，不省略任务路径，不写派生 CSV。"""
    counts = report["counts"]
    lines = [f"{'=' * 72}", f"阶段：{report['stage']}    任务总目录：{report['work_root']}",
             f"计划轮数：{len(report['plans'])}    结构总数（跨轮去重）：{report['total']}"]
    if not report["plans"]:
        lines.append("当前目录没有本阶段计划；尚未提交或配置根目录不正确。")
    for plan in report["plans"]:
        sizes = ", ".join(str(value) for value in plan["batch_sizes"]) or "无"
        lines.extend([f"计划：{plan['plan']}", f"  parallel_jobs={plan['parallel_jobs']}；批次数={plan['batches']}；每批结构数=[{sizes}]；有回执作业数={plan['submitted']}"])
        for job in plan["jobs"]:
            lines.append(f"  批次 {job['batch']}  job={job.get('job_id') or '无'}  提交={job.get('submit_status')}  Slurm={job['queue_state']}")
    failed = counts.get("technical_failed", 0) + counts.get("input_failed", 0)
    lines.append(f"完成={counts.get('success', 0)}  计算中={counts.get('running', 0)}  失败={failed}  物理终止={counts.get('physical_rejected', 0)}  批内等待={counts.get('waiting_in_batch', 0)}  排队={counts.get('queued', 0)}  待提交={counts.get('planned', 0)}  待核实={counts.get('unknown', 0)}")
    # 每个状态独立段落，失败原因保持短文本；详细证据定位回原始日志。
    for state in LABELS:
        selected = [task for task in report["tasks"] if task["status"] == state]
        if not selected:
            continue
        lines.append(f"\n【{LABELS[state]}】{len(selected)}")
        for task in selected:
            lines.append(f"  {task['directory']}")
            if task["job_id"]:
                lines.append(f"    批次={task['batch']}  job={task['job_id']}")
            symmetry = task.get("symmetry") or {}
            if symmetry:
                lines.append(f"    对称性：initial={symmetry.get('initial_group')} relaxed={symmetry.get('relaxed_group')} output={symmetry.get('output_group')} FLAG={symmetry.get('flags') or symmetry.get('flag') or '无'}")
                lines.append(f"    容差扫描：{symmetry.get('symmetry_scan', {})}；宽松匹配初始群：{symmetry.get('loose_matches_initial', [])}")
            if task.get("step"):
                lines.append(f"    步骤：{task['step']}")
            if task.get("reason"):
                lines.append(f"    原因：{task['reason']}")
            for location in task.get("evidence_locations", []):
                lines.append(f"    证据：{location}")
            for detail in task.get("supplementary", []):
                lines.append(f"    后续依据：{detail.get('command', '')}：{detail.get('reason', '')}")
                for location in detail.get("evidence_locations", []):
                    lines.append(f"      位置：{location}")
    if report["warnings"]:
        lines.append("\n【读取提示】")
        lines.extend("  " + warning for warning in report["warnings"])
    lines.append("=" * 72)
    return "\n".join(lines)
