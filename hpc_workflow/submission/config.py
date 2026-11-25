"""计算阶段独立配置入口；不改变 screening/pre 的配置白名单。"""

import copy
import importlib
import math
import os
import shlex
import string

from ..common.config_loader import _read_custom
from ..common.errors import ConfigError
from ..common.paths import (ensure_host_path, path_dirname, project_root,
                            resolve_local)


def command_argv(value: str | list | tuple, name: str) -> list[str]:
    """把可信命令配置转为 argv；拒绝空命令和换行，实际执行不使用 shell。"""
    # 字符串只做 POSIX 命令分词；推荐 argv 列表，带空格的可执行文件路径无需再猜测。
    try:
        result = shlex.split(value, posix=True) if isinstance(value, str) else list(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} 必须是命令字符串或 argv 列表。") from exc
    if not result or any(not isinstance(v, str) or not v or any(c in v for c in "\x00\r\n") for v in result):
        raise ConfigError(f"{name} 包含空参数、NUL 或换行。")
    return result


def _positive(value: object, name: str) -> None:
    """校验有限正数；bool 不能作为数值配置悄悄通过。"""
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{name} 必须是有限正数。")


def validate_config(config: dict, stage: str) -> None:
    """只读校验阶段参数与模板；不创建 work，也不检查赝势/轨道。"""
    if stage not in {"relax", "zstar"}:
        raise ConfigError(f"未知计算阶段：{stage}")
    if type(config["parallel_jobs"]) is not int or config["parallel_jobs"] < 0:
        raise ConfigError("parallel_jobs 必须为非负整数。")
    # 两个阶段目录必须是不同的单层名称，禁止覆盖 pre 根或逃逸到其他任务。
    for key in ("relax_subdir", "zstar_subdir"):
        value = config[key]
        if not isinstance(value, str) or value in ("", ".", "..") or any(c in value for c in "/\\:\x00\r\n") or value.startswith("."):
            raise ConfigError(f"{key} 必须是不以点开头的单层目录名。")
    if config["relax_subdir"].casefold() == config["zstar_subdir"].casefold():
        raise ConfigError("relax_subdir 与 zstar_subdir 不能相同。")
    for key in ("command_timeout", "minimum_distance"):
        _positive(config[key], key)
    maximum = config.get("relax_max_steps")
    if maximum is not None and (type(maximum) is not int or maximum <= 0):
        raise ConfigError("relax_max_steps 必须为空或正整数。")
    # 队列命令不展开占位；launcher 只允许受控资源参数，避免拼写错误到集群才暴露。
    formatter = string.Formatter()
    allowed = {"symprec", "ntasks", "omp_threads", "abacus_command", "pyatb_command", "xc", "kspacing"}
    for key in ("python_command", "sbatch_command", "squeue_command", "sacct_command", "abacus_command", "pyatb_command", "zstar_command"):
        if key not in config:
            continue
        argv = command_argv(config[key], key)
        for part in argv:
            try:
                fields = [field for _, field, _, _ in formatter.parse(part) if field is not None]
            except ValueError as exc:
                raise ConfigError(f"{key} 中的占位符格式错误。") from exc
            if set(fields) - allowed or (fields and key in {"python_command", "sbatch_command", "squeue_command", "sacct_command"}):
                raise ConfigError(f"{key} 中有不支持的占位符。")
        config[key] = argv
    if stage == "zstar":
        # 正式结构编辑、zstar pre/post 共享唯一容差；扫描值不能改变这一决定。
        if type(config["symprec"]) not in (int, float) or config["symprec"] != 1e-3:
            raise ConfigError("正式 symprec 固定为 1e-3 Å；其他容差只放在扫描列表中。")
        for key in ("max_symmetry_strain", "kspacing"):
            _positive(config[key], key)
        angle = config["angle_tolerance"]
        if type(angle) not in (int, float) or angle != -1:
            raise ConfigError("计算阶段 angle_tolerance 固定为 -1，保持空间群识别与编辑一致。")
        scan = config["symmetry_scan_tolerances"]
        if not isinstance(scan, (list, tuple)) or not scan:
            raise ConfigError("symmetry_scan_tolerances 必须是非空容差列表。")
        for value in scan:
            _positive(value, "symmetry_scan_tolerances")
        if not isinstance(config["xc"], str) or not config["xc"].strip():
            raise ConfigError("xc 必须是非空字符串。")
        commands = config.get("zstar_commands")
        if commands is not None:
            if not isinstance(commands, dict) or set(commands) != {"pre", "run", "post", "static"}:
                raise ConfigError("zstar_commands 必须为空或包含 pre/run/post/static 四个键。")
            # 配置可替换整条命令；仍按 argv 执行，不将用户命令送入隐式 shell。
            for label, value in commands.items():
                argv = command_argv(value, "zstar_commands." + label)
                for part in argv:
                    try:
                        fields = [field for _, field, _, _ in formatter.parse(part) if field is not None]
                    except ValueError as exc:
                        raise ConfigError(f"zstar_commands.{label} 占位符格式错误。") from exc
                    if set(fields) - allowed:
                        raise ConfigError(f"zstar_commands.{label} 中有不支持的占位符。")
                commands[label] = argv


def load_config(stage: str, custom_path: str | None = None) -> dict:
    """加载固定 relax/zstar 模板并叠加 CONFIG；相对路径按声明该值的文件解释。"""
    if stage not in {"relax", "zstar"}:
        raise ConfigError(f"未知计算阶段：{stage}")
    module = importlib.import_module(stage + "_config")
    defaults = copy.deepcopy(module.CONFIG)
    result = copy.deepcopy(defaults)
    default_dir = path_dirname(resolve_local(module.__file__, project_root()))
    overrides = {}
    source = resolve_local(module.__file__, project_root())
    if custom_path is not None:
        source = resolve_local(custom_path, os.getcwd())
        overrides = _read_custom(source)
        unknown = set(overrides) - set(defaults)
        if unknown:
            raise ConfigError("未知计算配置项：" + ", ".join(sorted(map(str, unknown))))
        result.update(copy.deepcopy(overrides))
    # 默认脚本的位置始终相对默认模板，覆盖文件放在其他目录时不会误定位默认 slurm。
    for key in ("work_root", "slurm_script", "relax_input_file"):
        if result.get(key) not in (None, ""):
            anchor = path_dirname(source) if key in overrides else default_dir
            result[key] = ensure_host_path(resolve_local(result[key], anchor))
    result["stage"] = stage
    result["_config_path"] = source
    validate_config(result, stage)
    return result
