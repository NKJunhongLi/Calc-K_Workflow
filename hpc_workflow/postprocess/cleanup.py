"""只清理当前版本明确完成任务的指定中间文件；先保全结果，再释放存储。"""

from contextlib import ExitStack, contextmanager
import csv
import fnmatch
import io
import os
import stat

from ..calculations.relax import calculation_directory, classify_failure, revision_matches
from ..common.errors import ConflictError, IntegrityError, WorkflowError
from ..common.paths import join_local
from ..submission.state import (RuntimeLock, batch_directory, load_plan,
                                read_jobs, read_progress)
from .config import output_path, validate_config
from .metadata import resolve_metadata
from .discovery import REVISION_FIELDS
from .writer import build_fieldnames


SPARSE_NAMES = frozenset({"data-rR-sparse.csr", "data-HR-sparse_SPIN0.csr",
                          "data-SR-sparse_SPIN0.csr"})
PHYSICAL_REASONS = frozenset({"能带绝缘检查不通过", "Gamma 点存在明显虚频"})


def _signature(info: os.stat_result) -> tuple:
    """返回不读取大文件正文的身份签名；不用跨平台语义不同的 ctime。"""
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            stat.S_IFMT(info.st_mode))


def _is_reparse(path: str) -> bool:
    """拒绝软链接和 Windows reparse point，兼容尚无 isjunction 的 Python 3.10。"""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _checked_path(root: str, path: str, task_dir: str | None = None) -> str:
    """核对实际路径仍在工作根及指定任务内，逐层拒绝链接，不创建任何目录。"""
    absolute, boundary = os.path.abspath(path), os.path.abspath(root)
    # 先检查字面边界，防止在核验过程中借助 realpath 探查不相关的目录树。
    for base in (boundary, os.path.abspath(task_dir) if task_dir else boundary):
        try:
            contained = os.path.commonpath((os.path.normcase(base), os.path.normcase(absolute))) == os.path.normcase(base)
        except ValueError:
            contained = False
        if not contained:
            raise IntegrityError(f"清理路径越出所属任务或工作根：{path}")
    current = absolute
    while True:
        if _is_reparse(current):
            raise IntegrityError(f"清理路径不接受符号链接、目录联接或 reparse point：{current}")
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    # 路径没有链接后，仍复核实际边界，避免盘符/大小写或目录移动造成误判。
    resolved = os.path.normcase(os.path.realpath(absolute))
    for base in (boundary, os.path.abspath(task_dir) if task_dir else boundary):
        if os.path.commonpath((os.path.normcase(os.path.realpath(base)), resolved)) != os.path.normcase(os.path.realpath(base)):
            raise IntegrityError(f"清理路径的实际位置越界：{path}")
    return absolute


def _canonical_rows(rows: list[dict]) -> dict[str, tuple[str, ...]]:
    """按标准 CSV 字符串转换全部46列，并以唯一任务名建立与排序无关的索引。"""
    fields, result = build_fieldnames(), {}
    for row in rows:
        if set(row) != set(fields):
            raise IntegrityError("清理前结果行不符合46列完整表头。")
        # csv.writer 的转换与最终 DictWriter 完全一致，不能自行格式化浮点数。
        stream = io.StringIO(newline="")
        csv.writer(stream).writerow([row[field] for field in fields])
        converted = tuple(next(csv.reader(io.StringIO(stream.getvalue(), newline=""))))
        name = converted[fields.index("task_name")]
        if not name or name in result:
            raise IntegrityError("结果表任务名为空或重复，拒绝清理。")
        result[name] = converted
    return result


