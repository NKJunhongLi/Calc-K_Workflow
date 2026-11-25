"""固定默认配置加可选 Python 覆盖；加载配置不会创建输出目录。"""

import copy
import importlib
import json
import math
import os
import types

from .errors import ConfigError
from .paths import ensure_host_path, path_dirname, path_style, project_root, resolve_local


def _read_custom(path: str) -> dict:
    """执行用户明确指定的配置模块，使用源码编译避免生成配置旁的 pycache。"""
    ensure_host_path(path)
    # Python 配置允许正常表达式与模块导入；配置文件属于用户可信源码。
    module = types.ModuleType("_hpc_custom_config")
    module.__file__ = path
    try:
        with open(path, "rb") as handle:
            source = handle.read()
        exec(compile(source, path, "exec"), module.__dict__)
    except SyntaxError as exc:
        raise ConfigError(f"配置语法错误：{path} 第 {exc.lineno} 行：{exc.msg}") from exc
    except (Exception, SystemExit) as exc:
        raise ConfigError(f"配置加载失败：{path}：{type(exc).__name__}: {exc}") from exc
    value = getattr(module, "CONFIG", None)
    if not isinstance(value, dict):
        raise ConfigError(f"配置文件必须声明 CONFIG 字典：{path}")
    return value


def load_config(kind: str, custom_path: str | None = None) -> dict:
    """加载pre默认与覆盖配置；源结构根和用户INPUT必须原本就是绝对路径。"""
    if kind != "pre":
        raise ConfigError(f"不支持的配置类型：{kind}")
    # 计算与post各自保留独立loader；这里只负责已经拆分出的pre配置。
    default_module = importlib.import_module("pre_config")
    defaults = getattr(default_module, "CONFIG", None)
    if not isinstance(defaults, dict):
        raise ConfigError("默认配置没有提供 CONFIG 字典。")
    result = copy.deepcopy(defaults)
    default_source = resolve_local(default_module.__file__, project_root())
    source_path = default_source
    overrides = {}
    # 命令行指定配置的位置按启动目录解释；配置内部相对路径按该文件目录解释。
    if custom_path is not None:
        source_path = resolve_local(custom_path, os.getcwd())
        overrides = _read_custom(source_path)
        unknown = set(overrides).difference(defaults)
        if unknown:
            raise ConfigError(f"未知配置项：{', '.join(sorted(map(str, unknown)))}")
        result.update(copy.deepcopy(overrides))
    config_dir = path_dirname(source_path)
    # 工作根允许相对配置目录；默认值仍附着默认模板，不被覆盖文件的位置改变。
    work_root = result.get("work_root")
    if work_root is not None and work_root != "":
        anchor = config_dir if "work_root" in overrides else path_dirname(default_source)
        result["work_root"] = ensure_host_path(resolve_local(work_root, anchor))
    # 结构父目录是用户唯一必填的来源位置，不能把相对文本悄悄转换为合法绝对路径。
    structures_root = result.get("structures_root")
    if structures_root is not None and structures_root != "":
        path_style(structures_root)
        result["structures_root"] = ensure_host_path(structures_root)
    # 用户 INPUT 是明确的文件约定；相对路径错误不能被悄悄换成内置模板。
    input_path = result.get("input_file")
    if input_path not in (None, ""):
        path_style(input_path)
        result["input_file"] = ensure_host_path(input_path)
    result["_config_dir"] = config_dir
    result["_config_path"] = source_path
    return result


def public_config(config: dict) -> dict:
    """生成可持久化配置快照；去掉内部加载字段并遮蔽 API 凭证。"""
    result = {k: copy.deepcopy(v) for k, v in config.items() if not k.startswith("_")}
    if "api_key" in result:
        result["api_key"] = "<已隐藏>" if result["api_key"] else ""
    return _json_value(result)


def _json_value(value):
    """把合法配置容器转成稳定 JSON 值；集合排序，未知对象和非有限数明确拒绝。"""
    # 标量保持准确类型；非有限浮点值不能进入标准 JSON 或持久化配置快照。
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ConfigError("配置快照不能包含 NaN 或 Infinity。")
        return value
    # 字典逐层验证字符串键；递归转换只生成新容器，不修改业务配置对象。
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise ConfigError("配置字典键必须是字符串。")
        return {key: _json_value(item) for key, item in value.items()}
    # 有序序列保持原顺序，统一转为 JSON 数组，保证 tuple 配置可以正常快照。
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    # 集合本身无序，按规范 JSON 排序后才入快照，避免相同配置得到不稳定快照。
    if isinstance(value, (set, frozenset)):
        items = [_json_value(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False))
    # 对自定义对象不猜测序列化方式；在任何运行目录创建前明确报配置错误。
    raise ConfigError(f"配置快照不支持对象类型：{type(value).__name__}")


def business_config(config: dict) -> dict:
    """为业务模块提供独立字典，不泄漏加载器的内部字段。"""
    return {k: copy.deepcopy(v) for k, v in config.items() if not k.startswith("_")}
