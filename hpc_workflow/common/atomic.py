"""同目录临时文件原子替换与显式互斥锁；不提供内容摘要接口。"""

import csv
import io
import json
import os
import socket
import time
import uuid

from .errors import ConflictError
from .paths import ensure_host_path, path_dirname


def atomic_write_bytes(path: str, data: bytes) -> None:
    """将完整字节发布到 path；异常清理仅限本次创建的同目录临时文件。"""
    ensure_host_path(path)
    os.makedirs(path_dirname(path), exist_ok=True)
    temporary = f"{path}.{uuid.uuid4().hex}.tmp"
    try:
        # 独占建立临时文件，防止另一个进程复用同一文件；落盘后再发布。
        with open(temporary, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_text(path: str, text: str) -> None:
    """UTF-8 文本原子写出；正文中的换行由调用方控制。"""
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: str, payload) -> None:
    """拒绝 NaN/Infinity，按稳定 UTF-8 JSON 格式发布记录。"""
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    atomic_write_text(path, text + "\n")


def atomic_write_csv(path: str, fieldnames, rows) -> None:
    """先在内存完成 CSV 编码，再原子发布带 BOM 的表格。"""
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(fieldnames), extrasaction="raise")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_bytes(path, buffer.getvalue().encode("utf-8-sig"))


def read_json(path: str):
    """只读 UTF-8/BOM JSON；底层文件和格式异常原样向上传递。"""
    ensure_host_path(path)
    with open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


class FileLock:
    """以独占文件保护同一工作区；不根据年龄擅自删除其他进程的锁。"""

    def __init__(self, path: str):
        self.path = ensure_host_path(path)
        self.token = uuid.uuid4().hex
        self.owned = False

    def __enter__(self):
        # 锁文件记录所有者与随机令牌；使用独占创建，已有锁一律交由人工核实而不抢占。
        os.makedirs(path_dirname(self.path), exist_ok=True)
        payload = {"token": self.token, "pid": os.getpid(), "host": socket.gethostname(), "created": time.time()}
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ConflictError(f"工作区已锁定：{self.path}；核实所属进程后才能人工处理残留锁。") from exc
        # 只有完整写出所有者后才声明持有；写入失败仅清除本次刚独占创建的锁。
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            self.owned = True
        except BaseException:
            os.unlink(self.path)
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        # 只释放仍由本上下文持有的锁，不删除被人工替换或其他进程接管的锁。
        if self.owned:
            try:
                if read_json(self.path).get("token") == self.token:
                    os.unlink(self.path)
            except FileNotFoundError:
                pass
            finally:
                self.owned = False
        return False
