"""整批复用隔离 writer；直接写出三文件，最后发布声明版本的完成记录。"""

from contextlib import nullcontext
import multiprocessing
import os
import re
import uuid
from types import SimpleNamespace

from .. import __version__
from ..common.atomic import atomic_write_json, read_json
from ..common.errors import ConflictError, IntegrityError
from ..common.paths import ensure_host_path, from_relative, join_local
from ..records.manifest import SCHEMA_VERSION, is_revision_id, new_revision_id
from .planner import task_id_for


REQUIRED_PRODUCTS = ("STRU", "INPUT", "primitive.cif")


def inspect_existing(task_directory: str, expected_input_description: dict | None, task_id: str) -> dict | None:
    """读取当前版本完成记录，检查身份、必要文件及可选输入描述，不回读结构。"""
    if not os.path.lexists(task_directory):
        return None
    if os.path.islink(task_directory) or not os.path.isdir(task_directory):
        raise ConflictError(f"已有任务位置不是普通目录：{task_directory}")
    try:
        manifest = read_json(join_local(task_directory, "task_manifest.json"))
        if (not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION
                or manifest.get("workflow_version") != __version__ or manifest.get("status") != "success"):
            raise IntegrityError("existing_task_not_current_completed_record")
        identity = SimpleNamespace(input_source=manifest.get("input_source"), source_record_id=manifest.get("source_record_id"))
        if task_id_for(identity, manifest.get("dataset_namespace")) != task_id or manifest.get("task_id") != task_id:
            raise IntegrityError("existing_task_identity_inconsistent")
        if not is_revision_id(manifest.get("input_revision_id")):
            raise IntegrityError("existing_task_revision_invalid")
        if not isinstance(manifest.get("input_description"), dict):
            raise IntegrityError("existing_task_input_description_invalid")
        if expected_input_description is not None and manifest["input_description"] != expected_input_description:
            raise ConflictError("existing_task_input_or_configuration_changed; 请使用新的 work_root")
        if not isinstance(manifest.get("products"), list) or set(manifest["products"]) != set(REQUIRED_PRODUCTS):
            raise IntegrityError("existing_task_product_list_incomplete")
        # 只查询三个必要文件的类型和大小，不读取正文或任何排序/映射副产物。
        for relative in REQUIRED_PRODUCTS:
            path = from_relative(task_directory, relative)
            if os.path.islink(path) or not os.path.isfile(path) or os.path.getsize(path) == 0:
                raise IntegrityError("existing_task_product_missing_or_empty:" + relative)
        return manifest
    except ConflictError:
        raise
    except (OSError, ValueError, TypeError, IntegrityError) as exc:
        raise ConflictError(f"已有任务记录不能复用，保留原目录：{task_directory}；{exc}") from exc


def scan_staging(work_root: str) -> dict[str, list[str]]:
    """整轮只枚举一次工作根，返回 task_id 到残留 staging 绝对路径列表。"""
    result = {}
    for name in sorted(os.listdir(work_root)):
        match = re.fullmatch(r"(.+)\.staging-[0-9a-f]{32}", name)
        if match:
            result.setdefault(match[1], []).append(join_local(work_root, name))
    return result


def recover_staging(task_directory: str, expected_input_description: dict, task_id: str,
                    staging_index: dict | None = None) -> dict | None:
    """只从本轮索引恢复已有完成记录的 staging；残缺或旧协议现场保留。"""
    for staging in (staging_index or {}).get(task_id, []):
        try:
            manifest = inspect_existing(staging, expected_input_description, task_id)
        except ConflictError:
            continue
        if manifest is not None:
            if os.path.lexists(task_directory):
                raise ConflictError("恢复期间正式任务目录已出现")
            os.rename(staging, task_directory)
            return manifest
    return None


def _worker_loop(connection: object) -> None:
    """常驻进程入口；初始化 ASE 一次，串行处理内存请求并始终恢复 CWD。"""
    try:
        from ase import Atoms
        from ase.io import write
        connection.send(("ready", os.getpid()))
    except Exception as exc:
        connection.send(("fatal", f"{type(exc).__name__}:{exc}"))
        connection.close()
        return
    # 普通结构写出错误仅影响当前请求，不重新启动解释器或丢失后续请求。
    while True:
        try:
            request = connection.recv()
        except EOFError:
            break
        if request is None:
            break
        previous = os.getcwd()
        temporary = {key: os.environ.get(key) for key in ("TMP", "TEMP", "TMPDIR")}
        error = None
        try:
            os.chdir(request["staging"])
            for key in temporary:
                os.environ[key] = request["staging"]
            atoms = Atoms(numbers=request["numbers"], scaled_positions=request["scaled_positions"],
                          cell=request["cell"], pbc=True)
            with open("INPUT", "wb") as handle:
                handle.write(request["input_bytes"])
            write("primitive.cif", atoms, format="cif")
            write("STRU", atoms, format="abacus", pp=request["resources"]["pp"], basis=request["resources"]["basis"])
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
        finally:
            # 不读取、不校验 ASE 的排序副产物；只处理自己 staging 内的这个确切名字。
            try:
                sort_path = os.path.join(request["staging"], "ase_sort.dat")
                if os.path.lexists(sort_path):
                    os.unlink(sort_path)
            except OSError as exc:
                error = error or f"sort_temporary_removal_failed:{exc}"
            os.chdir(previous)
            for key, value in temporary.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        connection.send(("error", error) if error else ("ok", None))
    connection.close()


