"""单层本地文件前处理；保留任务记录、独立快照及既有成果的只读复用。"""

import base64
from dataclasses import replace
import os
import re

from ..common.atomic import (FileLock, atomic_write_csv, atomic_write_json,
                             read_json)
from ..common.paths import join_local, path_basename
from ..common.errors import IntegrityError
from ..records.manifest import SCHEMA_VERSION, software_versions
from .. import __version__
from ..resources.abacus import build_abacus_resources
from .planner import build_plans, discover_inputs
from .primitive import make_primitive
from .templates import load_input_bytes
from .validation import read_and_validate_structure, validate_config
from .writer import WriterSession, inspect_existing, recover_staging, scan_staging, write_task


CSV_FIELDS = (
    "task_id", "task_name", "task_directory", "input_source", "source_record_id", "formula", "elements",
    "source_file", "input_revision_id", "source_nsites", "primitive_nsites",
    "used_input_cell_fallback", "stage", "status", "action", "reason",
)


def _row(record: object, task_id: str = "", manifest: dict | None = None, *, status: str = "failed",
         action: str = "failed", stage: str = "metadata", reason: str = "") -> dict:
    """生成固定字段的派生 CSV 行；所有任务引用相对当前 work 根目录。"""
    # 以固定列生成运行索引，缺少成功 manifest 的尝试保留空产物字段，不猜测结果身份。
    manifest = manifest or {}
    return {
        "task_id": task_id, "task_name": task_id, "task_directory": task_id,
        "input_source": record.input_source, "source_record_id": record.source_record_id,
        "formula": record.formula,
        "elements": "-".join(getattr(record, "elements", ())),
        "source_file": record.source_file,
        "input_revision_id": manifest.get("input_revision_id", ""),
        "source_nsites": manifest.get("source_nsites"), "primitive_nsites": manifest.get("primitive_nsites"),
        "used_input_cell_fallback": manifest.get("used_input_cell_fallback"),
        "stage": stage, "status": status, "action": action, "reason": reason,
    }


def _aggregate_rows(work_root: str) -> list[dict]:
    """从已发布任务重建累计索引，不把当前失败尝试写成旧成功任务的新身份。"""
    from ..inputs.models import InputRecord

    # 只从正式发布目录重建累计索引；运行临时目录和普通辅助文件不作为成功任务。
    rows = []
    # 逐个检查正式任务记录；任何坏记录都显式报告，避免累计表混入错误身份。
    for name in sorted(os.listdir(work_root)):
        if re.search(r"\.staging-[0-9a-f]{32}$", name):
            continue
        directory = join_local(work_root, name)
        manifest_path = join_local(directory, "task_manifest.json")
        if not os.path.isdir(directory) or os.path.islink(directory) or not os.path.isfile(manifest_path):
            continue
        try:
            manifest = read_json(manifest_path)
            if not isinstance(manifest, dict):
                raise IntegrityError("task_manifest_not_object")
            if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("workflow_version") != __version__:
                continue
            record = InputRecord(
                manifest["input_source"], manifest["source_record_id"], manifest["source_file"],
                manifest["formula"], tuple(manifest["elements"]),
            )
            # 累计索引只读完成记录；不在批末再次读取或验证全部计算输入。
            rows.append(_row(record, name, manifest, status="success", stage="export", action="published"))
        except Exception as exc:
            # 无法可靠解读的任务记录不猜测身份；整轮明确失败，防止发布误导累计索引。
            raise IntegrityError(f"累计索引发现损坏任务 {name}，保留既有记录：{exc}") from exc
    return rows