def _read_published_csv(path: str) -> dict[str, tuple[str, ...]]:
    """只读核验已有 CSV 的完整表头、行宽和唯一性，拒绝链接及读取期间变化。"""
    current = os.path.abspath(path)
    while True:
        if _is_reparse(current):
            raise IntegrityError(f"结果CSV及其祖先不能是链接：{current}")
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode):
        raise IntegrityError("清理前结果CSV必须为普通文件。")
    fields, result = build_fieldnames(), {}
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        if _signature(os.fstat(handle.fileno())) != _signature(before):
            raise IntegrityError("打开结果CSV期间文件发生替换。")
        reader = csv.reader(handle, strict=True)
        if tuple(next(reader, ())) != fields:
            raise IntegrityError("清理前结果CSV表头必须与当前46列完全一致。")
        for row in reader:
            if len(row) != len(fields):
                raise IntegrityError("清理前结果CSV存在缺列、多列或空白记录。")
            name = row[fields.index("task_name")]
            if not name or name in result:
                raise IntegrityError("清理前结果CSV任务名为空或重复。")
            result[name] = tuple(row)
        after_handle = os.fstat(handle.fileno())
    if (_signature(before) != _signature(after_handle)
            or _signature(before) != _signature(os.lstat(path))):
        raise IntegrityError("读取结果CSV期间文件发生变化。")
    return result


def _guarded_snapshot(paths: list[str]) -> dict:
    """快照前后核对所有祖先，兼容旧 Python 的 Windows reparse 检测。"""
    from .pipeline import _snapshot

    for path in paths:
        current = os.path.abspath(path)
        while True:
            if _is_reparse(current):
                raise IntegrityError(f"清理依赖的输入、结果或记录成为链接：{current}")
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    result = _snapshot(paths)
    # 检查过程中若有人换成链接，不接受替换后的元数据作为合法基线。
    for path in paths:
        current = os.path.abspath(path)
        while True:
            if _is_reparse(current):
                raise IntegrityError(f"清理快照期间路径成为链接：{current}")
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
    return result