class WriterSession:
    """一轮前处理的常驻写出会话；无新任务时不启动科学解释器。"""

    def __init__(self, timeout: float = 120.0):
        self.timeout = timeout
        self.process = None
        self.connection = None
        self.starts = 0
        self.worker_pid = None

    def __enter__(self) -> "WriterSession":
        return self

    def _start(self) -> None:
        """首次写出时建立一次 spawn 进程及内存管道，超时释放资源。"""
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe()
        self.connection = parent
        self.process = context.Process(target=_worker_loop, args=(child,), daemon=True)
        try:
            self.process.start()
            child.close()
            self.starts += 1
            status, payload = self._receive()
            if status != "ready":
                raise IntegrityError("isolated_ase_writer_initialization_failed:" + str(payload))
            self.worker_pid = payload
        except BaseException:
            child.close()
            self.close()
            raise

    def _receive(self) -> tuple:
        """等待有限时间的 worker 响应；不创建文件形式的请求或结果。"""
        try:
            if not self.connection.poll(self.timeout):
                self.close()
                raise IntegrityError("isolated_ase_writer_timeout")
            return self.connection.recv()
        except (EOFError, OSError) as exc:
            self.close()
            raise IntegrityError("isolated_ase_writer_process_exited") from exc

    def write(self, request: dict) -> None:
        """发送一份内存结构；普通写出错误抛 IntegrityError，进程继续服务。"""
        if self.process is None:
            self._start()
        # worker 也可能在两个请求之间退出；释放失效会话，让下一任务重新建立进程。
        try:
            self.connection.send(request)
        except (EOFError, OSError) as exc:
            self.close()
            raise IntegrityError("isolated_ase_writer_process_exited") from exc
        status, payload = self._receive()
        if status != "ok":
            raise IntegrityError("isolated_ase_writer_failed:" + str(payload))

    def close(self) -> None:
        """关闭管道并回收进程；异常死亡后不会遗留占用的 Python worker。"""
        if self.process is not None:
            try:
                if self.process.is_alive() and self.connection is not None:
                    try:
                        self.connection.send(None)
                    except (BrokenPipeError, EOFError, OSError):
                        pass
                    self.process.join(timeout=1.0)
                if self.process.is_alive():
                    self.process.terminate()
                    self.process.join(timeout=5.0)
            finally:
                self.process.close()
                self.process = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False


def write_task(plan: dict, conversion: object, resource_mapping: dict, input_bytes: bytes,
               input_origin: str, input_description: dict, config: dict, software_versions: dict,
               writer: WriterSession | None = None) -> dict:
    """在独占 staging 直接写出，最后写完成记录再发布；失败 staging 留待诊断。"""
    task_directory = ensure_host_path(plan["task_directory"])
    if os.path.lexists(task_directory):
        raise ConflictError(f"正式任务目录已存在：{task_directory}")
    staging = join_local(config["work_root"], plan["task_id"] + ".staging-" + uuid.uuid4().hex)
    os.mkdir(staging)
    plan["_staging_directory"] = staging
    atoms = conversion.atoms
    request = {"staging": staging, "numbers": atoms.numbers.tolist(), "cell": atoms.cell.array.tolist(),
               "scaled_positions": atoms.get_scaled_positions(wrap=True).tolist(),
               "resources": resource_mapping, "input_bytes": input_bytes}
    # 批次传入同一个会话；直接调用此函数时仍有作用域明确的资源回收。
    with (nullcontext(writer) if writer is not None else WriterSession(config["writer_timeout"])) as session:
        session.write(request)
    record = plan["record"]
    manifest = {
        "schema_version": SCHEMA_VERSION, "workflow_version": __version__, "status": "success", "task_id": plan["task_id"],
        "input_revision_id": new_revision_id(), "input_description": input_description,
        "input_source": record.input_source, "dataset_namespace": config["dataset_namespace"],
        "source_record_id": record.source_record_id, "formula": record.formula, "elements": list(record.elements),
        # 本地输入只记录真实文件与结构信息，不为空的旧来源字段制造占位。
        "source_file": record.source_file, "source_file_base": "configured_structures_root",
        "input_origin": input_origin, "source_nsites": conversion.source_nsites, "primitive_nsites": len(atoms),
        "used_input_cell_fallback": conversion.used_input_cell_fallback,
        "software_versions": software_versions, "products": list(REQUIRED_PRODUCTS),
        "input_file": "INPUT", "structure_file": "STRU",
    }
    # 三个文件必须存在且非空，但不回读数据。完成记录是 staging 可恢复的发布边界。
    for name in REQUIRED_PRODUCTS:
        path = join_local(staging, name)
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise IntegrityError("writer_product_missing_or_empty:" + name)
    atomic_write_json(join_local(staging, "task_manifest.json"), manifest)
    if os.path.lexists(task_directory):
        raise ConflictError("正式目录在写出期间出现，拒绝覆盖")
    os.rename(staging, task_directory)
    return manifest
