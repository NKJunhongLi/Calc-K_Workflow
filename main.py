"""本地结构计算应用编排；导入不创建目录或启动科学计算。"""

import os

from hpc_workflow.common.config_loader import business_config, load_config
from hpc_workflow.common.errors import ConfigError
from hpc_workflow.records.runs import create_run, finish_run


def validate_pre(config_path: str | None = None) -> dict:
    """只读检查本地结构目录及前处理配置，不转换结构或写出任务。"""
    from hpc_workflow.preprocess.validation import validate_config

    # 配置只加载一次；输入目录已经是用户明确指定的绝对地址，无需交接解析。
    config = load_config("pre", config_path)
    validate_config(business_config(config), check_inputs=True)
    return {"workflow": "pre", "status": "valid", "config": config["_config_path"],
            "input_source": "LOCAL"}


def run_pre(config_path: str | None = None) -> dict:
    """从本地结构目录生成任务，保留独立运行快照和已有成功任务。"""
    from hpc_workflow.preprocess.pipeline import run_pre as execute
    from hpc_workflow.preprocess.validation import validate_config

    # 在创建运行快照前完成全局校验；配置错误不能留下看似启动成功的业务现场。
    config = load_config("pre", config_path)
    resolved = business_config(config)
    validate_config(resolved, check_inputs=True)
    context = create_run(resolved["work_root"], "pre", config)
    try:
        # 引擎隔离单文件失败；应用层只登记本轮最终状态，不准备或提交DFT计算。
        result = execute(resolved, context["directory"])
        finish_run(context, result["status"], result)
        return result
    except BaseException as exc:
        # 中断和全局错误都保留已发布任务，下一轮仍可复用或恢复完整staging。
        status = "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed"
        finish_run(context, status, error=f"{type(exc).__name__}: {exc}")
        raise


def run_calculation(stage: str, action: str = "run", config_path: str | None = None,
                    plan_path: str | None = None, batch: int | None = None) -> dict:
    """派发独立计算模块；原筛选/前处理函数和配置加载行为保持原样。"""
    from hpc_workflow.common.paths import resolve_local
    # worker 只消费本轮冻结的计划，不重新执行用户配置，避免排队期间配置变化。
    if action == "worker":
        if not plan_path or batch is None or batch <= 0 or config_path is not None:
            raise ConfigError("worker 需要 --plan FILE --batch 正整数，不接受 --config。")
        from hpc_workflow.submission.worker import run_worker
        return run_worker(stage, resolve_local(plan_path, os.getcwd()), batch)
    if batch is not None:
        raise ConfigError("--batch 仅用于 worker。")
    from hpc_workflow.submission.config import load_config as load_calculation_config
    config = load_calculation_config(stage, config_path)
    if action == "status":
        from hpc_workflow.submission.status import collect_status
        return collect_status(stage, config, plan_path)
    # 提交旧计划必须显式选定该计划；scan/plan/resume 不接受容易造成歧义的旧计划。
    if plan_path is not None and action != "submit":
        raise ConfigError("--plan 仅用于 submit/status/worker。")
    if action == "submit" and not plan_path:
        raise ConfigError("submit 需要 --plan FILE。")
    from hpc_workflow.submission.pipeline import run
    return run(stage, action, config, resolve_local(plan_path, os.getcwd()) if plan_path else None)


def run_post(config_path: str | None = None, validate_only: bool = False, action: str = "collect") -> dict:
    """加载独立后处理配置，派发收集/只读预览/终态中间文件清理。"""
    from hpc_workflow.postprocess.config import load_config as load_post_config
    from hpc_workflow.postprocess.pipeline import run_clean, run_post as execute
    # 不加载当前zstar配置，更不调用提交、续算或原生状态写出器；历史执行参数来自计划。
    if action not in {"collect", "validate", "clean"} or (action == "clean" and validate_only):
        raise ConfigError("后处理动作必须为 collect/validate/clean；clean 不接受 validate_only。")
    config = load_post_config(config_path)
    return run_clean(config) if action == "clean" else execute(config, validate_only=validate_only or action == "validate")
