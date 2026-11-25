"""纯字符串区分 Windows/POSIX 语法；资源路径绝不按主机系统转换。"""

import ntpath
import os
import posixpath

from .errors import ConfigError


def path_style(path: str) -> str:
    """从明确的绝对路径语法返回 windows/posix，拒绝盘符相对路径。"""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ConfigError("路径必须是非空字符串，且不能包含 NUL。")
    if path.startswith("//"):
        raise ConfigError("双正斜杠路径语义不明确；POSIX 请用单 /，UNC 请用双反斜杠。")
    # 带盘符、UNC 的路径由 ntpath 解释，不依赖正在运行的操作系统。
    drive, tail = ntpath.splitdrive(path)
    if drive:
        if drive.startswith("\\\\") and len([p for p in drive[2:].split("\\") if p]) != 2:
            raise ConfigError(f"UNC 路径必须同时包含 server 和 share：{path!r}")
        if not ntpath.isabs(path):
            raise ConfigError(f"拒绝盘符相对路径：{path!r}")
        return "windows"
    # 单反斜杠缺少 Windows 盘符，不能猜测用户想使用哪个磁盘。
    if path.startswith("\\"):
        raise ConfigError(f"Windows 路径必须包含盘符或完整 UNC：{path!r}")
    if path.startswith("/"):
        if "\\" in path:
            raise ConfigError(f"POSIX 根路径不能混入反斜杠：{path!r}")
        return "posix"
    raise ConfigError(f"需要明确的绝对根路径：{path!r}")


def _module(path: str):
    """选择字符串处理模块；此函数不访问文件系统。"""
    return ntpath if path_style(path) == "windows" else posixpath


def join_local(base: str, *parts: str) -> str:
    """按 base 的语法拼接相对部分，不允许后续绝对路径替换根目录。"""
    module = _module(base)
    for part in parts:
        if not isinstance(part, str) or "\x00" in part:
            raise ConfigError("路径组成部分必须为无 NUL 的字符串。")
        # 两种绝对语法都检查，防止把异平台绝对路径误当文件名。
        if ntpath.splitdrive(part)[0] or part.startswith(("/", "\\")):
            raise ConfigError(f"拼接部分必须为相对路径：{part!r}")
        if module is posixpath and "\\" in part:
            raise ConfigError(f"POSIX 路径组成部分不能含反斜杠：{part!r}")
    return module.normpath(module.join(base, *parts))


def resolve_local(path: str, base: str) -> str:
    """绝对路径保持其语法；相对路径以配置目录为基准进行纯字符串拼接。"""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise ConfigError("本机路径必须为非空字符串。")
    if ntpath.splitdrive(path)[0] or path.startswith(("/", "\\")):
        return _module(path).normpath(path)
    return join_local(base, path)


def path_dirname(path: str) -> str:
    """按路径本身的语法取父目录。"""
    return _module(path).dirname(path)


def path_basename(path: str) -> str:
    """按路径本身的语法取最后一个名称。"""
    return _module(path).basename(path)


def to_relative(path: str, root: str) -> str:
    """返回 root 内的 POSIX 相对引用；拒绝越界或不同路径语法。"""
    if path_style(path) != path_style(root):
        raise ConfigError("相对引用的路径与根目录必须使用相同语法。")
    module = _module(root)
    try:
        relative = module.relpath(path, root)
    except ValueError as exc:
        raise ConfigError("不能在不同磁盘之间建立任务相对引用。") from exc
    # 任务内部引用必须处于根目录内；禁止 ../ 从记录中逃逸。
    portable = relative.replace("\\", "/")
    if portable == ".." or portable.startswith("../"):
        raise ConfigError(f"路径不在记录根目录内：{path!r}")
    return portable


def from_relative(root: str, relative: str) -> str:
    """将受限 POSIX 相对引用附着到当前根目录，支持整目录迁移。"""
    # 记录中的空值或 NUL 无法明确定位；根自身仅允许用单独的点号表示。
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise ConfigError("相对引用必须是非空字符串。")
    if relative == ".":
        return join_local(root)
    # 逐段拒绝空段、点段、向上路径与反斜杠，防止搬迁时跨平台解释出不同位置。
    pieces = relative.split("/")
    if "\\" in relative or any(p in ("", ".", "..") for p in pieces):
        raise ConfigError(f"不安全的任务相对引用：{relative!r}")
    # 盘符与冒号不能藏在相对段中；通过检查后才按当前根语法重新附着。
    if any(ntpath.splitdrive(p)[0] or ":" in p for p in pieces):
        raise ConfigError(f"相对引用中不能包含盘符：{relative!r}")
    return join_local(root, *pieces)


def ensure_host_path(path: str) -> str:
    """实际 I/O 前检查语法与主机匹配；不转换外来路径或探测远端文件。"""
    expected = "windows" if os.name == "nt" else "posix"
    if path_style(path) != expected:
        raise ConfigError(f"当前主机需要 {expected} 本机路径：{path!r}；资源路径应独立配置。")
    return path


def project_root() -> str:
    """从模块位置取得项目根目录，不依赖启动命令的工作目录。"""
    root = os.path.dirname(__file__)
    # __file__ 只用来定位本机程序；不参与改写用户填写的资源字符串。
    if not os.path.isabs(root):
        root = os.path.abspath(root)
    return join_local(root, "..", "..")
