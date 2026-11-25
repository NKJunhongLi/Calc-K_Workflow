"""唯一命令入口：位置子命令选择工作流，带值选项指定配置。"""

import argparse
import json
import sys


def build_parser() -> argparse.ArgumentParser:
    """创建不执行业务的解析器；帮助也采用位置子命令。"""
    # 入口只构造命令契约；禁用无值帮助开关，使功能与帮助统一采用位置子命令。
    parser = argparse.ArgumentParser(
        prog="python3 cli.py", add_help=False,
        description="本地结构前处理、Slurm 计算与批次CSV汇总；使用 help 查看命令。",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    # 输入仅来自配置指定的本地目录；不再公开筛选、CSV或交接文件入口。
    pre = commands.add_parser("pre", help="准备本地结构计算任务", add_help=False)
    pre.add_argument("--config", metavar="FILE", help="覆盖默认 CONFIG 的 Python 文件")
    # validate 只选择被检查的阶段，不创建业务运行；help 可按主题单独解释选项。
    validate = commands.add_parser("validate", help="只读校验配置和输入位置", add_help=False)
    validate.add_argument("workflow", choices=("pre",))
    validate.add_argument("--config", metavar="FILE")
    # 计算阶段沿用原子命令树与参数，前处理改造不改变提交和续算契约。
    run = commands.add_parser("run", help="计算提交、续算与状态查询", add_help=False)
    stages = run.add_subparsers(dest="stage", required=True)
    for stage in ("relax", "zstar"):
        child = stages.add_parser(stage, add_help=False)
        child.add_argument("action", nargs="?", default="run",
                           choices=("run", "scan", "plan", "submit", "resume", "status", "worker"))
        child.add_argument("--config", metavar="FILE", help="覆盖阶段固定配置模板")
        child.add_argument("--plan", metavar="FILE", help="指定本轮 plan.json；worker/submit 必须提供")
        child.add_argument("--batch", type=int, metavar="N", help="仅 worker 使用的批次编号")
    # 后处理是独立流程，默认收集；只读验证使用位置动作，不增加无值功能开关。
    post = commands.add_parser("post", help="整批终态后提取46列CSV", add_help=False)
    post.add_argument("action", nargs="?", default="collect", choices=("collect", "validate", "clean"))
    post.add_argument("--config", metavar="FILE", help="覆盖固定 post_config.py 的 CONFIG")
    help_command = commands.add_parser("help", help="显示命令帮助", add_help=False)
    help_command.add_argument("topic", nargs="?", choices=("pre", "validate", "run", "post"))
    help_command.add_argument("stage", nargs="?", choices=("relax", "zstar"))
    return parser


def main(argv=None) -> int:
    """解析并执行一个命令，返回 0 成功、1 运行失败、2 配置错误、130 中断。"""
    # 普通 python cli.py validate 也保持只读；不靠用户记住额外的 -B 参数。
    sys.dont_write_bytecode = True
    parser = build_parser()
    args = parser.parse_args(argv)
    # 帮助提前返回，配置缺失或科学库未安装也不妨碍查看使用说明。
    if args.command == "help":
        if args.stage and args.topic != "run":
            parser.error("帮助的阶段参数仅用于 help run relax 或 help run zstar。")
        if args.topic:
            subparsers = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
            selected = subparsers.choices[args.topic]
            if args.stage:
                stages = next(action for action in selected._actions if isinstance(action, argparse._SubParsersAction))
                selected = stages.choices[args.stage]
            selected.print_help()
        else:
            parser.print_help()
        return 0
    # 计算库仅在真正执行/校验相应工作流时导入；帮助不依赖 ASE 等科学库。
    import main as application
    from hpc_workflow.common.errors import ConfigError, WorkflowError
    try:
        # 解析后的命令只派发一次；非法阶段组合在应用调用前转成配置错误。
        if args.command == "pre":
            result = application.run_pre(args.config)
        elif args.command == "post":
            result = application.run_post(args.config, validate_only=args.action == "validate", action=args.action)
            from hpc_workflow.postprocess.pipeline import format_summary
            print(format_summary(result))
            return 0 if result["status"] in {"success", "valid", "no_tasks"} else 1
        elif args.command == "run":
            # 状态查询输出完整中文排版；worker 的子进程输出直接进入 Slurm 日志。
            result = application.run_calculation(args.stage, args.action, args.config, args.plan, args.batch)
            if args.action == "status":
                from hpc_workflow.submission.status import format_status
                print(format_status(result))
                return 0
        else:
            # argparse只允许validate pre，因此该分支不再解释任何外部交接状态。
            result = application.validate_pre(args.config)
        # 标准输出为可追踪的中文 JSON；部分失败保持非零返回，便于 shell 串联判断。
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0 if result.get("status") in {"success", "valid", "no_tasks", "submitted", "planned"} else 1
    except ConfigError as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2
    except (WorkflowError, OSError, ValueError, ImportError) as exc:
        # 可预期输入与依赖错误给出简洁诊断；未预期程序错误保留 traceback。
        print(f"[运行失败] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("[运行中断] 已发布任务保留，下一次运行将验证后复用。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
