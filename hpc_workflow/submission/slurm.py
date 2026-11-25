"""仅通过 Slurm 申请和查询资源；不在 Python 中分配节点、核数或并行池。"""

import os
import re
import subprocess

from ..common.paths import join_local, project_root, to_relative
from .models import short_reason, utc_now
from .state import batch_directory


def _scheduler(argv: list[str], config: dict) -> subprocess.CompletedProcess:
    """执行短时调度器命令并捕获其小体量响应，不调用 shell。"""
    return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          errors="replace", timeout=config.get("command_timeout", 30.0), check=False)


def query_jobs(job_ids: list[str], config: dict) -> dict[str, dict]:
    """联合 squeue/sacct 查询已知 job ID；不可确认的记录明确返回 UNKNOWN。"""
    identifiers = sorted({str(item) for item in job_ids if re.fullmatch(r"[0-9]+", str(item))})
    result = {item: {"state": "UNKNOWN", "reason": "调度器暂不可确认"} for item in identifiers}
    if not identifiers:
        return result
    # squeue 反映当前排队/运行，sacct 补充离开队列后的终态；不保存周期队列副本。
    try:
        queued = _scheduler([*config.get("squeue_command", ["squeue"]), "--noheader", "--jobs",
                             ",".join(identifiers), "--format=%i|%T|%r"], config)
        if queued.returncode == 0:
            for line in queued.stdout.splitlines():
                pieces = line.strip().split("|", 2)
                if len(pieces) >= 2 and pieces[0] in result:
                    result[pieces[0]] = {"state": pieces[1].strip().upper(),
                                         "reason": short_reason(pieces[2] if len(pieces) > 2 else "")}
    except (OSError, subprocess.SubprocessError):
        pass
    missing = [item for item in identifiers if result[item]["state"] == "UNKNOWN"]
    if missing:
        try:
            accounting = _scheduler([*config.get("sacct_command", ["sacct"]), "--noheader", "--parsable2",
                                     "--jobs", ",".join(missing), "--format=JobIDRaw,State,Reason"], config)
            if accounting.returncode == 0:
                for line in accounting.stdout.splitlines():
                    pieces = line.strip().split("|")
                    # 不把 .batch/.extern/步骤的状态当成另外一个结构或整个作业终态。
                    if len(pieces) >= 2 and pieces[0] in missing:
                        state = pieces[1].split()[0].rstrip("+").upper()
                        result[pieces[0]] = {"state": state, "reason": short_reason(pieces[2] if len(pieces) > 2 else "")}
        except (OSError, subprocess.SubprocessError):
            pass
    return result


def submission_tag(plan: dict, batch: int) -> str:
    """产生可查询的唯一作业名称，提交回执丢失时用它核对是否已入队。"""
    return f"hpc-{plan['stage']}-{plan['run_id']}-{batch:04d}"


def reconcile_submission(plan: dict, batch: int, config: dict) -> str | None:
    """按唯一作业名查找已受理作业；未确认不等价于可以再次提交。"""
    tag = submission_tag(plan, batch)
    matches = set()
    # 两个来源都查，因为回执未知的作业可能已经快速结束并离开 squeue。
    commands = (
        [*config.get("squeue_command", ["squeue"]), "--noheader", "--name", tag, "--format=%i|%j"],
        [*config.get("sacct_command", ["sacct"]), "--noheader", "--parsable2", "--name", tag,
         "--starttime", plan["created_at"][:10], "--format=JobIDRaw,JobName%128"],
    )
    for command in commands:
        try:
            response = _scheduler(command, config)
        except (OSError, subprocess.SubprocessError):
            continue
        if response.returncode != 0:
            continue
        for line in response.stdout.splitlines():
            fields = line.strip().split("|")
            if len(fields) >= 2 and fields[1] == tag and re.fullmatch(r"[0-9]+", fields[0]):
                matches.add(fields[0])
    return next(iter(matches)) if len(matches) == 1 else None


def submit_batch(plan_path: str, plan: dict, batch: int, config: dict) -> dict:
    """提交一个串行批次；超时或格式不明的回执标 UNKNOWN，绝不盲目重试。"""
    directory = batch_directory(plan_path, batch)
    os.makedirs(directory, exist_ok=True)
    out_pattern = join_local(directory, "slurm-%j.out")
    err_pattern = join_local(directory, "slurm-%j.err")
    # Slurm 脚本只负责环境/资源并 exec 参数；任务命令完整 argv 直接传递，保留空格路径。
    command = [*config.get("sbatch_command", ["sbatch"]), "--parsable", "--job-name", submission_tag(plan, batch),
               "--output", out_pattern, "--error", err_pattern,
               "--chdir", os.path.dirname(plan_path), config["slurm_script"],
               *config.get("python_command", ["python3"]), join_local(project_root(), "cli.py"),
               "run", plan["stage"], "worker", "--plan", plan_path, "--batch", str(batch)]
    row = {"batch": str(batch), "job_id": "", "submit_status": "UNKNOWN", "submitted_at": utc_now(),
           "stdout": to_relative(out_pattern, os.path.dirname(plan_path)),
           "stderr": to_relative(err_pattern, os.path.dirname(plan_path)), "reason": ""}
    try:
        response = _scheduler(command, config)
    except FileNotFoundError as exc:
        # 根本没能启动 sbatch 才能明确确认未受理；通信/超时异常保留 UNKNOWN。
        row.update(submit_status="FAILED", reason=short_reason(exc))
        return row
    except (OSError, subprocess.SubprocessError) as exc:
        row["reason"] = short_reason(exc)
        return row
    text = response.stdout.strip()
    match = re.fullmatch(r"([0-9]+)(?:;[^\s;]+)?", text)
    if response.returncode == 0 and match:
        row.update(job_id=match.group(1), submit_status="SUBMITTED")
        row["stdout"] = row["stdout"].replace("%j", row["job_id"])
        row["stderr"] = row["stderr"].replace("%j", row["job_id"])
    elif response.returncode != 0:
        row.update(submit_status="FAILED", reason=short_reason(response.stderr or response.stdout or f"sbatch exit {response.returncode}"))
    else:
        row["reason"] = short_reason(f"sbatch 回执无法解析：{text}")
    return row
