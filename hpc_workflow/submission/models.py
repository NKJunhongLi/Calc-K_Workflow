"""计算层轻量契约；状态只描述本层负责的边界。"""

from datetime import datetime, timezone


TERMINAL_SUCCESS = "success"
PHYSICAL_REJECTION = "physical_rejected"
FAILURE_STATES = frozenset({"technical_failed", "input_failed"})
ACTIVE_SLURM_STATES = frozenset({
    "PENDING", "RUNNING", "CONFIGURING", "COMPLETING", "SUSPENDED",
    "REQUEUED", "REQUEUE_FED", "REQUEUE_HOLD", "RESIZING", "SIGNALING", "STAGE_OUT",
})
JOB_FIELDS = ("batch", "job_id", "submit_status", "submitted_at", "stdout", "stderr", "reason")


def utc_now() -> str:
    """返回带时区的 UTC ISO 时间字符串。"""
    # 全部调度记录统一时区，跨本地机器和集群查看时不会混淆提交先后。
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def short_reason(value, limit: int = 240) -> str:
    """将任意错误压成可读短句，完整输出仍留在原始日志。"""
    # CSV/JSON 只留一行摘要；换行不应破坏表格，也不复制子进程大段输出。
    return " ".join(str(value or "").split())[:limit]
