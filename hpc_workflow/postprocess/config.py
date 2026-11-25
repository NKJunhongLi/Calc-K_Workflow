"""独立后处理配置入口；不扩展或修改旧阶段配置白名单。"""

import copy
import importlib
import os

from ..common.config_loader import _read_custom
from ..common.errors import ConfigError
from ..common.paths import (ensure_host_path, from_relative, join_local,
                            path_dirname, project_root, resolve_local)


def output_path(config: dict) -> str:
    """返回明确配置的输出，或当前批次默认的 post/zstar_results.csv，不创建目录。"""
    return config.get("output_csv") or join_local(config["work_root"], "post", "zstar_results.csv")


def validate_config(config: dict, check_inputs: bool = True) -> None:
    """校验批次路径与自动清理开关；不写目录、不执行计算或查询队列。"""
    required = {"work_root", "output_csv", "zstar_subdir", "band_relative_root"}
    allowed = required | {"cleanup_completed"}
    unknown = {key for key in config if not key.startswith("_")} - allowed
    if unknown or required - set(config):
        raise ConfigError(f"后处理配置键错误：未知={sorted(unknown)}，缺失={sorted(required - set(config))}")
    # 旧的完整四键配置仍兼容默认开启；拒绝1/0和字符串，避免误把拼写当成删除授权开关。
    if type(config.get("cleanup_completed", True)) is not bool:
        raise ConfigError("cleanup_completed 必须为布尔值 True 或 False。")
    root = ensure_host_path(config["work_root"])
    name = config["zstar_subdir"]
    # 不允许阶段目录覆盖任务根，也不能用路径分隔符逃出一个任务目录。
    if (not isinstance(name, str) or not name or name.startswith(".")
            or any(char in name for char in "/\\:") or any(ord(char) < 32 for char in name)):
        raise ConfigError("zstar_subdir 必须为不以点开头的单层目录名称。")
    from_relative(root, config["band_relative_root"])
    destination = ensure_host_path(output_path(config))
    if not destination.lower().endswith(".csv"):
        raise ConfigError("output_csv 必须使用 .csv 扩展名。")
    # 校验只查看现有根目录；输出父目录可在真正发布整批结果时建立。
    if check_inputs and (not os.path.isdir(root) or os.path.islink(root)):
        raise ConfigError(f"work_root 不存在、不是目录或为符号链接：{root}")


def load_config(custom_path: str | None = None) -> dict:
    """加载 post_config.CONFIG 并叠加可信 Python 配置，保留各值声明位置的路径语义。"""
    module = importlib.import_module("post_config")
    defaults = copy.deepcopy(module.CONFIG)
    result = copy.deepcopy(defaults)
    default_source = resolve_local(module.__file__, project_root())
    source = resolve_local(custom_path, os.getcwd()) if custom_path is not None else default_source
    overrides = _read_custom(source) if custom_path is not None else {}
    if set(overrides) - set(defaults):
        raise ConfigError("未知后处理配置项：" + ", ".join(sorted(map(str, set(overrides) - set(defaults)))))
    result.update(copy.deepcopy(overrides))
    # 默认 work 根始终相对默认模板，自定义相对路径则相对自定义文件；不受启动 CWD 污染。
    for key in ("work_root", "output_csv"):
        value = result.get(key)
        if value not in (None, ""):
            anchor = path_dirname(source if key in overrides else default_source)
            result[key] = ensure_host_path(resolve_local(value, anchor))
    result["_config_path"] = source
    validate_config(result)
    return result
