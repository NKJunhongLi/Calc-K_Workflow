"""每次执行独立快照；根目录改变不影响快照内部引用。"""

from datetime import datetime, timezone
import os
import uuid

from ..common.atomic import atomic_write_json
from ..common.config_loader import public_config
from ..common.paths import join_local
from .manifest import SCHEMA_VERSION, software_versions
from .. import __version__


def utc_now() -> str:
    """返回带时区的 ISO UTC 时间，用于跨集群排序与审计。"""
    return datetime.now(timezone.utc).isoformat()


def create_run(output_root: str, workflow: str, config: dict) -> dict:
    """独占建立 runs/<运行ID>，立即持久化 running 记录。"""
    # 时间便于人工定位，随机后缀避免同秒执行相互覆盖；配置快照先脱敏并规范序列化。
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
    directory = join_local(output_root, "runs", run_id)
    snapshot = public_config(config)
    record = {
        "schema_version": SCHEMA_VERSION, "workflow_version": __version__, "run_id": run_id, "workflow": workflow,
        "status": "running", "started_at": utc_now(), "finished_at": None,
        "config": snapshot,
        "software_versions": software_versions(),
    }
    # 配置必须先能形成完整快照；不为坏配置留下空运行目录。
    os.makedirs(directory, exist_ok=False)
    atomic_write_json(join_local(directory, "run_manifest.json"), record)
    return {"run_id": run_id, "directory": directory, "record": record}


def finish_run(context: dict, status: str, summary: dict | None = None, error: str | None = None) -> None:
    """原子更新本轮快照；失败记录保留原配置和已经发布的阶段产物。"""
    # 保留原始配置与开始时间，仅形成新的终态副本；异常文本不会替换已有来源关联。
    record = dict(context["record"])
    record.update(status=status, finished_at=utc_now(), summary=summary or {})
    if error is not None:
        record["error"] = error
    # 先发布到磁盘再更新内存，避免写盘失败时调用者误认终态已经可靠提交。
    atomic_write_json(join_local(context["directory"], "run_manifest.json"), record)
    context["record"] = record
