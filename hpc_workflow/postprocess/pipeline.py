"""整批汇总46列CSV；发布后可清理已确认终态的指定中间文件，不触发计算。"""

from collections import Counter
import os

from ..common.errors import ConflictError, IntegrityError
from ..common.paths import join_local, to_relative
from .config import output_path, validate_config
from .discovery import discover_tasks
from .readers import read_task_data
from .writer import build_csv_row, write_results_csv


OUTCOME_LABELS = {"physical_rejected": "物理终止", "technical_failed": "技术失败",
                  "input_failed": "输入处理失败"}


def _has_link(path: str) -> bool:
    """只看路径祖先是否有链接或Windows目录联接，不跟随它们读取文件。"""
    current = os.path.abspath(path)
    while True:
        if os.path.islink(current) or getattr(os.path, "isjunction", lambda item: False)(current):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def _control_paths(root: str) -> list[str]:
    """列出本批封存记录及两阶段计划/进度，不扫描庞大的DFT输出或资源库。"""
    paths = []
    for name in sorted(os.listdir(root)):
        directory = join_local(root, name)
        if name.startswith(".") or ".staging-" in name or _has_link(directory) or not os.path.isdir(directory):
            continue
        manifest = join_local(directory, "task_manifest.json")
        if os.path.lexists(manifest):
            paths.append(manifest)
    for stage in ("relax", "zstar"):
        directory = join_local(root, ".hpc_runs", stage)
        if _has_link(directory):
            paths.append(directory)
            continue
        if not os.path.isdir(directory):
            continue
        # 逐层检查后再列目录，不能先用glob穿过链接，最后才发现自己借读了别处记录。
        for run_name in sorted(os.listdir(directory)):
            run_dir = os.path.join(directory, run_name)
            if _has_link(run_dir):
                paths.append(run_dir)
                continue
            if not os.path.isdir(run_dir):
                continue
            paths.extend(os.path.join(run_dir, name) for name in ("plan.json", "jobs.csv") if os.path.lexists(os.path.join(run_dir, name)))
            batches = os.path.join(run_dir, "batches")
            if _has_link(batches):
                paths.append(batches)
                continue
            if not os.path.isdir(batches):
                continue
            for batch_name in sorted(os.listdir(batches)):
                batch_dir = os.path.join(batches, batch_name)
                if _has_link(batch_dir):
                    paths.append(batch_dir)
                elif os.path.isdir(batch_dir):
                    progress = os.path.join(batch_dir, "progress.json")
                    if os.path.lexists(progress):
                        paths.append(progress)
    return sorted(set(paths))


def _snapshot(paths: list[str]) -> dict:
    """记录有限文件的普通 stat；只提示变化，不证明文件内容未被外部修改。"""
    result = {}
    for path in sorted(set(paths)):
        if _has_link(path):
            result[path] = ("unsafe_link",)
            continue
        try:
            before = os.stat(path)
            if not os.path.isfile(path):
                result[path] = ("not_file",)
                continue
            after = os.stat(path)
            first = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            last = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            if first != last:
                raise IntegrityError(f"采集期间文件正在变化，请本批稳定后重试：{path}")
            result[path] = last
        except FileNotFoundError:
            result[path] = ("missing",)
        except (OSError, UnicodeError) as exc:
            # 文件不可读可以成为单任务的后处理失败，快照本身不能掩盖真实解析错误。
            result[path] = ("unreadable", type(exc).__name__)
    return result


def _task_paths(task: dict, config: dict) -> list[str]:
    """列举该任务实际依赖的记录、输入和物性，不遍历有限差分计算目录。"""
    directory = task["zstar_dir"]
    paths = list(task.get("record_paths", []))
    paths += [join_local(directory, *name.split("/")) for name in (
        "STRU", "INPUT", "BORN", "BEC.dat", "BEC.rep.dat",
        "dielectric_response/static_response.json", "0.no-move/zstar_insulation.json")]
    band_root = join_local(directory, *config["band_relative_root"].split("/"))
    if not _has_link(band_root):
        for parent, subdirs, files in os.walk(band_root, followlinks=False):
            subdirs[:] = sorted(name for name in subdirs if not _has_link(os.path.join(parent, name)))
            paths.extend(os.path.join(parent, name) for name in ("band.dat", "band_info.dat") if name in files)
    return sorted(set(paths))


