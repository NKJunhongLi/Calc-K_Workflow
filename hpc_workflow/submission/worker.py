"""单 Slurm 作业逐结构执行；单结构失败不截断串行批次。"""

import os
import shlex
import subprocess
import sys
import threading

from ..common.errors import IntegrityError
from ..common.paths import join_local, to_relative
from .discovery import checked_task_directory
from .models import short_reason, utc_now
from .planner import calculations_for
from .state import RuntimeLock, batch_directory, load_plan, read_progress, work_root_for_plan, write_progress


def _emit(stream, data: bytes) -> None:
    """将子进程输出原样转发一次，兼容终端字节流和测试文本流。"""
    binary = getattr(stream, "buffer", None)
    if binary is not None:
        binary.write(data)
        binary.flush()
    else:
        stream.write(data.decode("utf-8", errors="replace"))
        stream.flush()


def _stream_command(args: list[str], cwd: str) -> tuple[int, str]:
    """并发排空 stdout/stderr 到 Slurm 原始日志，只在内存保留有界错误尾部。"""
    process = subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    tails = []
    failures = []
    guard = threading.Lock()

    def pump(pipe, destination):
        """排空一个管道；每路不超过 128 KiB，避免长时 DFT 输出占满内存。"""
        tail = bytearray()
        try:
            with pipe:
                while True:
                    data = pipe.read1(8192)
                    if not data:
                        break
                    _emit(destination, data)
                    tail.extend(data)
                    del tail[:-131072]
        except Exception as exc:
            failures.append(exc)
            # 日志目的端失效时停止生产者，避免子进程继续填满无人排空的管道造成僵持。
            if process.poll() is None:
                process.terminate()
        finally:
            with guard:
                tails.append(bytes(tail))

    threads = [threading.Thread(target=pump, args=(process.stdout, sys.stdout), daemon=True),
               threading.Thread(target=pump, args=(process.stderr, sys.stderr), daemon=True)]
    for thread in threads:
        thread.start()
    try:
        returncode = process.wait()
    except BaseException:
        # 控制器中断时停止当前直接子进程并排空输出；远端 MPI 全组清理由 Slurm 作业处理。
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        raise
    finally:
        for thread in threads:
            thread.join()
    if failures:
        raise IntegrityError(f"原始日志转发失败：{failures[0]}")
    return returncode, b"\n".join(tails).decode("utf-8", errors="replace")


