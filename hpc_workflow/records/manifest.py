"""软件版本与声明输入版本；版本号不代表文件内容经过完整性验证。"""

import importlib.metadata
import uuid

from .. import __version__

SCHEMA_VERSION = 2


def new_revision_id() -> str:
    """返回随机 UUID4 字符串；仅在明确创建新输入时调用。"""
    # 提交轮次与输入世代分离，重排、续算不能重新分配输入版本。
    return str(uuid.uuid4())


def is_revision_id(value: object) -> bool:
    """判断 value 是否为规范 UUID4 字符串，不读取任何文件。"""
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
        return parsed.version == 4 and str(parsed) == value
    except (ValueError, AttributeError):
        return False


def software_versions() -> dict:
    """只查分发元数据，不导入计算库；缺少可选依赖时明确标记。"""
    versions = {"hpc_workflow": __version__}
    # 版本仅用于审计，不触发计算库导入；缺失分发记录为明确状态而非隐含默认版本。
    for name in ("ase", "spglib", "numpy", "pymatgen", "mp-api", "pandas"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not_installed"
    return versions