def _guard_unchanged(paths: list[str], baseline: dict) -> bool:
    """逐文件删除前仅比较 lstat 身份和祖先链接，不反复读取已核验的小结果正文。"""
    for path in paths:
        current = os.path.abspath(path)
        while True:
            if _is_reparse(current):
                raise IntegrityError(f"清理依赖路径成为链接：{current}")
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        expected = baseline[path]
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            if expected != ("missing",):
                return False
            continue
        if expected == ("not_file",):
            if stat.S_ISREG(info.st_mode):
                return False
        elif expected and isinstance(expected[0], int):
            if (not stat.S_ISREG(info.st_mode)
                    or (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != expected[:4]):
                return False
        else:
            # 少数先前不可读的产物仍沿用完整核验；常规正常文件只做轻量元数据检查。
            if _guarded_snapshot([path]).get(path) != expected:
                return False
    return True


def _eligibility(task: dict, row: dict) -> tuple[bool, str]:
    """只允许可信成功或两类明确物理终止；技术失败和后处理失败保留续算材料。"""
    if (not task.get("terminal") or task.get("provenance_status") != "revision_linked"
            or task.get("metadata_errors")):
        return False, "终态或当前输入版本关联不可靠，保留中间文件"
    if task.get("outcome") == "success":
        return (True, "介电计算完成且后处理校验通过") if not row.get("remarks") else (
            False, "成功产物尚未通过后处理校验，保留中间文件")
    if task.get("outcome") == "physical_rejected":
        reason = task.get("reason", "")
        if not isinstance(reason, str):
            return False, "物理终止原因不是有效字符串，保留中间文件"
        classified = classify_failure(reason)
        canonical = (classified or {}).get("reason", reason)
        if canonical in PHYSICAL_REASONS:
            return True, canonical
        return False, "物理终止原因不属于明确带隙拒绝或明显虚频，保留中间文件"
    return False, "技术失败、输入失败或未结束任务仍可能需要续算"


def _task_history_records(root: str, task: dict) -> dict[str, dict]:
    """取本轮共享历史；仅兼容直接调用且无缓存的旧辅助接口时读取记录。"""
    if "_history_records" in task:
        return task["_history_records"]
    # 正常 collect/自动清理/独立 clean 都带本轮索引，不会走此回退。
    # 回退只读取该任务参与的组，仍保留原 progress 供终态语义复核。
    records, name = {}, task["task_name"]
    for path in sorted(set(task.get("record_paths", []))):
        if os.path.basename(path) != "plan.json":
            continue
        _checked_path(root, path)
        plan = load_plan(path)
        tasks = {entry["path"]: entry for entry in plan["tasks"]}
        if name not in tasks:
            continue
        memberships, progresses, finished = {}, {}, {}
        for group in plan["groups"]:
            if name not in group["tasks"]:
                continue
            batch = group["batch"]
            _checked_path(root, join_local(batch_directory(path, batch), "progress.json"))
            progress = read_progress(path, batch)
            memberships[name] = batch
            progresses[batch] = progress
            finished.update({entry["path"]: entry for entry in progress.get("finished", [])})
        _checked_path(root, join_local(os.path.dirname(path), "jobs.csv"))
        records[path] = {"path": path, "plan": plan, "tasks": tasks,
                         "memberships": memberships, "progress": progresses, "finished": finished,
                         "jobs": {int(row["batch"]): row for row in read_jobs(path)}}
    return records


def _history_info(root: str, task: dict) -> tuple[list[str], list[str]]:
    """收集本结构全部历史布局和必要记录；未结束的已提交旧任务也必须阻止清理。"""
    directories = set()
    records = []
    name = task["task_name"]
    # 最新执行参数里的两阶段路径也纳入锁，防止只锁 zstar 却与新的 relax 并发。
    for stage in ("relax", "zstar"):
        directory = calculation_directory(task["task_dir"], task["execution"], stage)
        _checked_path(root, directory, task["task_dir"])
        if os.path.lexists(directory):
            if not os.path.isdir(directory):
                raise IntegrityError(f"计算阶段路径不是目录：{directory}")
            directories.add(directory)
    for path, record in sorted(_task_history_records(root, task).items()):
        _checked_path(root, path)
        plan = record["plan"]
        if name not in record["tasks"]:
            continue
        records.append(path)
        directory = calculation_directory(task["task_dir"], plan["execution"], plan["stage"])
        _checked_path(root, directory, task["task_dir"])
        if os.path.lexists(directory):
            if not os.path.isdir(directory):
                raise IntegrityError(f"历史阶段路径不是目录：{directory}")
            directories.add(directory)
        jobs_path = join_local(os.path.dirname(path), "jobs.csv")
        _checked_path(root, jobs_path)
        records.append(jobs_path)
        batch = record["memberships"].get(name)
        if batch is not None:
            progress_path = join_local(batch_directory(path, batch), "progress.json")
            _checked_path(root, progress_path)
            records.append(progress_path)
            progress = record["progress"][batch]
            finished = (plan.get("schema_version") == 2 and plan.get("workflow_version") == "0.0.2"
                        and progress.get("schema_version") == 2 and progress.get("workflow_version") == "0.0.2"
                        and name in record["finished"])
            job = record["jobs"].get(batch, {})
            if not finished and job.get("submit_status") in {"SUBMITTED", "SUBMITTING", "UNKNOWN"}:
                raise ConflictError(f"历史计划仍有未确认结束的已提交成员，保留续算文件：{path}")
            if progress.get("current_task") == name:
                raise ConflictError(f"历史进度仍将本结构标记为当前计算任务：{progress_path}")
    return sorted(directories), sorted(set(records))


def _verify_terminal_record(root: str, task: dict) -> None:
    """复核真正执行计划中的原终态，不用最新 CSV 或旧成功覆盖新进度。"""
    plans = [path for path in task.get("evidence", []) if isinstance(path, str)
             and os.path.isabs(path) and os.path.basename(path) == "plan.json"]
    if not plans:
        raise IntegrityError("明确完成任务缺少可复核的执行计划证据。")
    path = _checked_path(root, plans[0])
    record = _task_history_records(root, task).get(path)
    if record is None:
        raise IntegrityError("清理前执行计划不在本轮已观察的任务历史中。")
    plan = record["plan"]
    entry = record["tasks"].get(task["task_name"])
    if (entry is None or plan.get("schema_version") != 2 or plan.get("workflow_version") != "0.0.2"
            or plan["stage"] != "zstar" or plan["execution"] != task["execution"]
            or any(entry.get(key) != task["revision_entry"].get(key) for key in REVISION_FIELDS)):
        raise IntegrityError("清理前执行计划、阶段或冻结参数已经改变。")
    event = dict(entry)
    batch = record["memberships"].get(task["task_name"])
    if batch is not None:
        progress = record["progress"][batch]
        if progress.get("schema_version") != 2 or progress.get("workflow_version") != "0.0.2":
            raise IntegrityError("清理前进度记录不属于当前协议。")
        # read_progress 已拒绝重复成员，发现阶段也已核对所属批次与五个关联字段；
        # 这里复用该唯一原记录，不为每个成员重新扫描整组 finished 列表。
        finished = record["finished"].get(task["task_name"])
        if finished is None:
            raise IntegrityError("清理前本结构缺少唯一已结束记录。")
        event.update(finished)
    if (event.get("status") != task["outcome"]
            or event.get("reason", "") != task.get("reason", "")
            or any(event.get(key) != task["revision_entry"].get(key) for key in REVISION_FIELDS)):
        raise IntegrityError("清理前任务终态、根因或版本关联发生改变。")


def _verify_task(root: str, task: dict, row: dict, config: dict) -> None:
    """锁内复核终态、版本与本轮 stat 观察值；复用物性解析，不重读整套结果。"""
    _checked_path(root, task["task_dir"], task["task_dir"])
    _checked_path(root, task["zstar_dir"], task["task_dir"])
    # 普通 stat 仅提示变化；同大小同时间戳手改不在此保证范围内。
    observed = task.get("_observed_files")
    if not observed or _guarded_snapshot(list(observed)) != observed:
        raise IntegrityError("清理前本轮已观察的记录、输入或物性发生变化。")
    # 先确认采集时的控制记录仍未出现可观察变化，才使用内存中的原终态。
    _verify_terminal_record(root, task)
    current_metadata = resolve_metadata(task["task_dir"])
    current_metadata["zstar_relative_path"] = task["zstar_relative_path"]
    if current_metadata != task["metadata"]:
        raise IntegrityError("清理前前处理元数据发生变化。")
    # 内存索引与冻结参数分别保存，不能让临时优化字段改变计划语义比较。
    revision_config = {**task["execution"], "_relax_revision_index": task.get("_relax_revision_index")}
    if not revision_matches(task["task_dir"], revision_config, "zstar", task["revision_entry"]):
        raise IntegrityError("清理前当前来源版本与冻结计划不一致。")


def _enumerate_candidates(root: str, task_dir: str) -> tuple[list[tuple], list[dict]]:
    """不跟随链接递归列出四种目标普通文件；枚举失败留下明确路径，不扩大匹配。"""
    candidates, issues, stack = [], [], [task_dir]
    while stack:
        directory = stack.pop()
        try:
            _checked_path(root, directory, task_dir)
            with os.scandir(directory) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
            for entry in entries:
                path = entry.path
                matched = entry.name in SPARSE_NAMES or fnmatch.fnmatchcase(entry.name, "*DENSITY.restart")
                try:
                    if _is_reparse(path):
                        # 赝势等不匹配的文件链接不属于清理对象；链接目录不进入。
                        if matched or entry.is_dir(follow_symlinks=False):
                            issues.append({"directory": task_dir, "path": path, "reason": "链接或目录联接已保留，未进入或删除"})
                        continue
                    # Windows DirEntry 缓存 stat 的 inode/dev 可能为零；必须与删除前
                    # 使用同一种 lstat 来源，否则普通未变化文件也会被误报替换。
                    info = os.lstat(path)
                    if stat.S_ISDIR(info.st_mode):
                        stack.append(path)
                    elif matched and stat.S_ISREG(info.st_mode):
                        candidates.append((_checked_path(root, path, task_dir), _signature(info)))
                    elif matched:
                        issues.append({"directory": task_dir, "path": path, "reason": "目标不是普通文件，已保留"})
                except OSError as exc:
                    issues.append({"directory": task_dir, "path": path, "reason": str(exc)})
        except (OSError, WorkflowError) as exc:
            issues.append({"directory": task_dir, "path": directory, "reason": str(exc)})
    return sorted(candidates), issues


def _unlink_candidate(root: str, task_dir: str, path: str, signature: tuple) -> bool:
    """逐文件复核身份后 unlink；POSIX 锚定父目录描述符，缺失作为幂等跳过。"""
    _checked_path(root, path, task_dir)
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(before.st_mode) or _signature(before) != signature:
        raise IntegrityError(f"待清理文件在枚举后发生变化，保留该文件：{path}")
    parent = os.path.dirname(path)
    if os.name != "nt" and os.unlink in os.supports_dir_fd and os.stat in os.supports_dir_fd:
        # 父目录最终一级同样不能是链接；打开后保持 fd，避免再次按变动的完整路径删除。
        before_parent = os.lstat(parent)
        descriptor = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (before_parent.st_dev, before_parent.st_ino):
                raise IntegrityError("清理期间父目录发生替换，停止该文件。")
            _checked_path(root, path, task_dir)
            try:
                current = os.stat(os.path.basename(path), dir_fd=descriptor, follow_symlinks=False)
                if _signature(current) != signature or not stat.S_ISREG(current.st_mode):
                    raise IntegrityError("清理期间文件身份改变，保留该文件。")
                os.unlink(os.path.basename(path), dir_fd=descriptor)
            except FileNotFoundError:
                return False
        finally:
            os.close(descriptor)
    else:
        # Windows 没有 dir_fd；复核全部祖先与最新 lstat，再用同一个普通文件绝对路径。
        _checked_path(root, path, task_dir)
        try:
            if _signature(os.lstat(path)) != signature:
                raise IntegrityError("清理期间文件身份改变，保留该文件。")
            os.unlink(path)
        except FileNotFoundError:
            return False
    return True


def _new_summary(config: dict, preview: bool) -> dict:
    """建立精简内存汇总，不新增清理日志、CSV或manifest。"""
    return {"status": "preview" if preview else "success", "work_root": config["work_root"],
            "output_csv": output_path(config), "eligible_tasks": 0, "skipped_tasks": 0,
            "candidate_files": 0, "candidate_bytes": 0, "deleted_files": 0,
            "deleted_bytes": 0, "missing_files": 0, "issues": [], "tasks": []}


def _process_task(config: dict, task: dict, row: dict, summary: dict, preview: bool,
                  published_snapshot: dict | None = None) -> None:
    """处理一个结构；锁、读写或删除失败仅记录当前路径，不阻止其它结构继续检查。"""
    from .pipeline import _task_paths

    root, task_dir = config["work_root"], task["task_dir"]
    eligible, reason = _eligibility(task, row)
    item = {"directory": task_dir, "eligible": eligible, "reason": reason,
            "candidate_files": 0, "candidate_bytes": 0, "deleted_files": 0,
            "deleted_bytes": 0, "missing_files": 0}
    summary["tasks"].append(item)
    if not eligible:
        summary["skipped_tasks"] += 1
        return
    counted = False
    try:
        directories, history_paths = _history_info(root, task)
        with ExitStack() as locks:
            if not preview:
                # 所有历史冻结布局使用相同排序，且复用 worker 内核锁，不用自建年龄锁。
                for directory in directories:
                    path = _checked_path(root, join_local(directory, ".hpc-worker.lock"), task_dir)
                    locks.enter_context(RuntimeLock(path))
                    _checked_path(root, path, task_dir)
            # 只保留本结构输入/物性和它参与的历史控制文件，逐文件比较普通 stat。
            watched = [path for path in _task_paths(task, config)
                       if os.path.commonpath((os.path.abspath(path), os.path.abspath(task_dir))) == os.path.abspath(task_dir)]
            watched += [*history_paths, output_path(config)] if not preview else history_paths
            before = _guarded_snapshot(watched)
            if published_snapshot is not None and _guarded_snapshot([output_path(config)]) != published_snapshot:
                raise IntegrityError("结果CSV在整批校验后发生改变，拒绝清理本结构。")
            _verify_task(root, task, row, config)
            candidates, issues = _enumerate_candidates(root, task_dir)
            if _guarded_snapshot(watched) != before:
                raise IntegrityError("枚举清理文件期间关键输入、终态或物性发生变化，保留本结构文件。")
            item["candidate_files"] = len(candidates)
            item["candidate_bytes"] = sum(signature[2] for _, signature in candidates)
            summary["eligible_tasks"] += 1
            counted = True
            summary["issues"].extend(issues)
            for key in ("candidate_files", "candidate_bytes"):
                summary[key] += item[key]
            if preview:
                return
            for path, signature in candidates:
                # 锁保护正常工作流；逐文件只做身份/时间戳检查，避免数百位移输出导致
                # 同一CSV和BEC正文被反复读取。开头/枚举后/末尾均核对普通 stat。
                if not _guard_unchanged(watched, before):
                    summary["issues"].append({"directory": task_dir, "path": path,
                                               "reason": "关键输入、结果或终态改变，停止本结构剩余清理"})
                    break
                try:
                    if _unlink_candidate(root, task_dir, path, signature):
                        item["deleted_files"] += 1
                        item["deleted_bytes"] += signature[2]
                        summary["deleted_files"] += 1
                        summary["deleted_bytes"] += signature[2]
                    else:
                        item["missing_files"] += 1
                        summary["missing_files"] += 1
                except (OSError, WorkflowError) as exc:
                    # 单个只读/权限受限文件不能使本批其余结构失去释放存储的机会。
                    summary["issues"].append({"directory": task_dir, "path": path, "reason": str(exc)})
            if _guarded_snapshot(watched) != before:
                summary["issues"].append({"directory": task_dir, "path": task_dir,
                                           "reason": "清理结束时关键输入、结果或终态发生变化，请检查本结构"})
    except (OSError, ValueError, UnicodeError, WorkflowError) as exc:
        # 已实际进入清理的结构仍只计一次；中途快照失败不能同时算成 eligible 和 skipped。
        if not counted:
            item.update(eligible=False, reason=str(exc))
            summary["skipped_tasks"] += 1
        else:
            item["reason"] = str(exc)
        summary["issues"].append({"directory": task_dir, "path": task_dir, "reason": str(exc)})


@contextmanager
def _controller_locks(config: dict, inactive: bool = False):
    """在同一锁范围内完成采集、发布和清理；inactive 用于严格只读预览。"""
    with ExitStack() as stack:
        if not inactive:
            root = _checked_path(config["work_root"], config["work_root"])
            for stage in ("relax", "zstar"):
                path = _checked_path(root, join_local(root, ".hpc_runs", f".{stage}.controller.lock"))
                stack.enter_context(RuntimeLock(path))
                _checked_path(root, path)
        yield


def run_cleanup(config: dict, preview: bool = False, expected_rows: list[dict] | None = None,
                *, collected: tuple | None = None, controllers_locked: bool = False) -> dict:
    """复用本轮采集或独立采集一次；真实清理必须匹配已发布46列CSV。"""
    # 延迟导入，避免 pipeline 自动收集完成后调用清理时产生循环导入。
    from .pipeline import _collect_batch

    summary = _new_summary(config, preview)
    try:
        validate_config(config)
        root = _checked_path(config["work_root"], config["work_root"])
        if collected is not None and not preview and not controllers_locked:
            raise IntegrityError("复用采集数据的清理必须处于首次采集开始的控制器锁范围内。")
        with _controller_locks(config, inactive=preview or controllers_locked):
            report, tasks, rows = collected if collected is not None else _collect_batch(config)
            if report["status"] == "no_tasks":
                summary["status"] = "no_tasks"
                return summary
            if report["status"] not in {"valid", "success"}:
                raise ConflictError("整批任务尚未确认结束，当前不执行中间文件清理。")
            canonical = _canonical_rows(rows)
            if expected_rows is not None and _canonical_rows(expected_rows) != canonical:
                raise IntegrityError("结果发布后本批结果行发生改变，拒绝按旧CSV清理。")
            published_snapshot = None
            if not preview:
                # 必须在整表比对之前建立 CSV 基线；不能等到单任务开始时才把已被
                # 人工修改的文件当成新基线。此同一版本持续传到每个删除临界区。
                published_snapshot = _guarded_snapshot([output_path(config)])
                if (_read_published_csv(output_path(config)) != canonical
                        or _guarded_snapshot([output_path(config)]) != published_snapshot):
                    raise IntegrityError("已有结果CSV与当前整批46列数据不一致或正在变化，拒绝清理。")
            indexed = {row["task_name"]: row for row in rows}
            for task in tasks:
                _process_task(config, task, indexed[task["task_name"]], summary, preview, published_snapshot)
            if summary["issues"] and not preview:
                summary["status"] = "partial_failed"
    except (OSError, ValueError, UnicodeError, csv.Error, WorkflowError) as exc:
        summary["status"] = "blocked"
        summary["issues"].append({"directory": config["work_root"], "path": output_path(config), "reason": str(exc)})
    return summary