def run_worker(stage: str, plan_path: str, batch: int) -> dict:
    """按冻结计划串行遍历本批全部结构；只把当前结构标成计算中。"""
    plan = load_plan(plan_path)
    if plan.get("schema_version") != 2 or plan.get("workflow_version") != "0.0.2":
        raise IntegrityError("旧协议计划仅可查看，请在新阶段目录重新规划")
    if plan["stage"] != stage:
        raise IntegrityError("worker 阶段与计划不一致")
    groups = {group["batch"]: group for group in plan["groups"]}
    if batch not in groups:
        raise IntegrityError("worker batch 不存在")
    root = work_root_for_plan(plan_path)
    config = {**plan["execution"], "work_root": root, "stage": stage}
    if stage == "zstar":
        from ..calculations.relax import build_relax_revision_index
        config["_relax_revision_index"] = build_relax_revision_index(root, config.get("relax_subdir", "cell-relax"))
    _, _, execute = calculations_for(stage)
    tasks = {task["path"]: task for task in plan["tasks"]}
    directory = batch_directory(plan_path, batch)
    # 同一批次只允许一个 worker；跨计划另有逐任务锁，避免重复 sbatch 或人工启动并跑。
    with RuntimeLock(join_local(directory, ".worker.lock")):
        existing = read_progress(plan_path, batch)
        from ..calculations.relax import REVISION_FIELDS
        if existing:
            if existing.get("schema_version") != 2 or existing.get("workflow_version") != "0.0.2":
                raise IntegrityError("批次进度不属于当前协议，不能继续执行")
            for event in existing.get("finished", []):
                original = tasks.get(event["path"])
                if (event["path"] not in groups[batch]["tasks"] or original is None
                        or any(event.get(key) != original.get(key) for key in REVISION_FIELDS)):
                    raise IntegrityError("批次终态与计划声明版本不一致")
        progress = {"schema_version": 2, "workflow_version": "0.0.2", "batch": batch, "status": "running", "current_task": None,
                    "current_command": None, "current_started_at": None,
                    "started_at": existing.get("started_at", utc_now()), "finished": existing.get("finished", [])}
        ended = {task["path"] for task in progress["finished"]}
        write_progress(plan_path, batch, progress)
        for relative in groups[batch]["tasks"]:
            if relative in ended:
                continue
            progress.update(current_task=relative, current_command=None, current_started_at=utc_now())
            write_progress(plan_path, batch, progress)
            marker = f"\n===== TASK {relative} BEGIN {progress['current_started_at']} =====\n"
            _emit(sys.stdout, marker.encode("utf-8"))
            _emit(sys.stderr, marker.encode("utf-8"))
            try:
                task_dir = checked_task_directory(root, relative)
                subdir = config.get("relax_subdir", "cell-relax") if stage == "relax" else config.get("zstar_subdir", "zstar")
                calculation_dir = join_local(task_dir, subdir)
                if not os.path.isdir(calculation_dir) or os.path.islink(calculation_dir):
                    raise IntegrityError("计算目录不存在或为符号链接，必须重新规划可靠输入")
                with RuntimeLock(join_local(calculation_dir, ".hpc-worker.lock")):
                    # 只比较声明版本，不声称能够发现没有更新版本的人工文件改写。
                    from ..calculations.relax import revision_matches, REVISION_FIELDS
                    if not revision_matches(task_dir, config, stage, tasks[relative]):
                        raise IntegrityError("排队后声明输入版本不一致，停止该结构")
                    task_config = {**config, "_revision_entry": tasks[relative]}

                    def on_command(label, args, cwd):
                        """发布外层命令边界，输出有分隔的唯一原始日志并返回可归因结果。"""
                        progress["current_command"] = str(label)
                        write_progress(plan_path, batch, progress)
                        # 真实资源占位已在此刻展开；只在原日志留一次可复现命令，不扩充台账。
                        text = f"\n----- TASK {relative} COMMAND {label} BEGIN -----\nARGV: {shlex.join(args)}\n"
                        _emit(sys.stdout, text.encode("utf-8"))
                        _emit(sys.stderr, text.encode("utf-8"))
                        try:
                            code, tail = _stream_command(args, cwd)
                        except Exception as exc:
                            code, tail = 127, f"{type(exc).__name__}: {exc}"
                            _emit(sys.stderr, (tail + "\n").encode("utf-8"))
                        end = f"\n----- TASK {relative} COMMAND {label} END rc={code} -----\n"
                        _emit(sys.stdout, end.encode("utf-8"))
                        _emit(sys.stderr, end.encode("utf-8"))
                        evidence = [to_relative(join_local(directory, f"slurm-{os.environ.get('SLURM_JOB_ID', 'manual')}.{suffix}"), root)
                                    for suffix in ("out", "err")]
                        value = {"returncode": code, "evidence": evidence, "output_tail": tail}
                        if code:
                            # 只读取原始输出尾部的明确原因，不把所有命令失败视为互不相关的三个故障。
                            try:
                                from ..calculations.zstar import classify_failure
                                classified = classify_failure(tail)
                            except (ImportError, AttributeError):
                                classified = None
                            value["reason"] = short_reason((classified or {}).get("reason") or tail or f"命令退出码 {code}")
                            if classified:
                                value.update({key: val for key, val in classified.items() if key != "evidence"})
                        return value

                    result = execute(task_dir, task_config, on_command)
                    if not isinstance(result, dict):
                        raise IntegrityError("计算适配器未返回结果字典")
            except Exception as exc:
                # 锁冲突、输入变动和命令技术故障只结束当前结构，后续成员仍逐个遍历。
                result = {"status": "technical_failed", "reason": short_reason(f"{type(exc).__name__}: {exc}")}
                if isinstance(exc, IntegrityError):
                    result["status"] = "input_failed"
            compact = {key: result[key] for key in ("status", "reason", "evidence", "step", "supplementary") if key in result}
            from ..calculations.relax import REVISION_FIELDS
            compact.update(path=relative, **{key: tasks[relative][key] for key in REVISION_FIELDS if key in tasks[relative]})
            progress["finished"].append(compact)
            ended.add(relative)
            progress.update(current_task=None, current_command=None, current_started_at=None)
            write_progress(plan_path, batch, progress)
            end = f"\n===== TASK {relative} END {compact.get('status', 'technical_failed')} =====\n"
            _emit(sys.stdout, end.encode("utf-8"))
            _emit(sys.stderr, end.encode("utf-8"))
        progress["status"] = "finished"
        write_progress(plan_path, batch, progress)
        failed = any(task.get("status") in {"technical_failed", "input_failed"} for task in progress["finished"])
        return {"status": "partial_failed" if failed else "success", "batch": batch, "finished": progress["finished"]}