def run_pre(config: dict, run_dir: str) -> dict:
    """执行本地目录前处理并返回摘要；任何单文件错误不影响后续文件。"""
    # 进入写出前完成全局配置、INPUT 和资源检查，避免错误配置产生空任务树。
    config = validate_config(config)
    resources = build_abacus_resources(config)
    input_bytes, input_origin = load_input_bytes(config)
    versions = software_versions()
    # 单层枚举不按名称筛选，源文件顺序固定；目录和链接仅记录为跳过项。
    records, skipped = discover_inputs(config["structures_root"])
    plans = build_plans(records, config)
    rows = []
    created, reused, recovered, failed = 0, 0, 0, 0
    work_root = config["work_root"]
    os.makedirs(work_root, exist_ok=True)
    os.makedirs(run_dir, exist_ok=True)

    # 工作区级互斥保护任务发布和累计索引；不按锁年龄擅自删除其他进程的锁。
    # 使用工作根互斥锁保护发布、恢复和累计表更新，避免两个进程同时改写任务。
    with FileLock(join_local(work_root, ".preprocess.lock")), WriterSession(config["writer_timeout"]) as writer:
        staging_index = scan_staging(work_root)
        aggregate_by_id = {row["task_id"]: row for row in _aggregate_rows(work_root)}
        for number, plan in enumerate(plans, 1):
            record = plan["record"]
            stage = "planning" if plan.get("planning_error") else "preflight"
            print(f"[pre {number}/{len(plans)}] {record.input_source}:{record.source_record_id}")
            try:
                # 命名失败也走原逐文件失败路径，避免一份长文件名使整批尚未开始就退出。
                if plan.get("planning_error"):
                    raise IntegrityError(plan["planning_error"])
                atoms = read_and_validate_structure(plan["source_file"], config)
                actual_elements = tuple(dict.fromkeys(atoms.get_chemical_symbols()))
                # 组成直接来自本次读取的结构；不制造样本号、优先级或预测介电信息。
                record = replace(record, formula=atoms.get_chemical_formula(empirical=True), elements=actual_elements)
                plan["record"] = record
                mapping = resources.mapping_for(actual_elements)
                # 比较已加载的实际设置，不重读源文件，不建立内容摘要。
                description = {
                    "identity": {"source": record.input_source, "namespace": config["dataset_namespace"], "id": record.source_record_id},
                    "source_file": record.source_file,
                    "input_bytes_base64": base64.b64encode(input_bytes).decode("ascii"),
                    "conversion": {key: config[key] for key in ("primitive_method", "symprec", "angle_tolerance",
                        "standardize_no_idealize", "primitive_failure_policy", "minimum_distance")},
                    "resource_mapping": mapping,
                }
                stage = "resume"
                existing = inspect_existing(plan["task_directory"], description, plan["task_id"])
                recovered_this_task = False
                if existing is None and config["reuse_existing"]:
                    existing = recover_staging(plan["task_directory"], description, plan["task_id"], staging_index)
                    recovered_this_task = existing is not None
                if existing is not None:
                    if not config["reuse_existing"]:
                        raise RuntimeError("existing_task_reuse_disabled")
                    reused += 1
                    recovered += int(recovered_this_task)
                    rows.append(_row(record, plan["task_id"], existing, status="success", stage="export", action="recovered" if recovered_this_task else "reused"))
                    aggregate_by_id[plan["task_id"]] = _row(record, plan["task_id"], existing, status="success", stage="export", action="published")
                    continue
                stage = "primitive"
                conversion = make_primitive(atoms, config)
                stage = "export"
                manifest = write_task(plan, conversion, mapping, input_bytes, input_origin,
                                      description, config, versions, writer)
                aggregate_by_id[plan["task_id"]] = _row(record, plan["task_id"], manifest, status="success", stage="export", action="published")
                created += 1
                rows.append(_row(record, plan["task_id"], manifest, status="success", stage="export", action="created"))
            except Exception as exc:
                if plan.get("_staging_directory"):
                    staging_index.setdefault(plan["task_id"], []).append(plan["_staging_directory"])
                failed += 1
                reason = f"{type(exc).__name__}:{exc}".replace("\r", " ").replace("\n", " ")
                rows.append(_row(record, plan["task_id"], stage=stage, reason=reason))
                print(f"[pre failed] {plan['task_id']}: {reason}")

        # 当前运行保存全部尝试，累计表仅从真正发布的任务记录重建，避免重跑污染旧成功。
        failures = [row for row in rows if row["status"] != "success"]
        atomic_write_csv(join_local(run_dir, "preprocess_manifest.csv"), CSV_FIELDS, rows)
        atomic_write_csv(join_local(run_dir, "preprocess_failed.csv"), CSV_FIELDS, failures)
        aggregate = [aggregate_by_id[key] for key in sorted(aggregate_by_id)]
        atomic_write_csv(join_local(work_root, "preprocess_manifest.csv"), CSV_FIELDS, aggregate)
        atomic_write_csv(join_local(work_root, "preprocess_failed.csv"), CSV_FIELDS, failures)
        summary = {
            "status": "success" if failed == 0 and bool(plans) else "partial_failed",
            "tasks_succeeded": created + reused, "tasks_created": created, "tasks_reused": reused,
            "tasks_recovered": recovered,
            "tasks_failed": failed, "input_files": len(records), "skipped_entries": skipped,
            "tasks_planned": len(plans), "total_published_tasks": len(aggregate),
            "manifest": "preprocess_manifest.csv", "failure_manifest": "preprocess_failed.csv",
            "run_manifest": "preprocess_manifest.csv", "run_id": path_basename(run_dir),
            "input_origin": input_origin, "software_versions": versions,
            "writer_process_starts": writer.starts,
            "unpublished_staging": sorted(os.path.basename(path) for paths in staging_index.values() for path in paths if os.path.isdir(path)),
        }
        # 工作根只保存稳定的相对文件名；运行位置由应用层记录，避免嵌入 Windows 绝对依赖。
        atomic_write_json(join_local(run_dir, "preprocess_summary.json"), summary)
        atomic_write_json(join_local(work_root, "preprocess_summary.json"), summary)
    return {**summary, "summary": summary}