def _check_output_scope(destination: str, root: str, tasks: list[dict]) -> None:
    """禁止把结果写进任务/控制目录；CSV发布不能修改任何计算输入树。"""
    target = os.path.realpath(destination)
    for directory in [join_local(root, ".hpc_runs"), *(task["task_dir"] for task in tasks)]:
        try:
            if os.path.commonpath((os.path.realpath(directory), target)) == os.path.realpath(directory):
                raise IntegrityError(f"结果CSV不能放入任务目录或运行控制目录：{destination}")
        except ValueError:
            # Windows不同盘符没有共同目录，明确的外部输出位置仍允许使用。
            continue


def _collect_batch(config: dict) -> tuple[dict, list[dict], list[dict]]:
    """纯只读核验整批，返回汇总、任务和46列数据；不发布CSV或创建运行锁。"""
    validate_config(config)
    root, destination = config["work_root"], output_path(config)
    if _has_link(root):
        raise IntegrityError("work_root 及其祖先不能是符号链接或目录联接。")
    before_controls = _snapshot(_control_paths(root))
    tasks = discover_tasks(config)
    # 计划在发现任务过程中变化也必须重试，不能把不同时刻的任务范围拼成完整批次。
    if _snapshot(_control_paths(root)) != before_controls:
        raise IntegrityError("发现任务期间计划或元数据发生变化，本轮未发布CSV。")
    _check_output_scope(destination, root, tasks)
    report = {"status": "valid", "work_root": root,
              "output_csv": destination, "rows_written": 0, "total": len(tasks),
              "counts": {}, "issues": [], "output_exists": os.path.lexists(destination)}
    if not tasks:
        return dict(report, status="no_tasks"), tasks, []
    unfinished = [task for task in tasks if not task["terminal"]]
    if unfinished:
        # 一份表代表完整批次。排队/运行/未知状态不能因为物性缺失而伪装成失败行。
        known = Counter(task["outcome"] for task in tasks if task["terminal"])
        known["unfinished"] = len(unfinished)
        report.update(status="incomplete", counts=dict(known))
        report["issues"] = [{"directory": task["task_dir"], "reason": task.get("reason") or "尚无已结束记录",
                             "evidence": task.get("evidence", [])} for task in unfinished]
        return report, tasks, []

    counts, rows = Counter(), []
    watched = sorted({path for task in tasks for path in _task_paths(task, config)})
    before_results = _snapshot(watched)
    for task in tasks:
        outcome = task["outcome"]
        values, errors, evidence = {}, list(task.get("metadata_errors", [])), list(task.get("evidence", []))
        # 输入关联失败或从未准备zstar输入的任务只保留身份与根因；残留产物不能借用。
        if task["provenance_status"] == "revision_linked" and not errors and outcome != "input_failed":
            data = read_task_data(task["zstar_dir"], config)
            values = data["values"]
            errors.extend(data["errors"])
            evidence.extend(data["evidence"])
            if outcome == "success" and data.get("insulation") is not True:
                errors.append("已记录成功，但当前产物缺少一致的绝缘检查通过结论")
        elif outcome == "success":
            errors.append("成功记录未通过当前输入版本关联，禁止读取可能残留的物性")
        if outcome == "success" and not errors:
            remarks, category = "", "success"
        elif outcome == "success":
            remarks = "后处理失败：" + "; ".join(errors)
            category = "post_failed"
        else:
            # 原执行根因优先。带隙/虚频拒绝后缺static属于预期后果，不再堆入CSV备注。
            remarks = OUTCOME_LABELS.get(outcome, "任务失败") + "：" + (task.get("reason") or "已结束但未报告具体原因")
            category = outcome
        metadata = dict(task.get("metadata", {}))
        metadata["zstar_relative_path"] = to_relative(task["zstar_dir"], root)
        row = build_csv_row({**task, "metadata": metadata}, values, " ".join(remarks.split())[:240])
        rows.append(row)
        counts[category] += 1
        if remarks:
            # 详细原因和原始文件地址只在终端展开，CSV保留单行短备注。
            report["issues"].append({"directory": task["task_dir"], "reason": remarks,
                                     "supplementary": errors if outcome != "success" else [],
                                     "evidence": list(dict.fromkeys(evidence))})
    after_watched = sorted({path for task in tasks for path in _task_paths(task, config)})
    # 复用本轮发现的终态与关联；控制文件范围及物性 stat 前后核对常见并发变化，
    # 不再重复全批发现。UUID 不证明外部手改未发生，stat 也不保证识别
    # 同大小、同时间戳的内容替换。
    if (_snapshot(_control_paths(root)) != before_controls
            or _snapshot(after_watched) != before_results):
        raise IntegrityError("采集期间输入、记录或结果文件发生变化，本轮未发布CSV，请稳定后重试。")
    report["counts"] = dict(counts)
    # 将本轮已读文件的轻量观察值随内存任务传给清理；不写新缓存，不重复解析物性。
    observed = {**before_controls, **before_results}
    for task in tasks:
        task["_observed_files"] = {path: observed[path] for path in _task_paths(task, config)}
    return report, tasks, rows


