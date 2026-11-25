"""只读确定整个工作根的任务终态和输入关联；不查询队列、不接管旧散装结果。"""

import os
import stat
from datetime import datetime
from uuid import UUID

from ..calculations.relax import calculation_directory, revision_matches
from ..common.errors import IntegrityError, WorkflowError
from ..common.paths import from_relative, join_local, to_relative
from ..submission.discovery import checked_task_directory
from ..submission.state import batch_directory, list_plans, load_plan, read_jobs, read_progress
from .metadata import resolve_metadata


TERMINAL_OUTCOMES = frozenset({"success", "physical_rejected", "technical_failed", "input_failed"})
REVISION_FIELDS = ("input_revision_id", "source_input_revision_id", "source_relax_revision_id",
                   "calculation_subdir", "scientific_input")


def _stage_inputs_present(directory: str) -> bool:
    """仅检查阶段两输入为普通非空文件；不回读结构，不承诺识别人工内容修改。"""
    for name in ("STRU", "INPUT"):
        try:
            info = os.lstat(join_local(directory, name))
        except FileNotFoundError:
            return False
        if (not stat.S_ISREG(info.st_mode) or info.st_size == 0
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            return False
    return True


def _checked_record(root: str, path: str) -> str:
    """确认运行记录没有通过符号链接越出当前工作根，返回原绝对路径。"""
    relative = to_relative(path, root)
    # checked_task_directory 同样能核对普通文件的祖先；不要求最后一级必须为目录。
    checked_task_directory(root, relative)
    current = path
    while True:
        if getattr(os.path, "isjunction", lambda value: False)(current):
            raise IntegrityError(f"记录或任务目录不接受 junction：{current}")
        if os.path.normcase(current) == os.path.normcase(os.path.normpath(root)):
            break
        current = os.path.dirname(current)
    return path


def _plan_order(plan: dict, path: str) -> tuple[float, int]:
    """用真实创建时间和纳秒修改时间排序，不把同秒随机 UUID 当成先后顺序。"""
    modified = os.stat(path).st_mtime_ns
    created = plan.get("created_at")
    if created is None:
        return modified / 1_000_000_000, modified
    try:
        parsed = datetime.fromisoformat(created)
        if parsed.tzinfo is None:
            raise ValueError("missing_timezone")
    except (ValueError, TypeError) as exc:
        raise IntegrityError(f"计划 created_at 不是带时区 ISO 时间：{path}") from exc
    # 当前记录含微秒；兼容没有微秒的同秒记录时，以可随 copytree 保留的文件 mtime 细分。
    return parsed.timestamp(), modified


def _read_history(root: str, stage: str) -> tuple[dict, list[str]]:
    """读取一阶段的所有计划及其专属进度，返回索引和实际读取的控制文件位置。"""
    history, record_paths = {}, []
    # 先检查运行目录祖先，再列举；不能等读到计划后才发现 .hpc_runs 被链接到外部。
    _checked_record(root, join_local(root, ".hpc_runs", stage))
    for path in list_plans(root, stage):
        _checked_record(root, path)
        plan = load_plan(path)
        current_protocol = plan.get("schema_version") == 2 and plan.get("workflow_version") == "0.0.2"
        record_paths.append(path)
        tasks = {item["path"]: dict(item) for item in plan["tasks"]}
        finished, memberships, progress_by_batch = {}, {}, {}
        # 完成记录必须沿用本批计划的版本、来源版本、目录和冻结科学参数。
        for group in plan["groups"]:
            progress_path = join_local(batch_directory(path, group["batch"]), "progress.json")
            _checked_record(root, progress_path)
            record_paths.append(progress_path)
            progress = read_progress(path, group["batch"])
            progress_by_batch[group["batch"]] = progress
            if progress and current_protocol and (progress.get("schema_version") != 2
                                                  or progress.get("workflow_version") != "0.0.2"):
                raise IntegrityError("当前计划的 progress.json 缺少当前协议版本，不能作为终态")
            members = set(group["tasks"])
            current = progress.get("current_task")
            if current is not None and current not in members:
                raise IntegrityError("progress.json 当前结构不属于本批次")
            for item in progress.get("finished", []):
                relative = item["path"]
                if relative not in members or relative not in tasks:
                    raise IntegrityError("progress.json 完成结构不属于本计划批次")
                if not current_protocol:
                    continue
                if (any(item.get(key) != tasks[relative].get(key) for key in REVISION_FIELDS)
                        or item.get("status") not in TERMINAL_OUTCOMES):
                    raise IntegrityError("progress.json 完成状态或版本关联与本计划不一致")
                finished[relative] = dict(item, _progress_path=progress_path)
            for relative in members:
                memberships[relative] = group["batch"]
        jobs_path = join_local(os.path.dirname(path), "jobs.csv")
        _checked_record(root, jobs_path)
        record_paths.append(jobs_path)
        jobs = read_jobs(path)
        batches = {group["batch"] for group in plan["groups"]}
        if any(int(row["batch"]) not in batches for row in jobs):
            raise IntegrityError("jobs.csv 引用了本计划不存在的批次")
        history[to_relative(path, root)] = {
            "path": path, "plan": plan, "tasks": tasks, "finished": finished,
            "memberships": memberships, "jobs": {int(row["batch"]): row for row in jobs},
            "progress": progress_by_batch,
            "order": _plan_order(plan, path),
            "current_protocol": current_protocol,
        }
    return history, record_paths


def _latest_event(relative: str, history: dict, root: str) -> dict | None:
    """选择任务最近一轮状态；active 快照沿指针回到真正执行计划，不冻结旧观察结果。"""
    candidates = [value for value in history.values() if relative in value["tasks"]]
    if not candidates:
        return None
    latest = max(item["order"] for item in candidates)
    tied = [item for item in candidates if item["order"] == latest]
    if len(tied) != 1:
        raise IntegrityError(f"同一任务的多轮计划时间完全相同，无法可靠确定最新输入轮次：{relative}")
    record = tied[0]
    visited = set()
    while True:
        key = to_relative(record["path"], root)
        if key in visited:
            raise IntegrityError("活动计划引用形成循环")
        visited.add(key)
        task = record["tasks"][relative]
        if not record["current_protocol"]:
            return dict(task, status="pending", reason="旧协议记录仅供查看，不能提供当前版本终态",
                        _legacy_protocol=True, _plan_path=record["path"], _execution=record["plan"]["execution"])
        if task.get("status") != "active":
            break
        target = task.get("plan")
        # 无明确来源的活动状态没有结束证据，保持未完成；不得回退使用更旧的成功。
        if not target:
            return dict(task, _plan_path=record["path"], _execution=record["plan"]["execution"])
        from_relative(root, target)
        if target not in history or relative not in history[target]["tasks"]:
            raise IntegrityError("活动任务引用了不存在或不含该任务的执行计划")
        record = history[target]
    event = dict(task, _plan_path=record["path"], _execution=record["plan"]["execution"])
    if relative in record["finished"]:
        event.update(record["finished"][relative])
    elif task.get("status") == "planned":
        row = record["jobs"].get(record["memberships"].get(relative))
        if row and row["submit_status"] == "FAILED":
            event.update(status="technical_failed", reason="Slurm 提交失败：" + row.get("reason", ""),
                         evidence=[join_local(os.path.dirname(record["path"]), "jobs.csv")])
        else:
            # 没有 worker 的 finished 边界不能证明整条四命令链结束；即使产物已出现也不抢先发布。
            event.update(status="pending", reason="本轮尚无已结束记录，可能排队、计算中或需要续算核验")
    return event


def _base_metadata(relative: str) -> dict:
    """为损坏元数据但已知终态的任务保留可定位空行，不猜测任何源身份。"""
    return {"task_name": relative, "dft_priority": "", "global_sample_id": "",
            "formula": "", "elements": "", "nominal_k_available": "", "nominal_k_value": "",
            "input_source": "", "dataset_namespace": "", "source_record_id": ""}


def _relax_revision_indexes(root: str, history: dict) -> dict[str, dict]:
    """复用已读取历史，按阶段目录建立本轮父版本索引，不再逐结构重读计划。"""
    indexes = {}
    for record in sorted(history.values(), key=lambda value: value["order"]):
        if not record["current_protocol"]:
            continue
        for relative, planned in record["tasks"].items():
            event = {**planned, **record["finished"].get(relative, {})}
            source, revision = event.get("source_input_revision_id"), event.get("input_revision_id")
            if not source or not revision:
                continue
            subdir = event["calculation_subdir"]
            index = indexes.setdefault(subdir, {"work_root": os.path.normcase(os.path.abspath(root)),
                                               "relax_subdir": subdir, "entries": {}})
            index["entries"].setdefault(relative, {})[source] = revision if event.get("status") == "success" else None
    return indexes


def discover_tasks(config: dict) -> list[dict]:
    """只读汇总一个 work_root 批次的每个新版任务，返回终态、元数据和输入关联记录。"""
    root = config["work_root"]
    if not os.path.isdir(root) or os.path.islink(root):
        raise IntegrityError("work_root 必须是现存普通目录")
    histories, record_paths = {}, []
    for stage in ("relax", "zstar"):
        histories[stage], paths = _read_history(root, stage)
        record_paths.extend(paths)
    # 两阶段控制记录本轮只读一次；下面及清理复用同一小型索引，普通UUID仍逐任务核对。
    relax_indexes = _relax_revision_indexes(root, histories["relax"])
    # 按成员关联共享记录对象，不复制整批计划；清理只访问该结构参与过的历史，
    # 不再为每个结构重新读取全部 plan/jobs/progress。
    history_records = {}
    for history in histories.values():
        for record in history.values():
            for relative in record["tasks"]:
                history_records.setdefault(relative, {})[record["path"]] = record
    # 正式任务范围取当前直接子目录中的 task_manifest；不扫描 staging 或旧散装 STRU。
    names = []
    for name in sorted(os.listdir(root)):
        path = join_local(root, name)
        if name.startswith(".") or ".staging-" in name or not os.path.isdir(path):
            continue
        if os.path.lexists(join_local(path, "task_manifest.json")):
            names.append(name)
    # 历史计划引用的正式任务若被移走/删清单，不能静默从整批表中消失。
    declared = {relative for history in histories.values() for value in history.values() for relative in value["tasks"]}
    for relative in declared:
        if "/" in relative or relative not in names:
            raise IntegrityError(f"计算计划引用的正式前处理任务缺失或不在直接子目录：{relative}")
    result, identities, samples, priorities = [], set(), set(), set()
    for relative in names:
        task_dir = checked_task_directory(root, relative)
        _checked_record(root, task_dir)
        metadata, metadata_errors = _base_metadata(relative), []
        try:
            metadata = resolve_metadata(task_dir)
        except (OSError, ValueError, UnicodeError, WorkflowError) as exc:
            # 记录完整性问题归属于该任务，但缺乏可信计算终态时仍阻止最终 CSV 发布。
            metadata_errors.append(str(exc))
        if not metadata_errors:
            identity = (metadata["input_source"], metadata["dataset_namespace"], metadata["source_record_id"])
            if identity in identities:
                raise IntegrityError(f"本批稳定来源身份重复：{identity}")
            identities.add(identity)
            if metadata["input_source"] == "MJM":
                # 不同命名空间可重用编号；同一数据集中的样本编号/priority 不能关联到两个任务。
                sample = (metadata["dataset_namespace"], metadata["global_sample_id"])
                priority = (metadata["dataset_namespace"], metadata["dft_priority"])
                if sample in samples or priority in priorities:
                    raise IntegrityError("同一 MJM 数据集的 global_sample_id 或 dft_priority 重复")
                samples.add(sample)
                priorities.add(priority)
        event = _latest_event(relative, histories["zstar"], root)
        stage = "zstar"
        if event is None:
            relax_event = _latest_event(relative, histories["relax"], root)
            if relax_event and relax_event.get("status") in {"technical_failed", "input_failed", "physical_rejected"}:
                event, stage = relax_event, "relax"
        event = event or {"status": "pending", "reason": "尚无 zstar 已结束记录", "_execution": {}}
        outcome = event.get("status", "pending")
        terminal = outcome in TERMINAL_OUTCOMES
        execution = dict(event["_execution"])
        relax_subdir = execution.get("relax_subdir", "cell-relax")
        execution["_relax_revision_index"] = relax_indexes.get(relax_subdir, {
            "work_root": os.path.normcase(os.path.abspath(root)), "relax_subdir": relax_subdir, "entries": {}})
        expected = "" if event.get("_legacy_protocol") else event.get("input_revision_id", "")
        evidence = [event[key] for key in ("_plan_path", "_progress_path") if event.get(key)]
        raw_evidence = event.get("evidence", [])
        if isinstance(raw_evidence, list):
            evidence.extend(str(item) for item in raw_evidence)
        reason = event.get("reason", "")
        provenance = "unavailable"
        # 计算目录使用冻结计划布局；默认配置只用于尚没有计划的任务，不重新解释旧物理输入。
        layout = {"zstar_subdir": config.get("zstar_subdir", "zstar"), **execution}
        zstar_dir = calculation_directory(task_dir, layout, "zstar")
        _checked_record(root, zstar_dir)
        relax_dir = None
        if stage == "zstar" and expected:
            try:
                UUID(expected)
            except (ValueError, TypeError, AttributeError) as exc:
                raise IntegrityError("计划中的 zstar input_revision_id 不是有效 UUID") from exc
            try:
                if _stage_inputs_present(zstar_dir) and revision_matches(task_dir, execution, "zstar", event):
                    provenance = "revision_linked"
                else:
                    provenance = "revision_mismatch"
                    reason = "zstar 阶段输入缺失/无效，或当前来源版本、冻结科学参数与计划不一致"
            except (OSError, ValueError, WorkflowError) as exc:
                provenance = "revision_mismatch"
                reason = "zstar 输入无法与冻结计划关联：" + str(exc)
            if provenance == "revision_mismatch" and terminal:
                outcome = "input_failed"
        elif stage == "relax":
            relax_dir = calculation_directory(task_dir, execution, "relax")
            _checked_record(root, relax_dir)
            # 上游失败仍核对声明的来源版本；relax 的关联不能当作 zstar 关联，
            # 因此这里不读取任何残留 zstar 物性。UUID 不证明输入字节从未手改。
            if expected:
                try:
                    UUID(expected)
                    if not _stage_inputs_present(relax_dir) or not revision_matches(task_dir, execution, "relax", event):
                        outcome = "input_failed"
                        reason = "relax 阶段输入缺失/无效，或当前来源版本、冻结科学参数与计划不一致"
                except (OSError, ValueError, TypeError, AttributeError, WorkflowError) as exc:
                    outcome = "input_failed"
                    reason = "relax 输入无法与冻结计划关联：" + str(exc)
            # 输入准备失败可能没有阶段版本，此时允许保留已有终态失败行。
        if metadata_errors:
            provenance = "invalid_record"
            if terminal:
                outcome = "input_failed"
                reason = "前处理记录或元数据核验失败：" + "; ".join(metadata_errors)
        # 未绑定当前版本的成功/物理拒绝不接受为正式结果；输入准备失败可没有 STRU。
        if terminal and stage == "zstar" and not expected and outcome in {"success", "physical_rejected"}:
            outcome = "input_failed"
            reason = "已结束结果缺少当前计算 input_revision_id，拒绝接管未绑定产物"
        paths = [join_local(task_dir, name) for name in
                 ("task_manifest.json", "STRU", "INPUT", "primitive.cif")]
        paths += [join_local(zstar_dir, name) for name in ("STRU", "INPUT")]
        if relax_dir is not None:
            paths += [join_local(relax_dir, name) for name in ("STRU", "INPUT")]
        metadata["zstar_relative_path"] = to_relative(zstar_dir, root)
        result.append({
            "task_name": relative, "task_dir": task_dir, "metadata": metadata,
            "metadata_errors": metadata_errors, "terminal": terminal, "outcome": outcome,
            "reason": reason, "evidence": evidence, "zstar_dir": zstar_dir,
            "zstar_relative_path": metadata["zstar_relative_path"],
            "expected_input_revision_id": expected if stage == "zstar" else "",
            "revision_entry": {key: event.get(key) for key in REVISION_FIELDS},
            "execution": event["_execution"], "provenance_status": provenance,
            "_relax_revision_index": execution["_relax_revision_index"],
            "_history_records": history_records.get(relative, {}),
            "record_paths": [*record_paths, *paths],
        })
    return result
