"""三种必要运行记录的读写及可迁移定位；不复制 zstar 内部状态。"""

import csv
import os

from ..common.atomic import atomic_write_csv, atomic_write_json, read_json
from ..common.errors import ConflictError, IntegrityError
from ..common.paths import ensure_host_path, from_relative, join_local
from .models import JOB_FIELDS, utc_now


class RuntimeLock:
    """内核自动释放的非阻塞运行锁；空锁文件保留稳定 inode，不作为运行记录。"""

    def __init__(self, path: str):
        """保存本机锁位置；构造不创建文件、不申请锁。"""
        self.path = ensure_host_path(path)
        self.handle = None

    def __enter__(self):
        """取得 POSIX flock 或 Windows 一字节锁；已占用明确报冲突，不按年龄抢锁。"""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if os.path.islink(self.path):
            raise IntegrityError("运行锁位置不接受符号链接")
        self.handle = open(self.path, "a+b", buffering=0)
        try:
            # Windows 的字节锁需实际存在一个字节；该文件没有状态正文，不复制进度或日志。
            if os.fstat(self.handle.fileno()).st_size == 0:
                self.handle.write(b"\0")
                self.handle.flush()
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise ConflictError(f"运行锁正被其他进程持有或文件系统不支持该锁：{self.path}") from exc
        except BaseException:
            self.handle.close()
            self.handle = None
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """释放内核锁并关闭描述符；SIGKILL/OOM 时操作系统同样自动释放。"""
        if self.handle is not None:
            try:
                self.handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            finally:
                self.handle.close()
                self.handle = None
        # 不 unlink：删除后其他进程可能锁住新 inode，让两个 worker 同时进入临界区。
        return False


def work_root_for_plan(plan_path: str) -> str:
    """从 .hpc_runs/stage/run/plan.json 布局定位当前工作根，不读历史绝对路径。"""
    path = os.path.abspath(ensure_host_path(plan_path))
    # 计划只能位于明确的固定层级；搬迁整棵 work 后仍可用同一相对布局继续。
    run_dir = os.path.dirname(path)
    stage_dir = os.path.dirname(run_dir)
    runs_dir = os.path.dirname(stage_dir)
    if (os.path.basename(path) != "plan.json" or os.path.basename(runs_dir) != ".hpc_runs"
            or os.path.basename(stage_dir) not in {"relax", "zstar"}):
        raise IntegrityError("计算计划必须位于 work/.hpc_runs/<stage>/<run>/plan.json")
    root = os.path.dirname(runs_dir)
    # 拒绝软链接把任务根或运行记录重定向到意料之外的树。
    for item in (root, runs_dir, stage_dir, run_dir, path):
        if os.path.islink(item):
            raise IntegrityError(f"计算记录不接受符号链接：{item}")
    return root


def load_plan(plan_path: str) -> dict:
    """只读校验计划结构、分组唯一性与受限任务相对引用。"""
    root = work_root_for_plan(plan_path)
    plan = read_json(plan_path)
    if (not isinstance(plan, dict) or plan.get("schema_version") not in (1, 2)
            or plan.get("stage") != os.path.basename(os.path.dirname(os.path.dirname(plan_path)))
            or plan.get("task_root") != "../../.."):
        raise IntegrityError("计划版本、阶段或任务根定位不一致")
    if plan["schema_version"] == 2 and plan.get("workflow_version") != "0.0.2":
        raise IntegrityError("计划 workflow_version 不受支持")
    # 旧协议只允许读取历史；worker/submit 会拒绝执行。
    tasks = plan.get("tasks")
    groups = plan.get("groups")
    if not isinstance(tasks, list) or not isinstance(groups, list) or not isinstance(plan.get("execution"), dict):
        raise IntegrityError("计划任务、分组或执行参数格式不正确")
    paths = [item.get("path") for item in tasks if isinstance(item, dict)]
    if len(paths) != len(tasks) or len(set(paths)) != len(paths):
        raise IntegrityError("计划任务缺少路径或包含重复路径")
    for path in paths:
        from_relative(root, path)
    if plan["schema_version"] == 2:
        from ..records.manifest import is_revision_id
        for item in tasks:
            if not item.get("input_revision_id") and item.get("status") not in {"planned", "success", "physical_rejected"}:
                continue
            subdir = item.get("calculation_subdir")
            if (not is_revision_id(item.get("input_revision_id"))
                    or not is_revision_id(item.get("source_input_revision_id"))
                    or not isinstance(item.get("scientific_input"), dict)
                    or not isinstance(subdir, str) or not subdir or subdir.startswith(".")
                    or any(value in subdir for value in "/\\:\x00\r\n")
                    or (plan["stage"] == "zstar" and not is_revision_id(item.get("source_relax_revision_id")))):
                raise IntegrityError("计划任务声明版本或科学设置格式不正确")
    planned = {item["path"] for item in tasks if item.get("status") == "planned"}
    grouped = []
    for index, group in enumerate(groups, 1):
        if (not isinstance(group, dict) or group.get("batch") != index
                or not isinstance(group.get("tasks"), list) or not group["tasks"]
                or not all(isinstance(path, str) for path in group["tasks"])):
            raise IntegrityError("批次编号必须连续且每组非空")
        grouped.extend(group["tasks"])
    if len(grouped) != len(set(grouped)) or set(grouped) != planned:
        raise IntegrityError("分组必须恰好覆盖全部 planned 任务一次")
    return plan