def run_post(config: dict, validate_only: bool = False) -> dict:
    """核验整批并发布CSV，再按配置清理；验证模式只读预览且绝不申请文件锁。"""
    validate_config(config)
    destination = output_path(config)
    if not validate_only and os.path.lexists(destination):
        raise ConflictError(f"本批结果CSV已存在，拒绝覆盖：{destination}；仅重试清理请使用 post clean。")
    from .cleanup import _controller_locks, run_cleanup
    cleanup_enabled = config.get("cleanup_completed", True)
    # 自动清理在首次采集前获取控制器锁，整轮复用同一份数据；验证模式不创建锁。
    with _controller_locks(config, inactive=validate_only or not cleanup_enabled):
        report, tasks, rows = _collect_batch(config)
        if report["status"] != "valid":
            return report
        if not validate_only:
            report["rows_written"] = write_results_csv(destination, rows)
            report["status"] = "success"
        if cleanup_enabled:
            report["cleanup"] = run_cleanup(config, preview=validate_only,
                                            collected=(report, tasks, rows), controllers_locked=True)
            # 清理失败保留已发布 CSV；独立 post clean 可重新采集后幂等重试。
            if not validate_only and report["cleanup"]["status"] not in {"success", "no_tasks"}:
                report["status"] = "partial_failed"
    return report


def run_clean(config: dict) -> dict:
    """独立重试清理已导出批次；不覆盖CSV，显式调用不受自动清理开关影响。"""
    from .cleanup import run_cleanup
    cleanup = run_cleanup(config)
    return {"status": cleanup["status"], "action": "clean", "work_root": config["work_root"],
            "output_csv": output_path(config), "rows_written": 0,
            "total": cleanup["eligible_tasks"] + cleanup["skipped_tasks"], "counts": {},
            "issues": [], "output_exists": os.path.lexists(output_path(config)), "cleanup": cleanup}


def format_summary(report: dict) -> str:
    """返回CSV与清理的中文汇总、失败依据；不生成重复日志或额外结果表。"""
    counts = report["counts"]
    lines = ["=" * 72, f"后处理批次：{report['work_root']}",
             f"状态：{report['status']}；任务总数：{report['total']}；写出行数：{report['rows_written']}",
             f"成功={counts.get('success', 0)}  物理终止={counts.get('physical_rejected', 0)}  技术失败={counts.get('technical_failed', 0)}  输入失败={counts.get('input_failed', 0)}  后处理失败={counts.get('post_failed', 0)}  未结束={counts.get('unfinished', 0)}"]
    if report.get("action") == "clean":
        lines = ["=" * 72, f"中间文件清理批次：{report['work_root']}",
                 f"状态：{report['status']}；已有结果CSV：{report['output_csv']}"]
    if report["status"] == "incomplete":
        lines.append("整批尚未确认结束；未发布最终CSV。请先检查对应任务并完成必要续算。")
    elif report["rows_written"]:
        lines.append(f"结果CSV：{report['output_csv']}（46列，总介电trace平均值从大到小）")
    elif report["status"] == "valid":
        lines.append("只读验证完成，未写出CSV。")
    for issue in report["issues"]:
        lines.extend([f"\n{issue['directory']}", "  原因：" + issue["reason"]])
        for detail in issue.get("supplementary", []):
            lines.append("  补充：" + detail)
        for path in issue.get("evidence", []):
            lines.append("  证据：" + str(path))
    if "cleanup" in report:
        cleanup = report["cleanup"]
        label = "清理只读预览" if cleanup["status"] == "preview" else "中间文件清理"
        lines.extend([f"\n{label}：{cleanup['status']}",
                      f"  符合条件结构={cleanup['eligible_tasks']}  保留结构={cleanup['skipped_tasks']}  候选文件={cleanup['candidate_files']}",
                      f"  候选逻辑大小={cleanup['candidate_bytes']} bytes  已删除={cleanup['deleted_files']}  删除逻辑大小={cleanup['deleted_bytes']} bytes"])
        # 只列需要关注的失败位置，不逐一复制所有成功删除路径或原生日志内容。
        for issue in cleanup["issues"]:
            lines.append(f"  保留/失败：{issue.get('path') or issue['directory']}：{issue['reason']}")
    lines.append("=" * 72)
    return "\n".join(lines)