def list_plans(work_root: str, stage: str) -> list[str]:
    """按运行目录名排序只读列出本阶段已发布计划。"""
    directory = join_local(work_root, ".hpc_runs", stage)
    if not os.path.isdir(directory):
        return []
    # 不递归走任意目录，只识别固定运行布局；损坏计划由调用方明确报告。
    return [join_local(directory, name, "plan.json") for name in sorted(os.listdir(directory))
            if os.path.isfile(join_local(directory, name, "plan.json"))]


def batch_directory(plan_path: str, batch: int) -> str:
    """返回某批次运行目录；本函数只定位，不创建。"""
    if isinstance(batch, bool) or not isinstance(batch, int) or batch < 1:
        raise IntegrityError("batch 必须为从 1 开始的整数")
    return join_local(os.path.dirname(plan_path), "batches", f"{batch:04d}")


def read_jobs(plan_path: str) -> list[dict]:
    """只读 jobs.csv；尚未提交的计划返回空列表。"""
    path = join_local(os.path.dirname(plan_path), "jobs.csv")
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != list(JOB_FIELDS):
                raise IntegrityError(f"jobs.csv 列定义不正确：{path}")
            rows = list(reader)
            batches = [row.get("batch", "") for row in rows]
            if (any(not value.isdigit() or int(value) < 1 for value in batches)
                    or len(batches) != len(set(batches))
                    or any(row.get("submit_status") not in {"SUBMITTING", "SUBMITTED", "FAILED", "UNKNOWN"} for row in rows)):
                raise IntegrityError(f"jobs.csv 批次重复或状态不正确：{path}")
            return rows
    except FileNotFoundError:
        return []


def write_jobs(plan_path: str, rows: list[dict]) -> None:
    """在控制器锁内原子更新唯一的提交台账。"""
    atomic_write_csv(join_local(os.path.dirname(plan_path), "jobs.csv"), JOB_FIELDS, rows)


def read_progress(plan_path: str, batch: int) -> dict:
    """只读每批进度；worker 尚未启动时返回空字典。"""
    try:
        result = read_json(join_local(batch_directory(plan_path, batch), "progress.json"))
    except FileNotFoundError:
        return {}
    if not isinstance(result, dict) or result.get("batch") != batch:
        raise IntegrityError("progress.json 批次或格式不正确")
    finished = result.get("finished", [])
    if not isinstance(finished, list) or not all(isinstance(task, dict) and isinstance(task.get("path"), str) for task in finished):
        raise IntegrityError("progress.json 已结束结构格式不正确")
    paths = [task["path"] for task in finished]
    if len(paths) != len(set(paths)):
        raise IntegrityError("progress.json 包含重复的已结束结构")
    return result


def write_progress(plan_path: str, batch: int, progress: dict) -> None:
    """原子发布当前批次边界事件；不定时写入队列快照。"""
    progress["updated_at"] = utc_now()
    atomic_write_json(join_local(batch_directory(plan_path, batch), "progress.json"), progress)
