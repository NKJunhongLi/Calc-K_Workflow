"""cell-relax 输入准备、只读完成检查及单结构执行。"""

import glob
import json
import os
import re
import shlex
import stat

from ..common.atomic import atomic_write_bytes, read_json
from ..common.errors import IntegrityError
from ..records.manifest import new_revision_id, is_revision_id
from ..common.paths import join_local, to_relative
from .symmetry import read_structure, validate_geometry


def calculation_directory(task_dir: str, config: dict, stage: str) -> str:
    """返回 task 下受控的计算目录，不创建目录；拒绝绝对路径或穿越路径。"""
    name = config.get(stage + "_subdir", "cell-relax" if stage == "relax" else "zstar")
    if not isinstance(name, str) or name in ("", ".", "..") or "/" in name or "\\" in name or ":" in name:
        raise ValueError("calculation_subdir_must_be_single_name")
    path = join_local(task_dir, name)
    # 不通过符号链接进入别处；计算适配层永远只写自己的任务子目录。
    if os.path.islink(path):
        raise ValueError("calculation_directory_is_symlink")
    return path


REVISION_FIELDS = ("input_revision_id", "source_input_revision_id", "calculation_subdir",
                   "scientific_input", "source_relax_revision_id")


def read_task_revision_id(task_dir: str) -> str:
    """读取当前协议 pre 输入声明；UUID 不证明文件未被人工改写。"""
    path = join_local(task_dir, "task_manifest.json")
    if os.path.islink(path):
        raise IntegrityError("task_manifest_is_symlink")
    manifest = read_json(path)
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 2
            or manifest.get("workflow_version") != "0.0.2"
            or manifest.get("status") != "success"
            or not is_revision_id(manifest.get("input_revision_id"))):
        raise IntegrityError("task_revision_requires_current_workflow_record")
    return manifest["input_revision_id"]


def scientific_input(config: dict, stage: str) -> dict:
    """冻结命令模板及小型科学/QC 设置原值；不猜测自定义 argv 的参数语义。"""
    common = {"abacus_command": config.get("abacus_command", ["mpirun", "-np", "{ntasks}", "abacus"]),
              "minimum_distance": config.get("minimum_distance", 0.1)}
    if stage == "relax":
        return {**common, "relax_input_file": config.get("relax_input_file"),
                "relax_max_steps": config.get("relax_max_steps")}
    return {**common, "xc": config.get("xc", "pbesol"), "kspacing": config.get("kspacing", 0.1),
            "symprec": 0.001, "commands": config.get("zstar_commands"),
            "zstar_command": config.get("zstar_command", ["zstar"]),
            "pyatb_command": config.get("pyatb_command", ["mpirun", "-np", "{ntasks}", "pyatb"]),
            "relax_subdir": config.get("relax_subdir", "cell-relax"),
            "angle_tolerance": config.get("angle_tolerance", -1),
            "max_symmetry_strain": config.get("max_symmetry_strain", 0.01),
            "symmetry_scan_tolerances": list(config.get("symmetry_scan_tolerances", [1e-5, 1e-4, 0.001, 0.01, 0.1]))}


def build_relax_revision_index(work_root: str, relax_subdir: str = "cell-relax") -> dict:
    """一次读取当前 relax 历史，返回仅本轮调用复用的来源版本索引。"""
    from ..submission.state import list_plans, load_plan, read_progress
    result = {"work_root": os.path.normcase(os.path.abspath(work_root)),
              "relax_subdir": relax_subdir, "entries": {}}
    for path in list_plans(work_root, "relax"):
        plan = load_plan(path)
        if plan.get("schema_version") != 2 or plan.get("workflow_version") != "0.0.2":
            continue
        entries = {entry["path"]: dict(entry) for entry in plan["tasks"]}
        for group in plan["groups"]:
            progress = read_progress(path, group["batch"])
            if progress.get("schema_version") != 2 or progress.get("workflow_version") != "0.0.2":
                continue
            for ended in progress.get("finished", []):
                original = entries.get(ended.get("path"))
                if original and all(ended.get(key) == original.get(key) for key in REVISION_FIELDS):
                    original.update(ended)
        for entry in entries.values():
            if (entry.get("calculation_subdir") == relax_subdir
                    and is_revision_id(entry.get("source_input_revision_id"))
                    and is_revision_id(entry.get("input_revision_id"))):
                versions = result["entries"].setdefault(entry["path"], {})
                versions[entry["source_input_revision_id"]] = entry["input_revision_id"] if entry.get("status") == "success" else None
    return result


def selected_relax_revision_id(task_dir: str, config: dict) -> str | None:
    """读取本轮临时索引；非顶层直接调用才回退扫描，不建立跨轮全局缓存。"""
    root = config.get("work_root") or os.path.dirname(task_dir)
    subdir = config.get("relax_subdir", "cell-relax")
    relative = to_relative(task_dir, root)
    source = read_task_revision_id(task_dir)
    index = config.get("_relax_revision_index")
    if (not isinstance(index, dict) or index.get("work_root") != os.path.normcase(os.path.abspath(root))
            or index.get("relax_subdir") != subdir or not isinstance(index.get("entries"), dict)):
        index = build_relax_revision_index(root, subdir)
    return index["entries"].get(relative, {}).get(source)


def revision_matches(task_dir: str, config: dict, stage: str, entry: dict | None) -> bool:
    """核对声明版本、目录及冻结科学设置；不读取或比较输入文件内容。"""
    if not isinstance(entry, dict) or not is_revision_id(entry.get("input_revision_id")):
        return False
    if (entry.get("source_input_revision_id") != read_task_revision_id(task_dir)
            or entry.get("calculation_subdir") != config.get(stage + "_subdir", "cell-relax" if stage == "relax" else "zstar")
            or entry.get("scientific_input") != scientific_input(config, stage)):
        return False
    if stage == "zstar":
        parent = selected_relax_revision_id(task_dir, config)
        if parent is None or entry.get("source_relax_revision_id") != parent:
            return False
    return True


def new_stage_revision(task_dir: str, config: dict, stage: str) -> dict:
    """新阶段输入分配 UUID；后续轮次必须沿用此记录。"""
    result = {"input_revision_id": new_revision_id(),
              "source_input_revision_id": read_task_revision_id(task_dir),
              "calculation_subdir": config.get(stage + "_subdir", "cell-relax" if stage == "relax" else "zstar"),
              "scientific_input": scientific_input(config, stage)}
    if stage == "zstar":
        result["source_relax_revision_id"] = selected_relax_revision_id(task_dir, config)
        if result["source_relax_revision_id"] is None:
            raise IntegrityError("relax_revision_not_associated")
    return result


def validate_stage_inputs(directory: str) -> None:
    """只检查阶段 STRU/INPUT 是普通非空文件；不读正文或证明字节一致性。"""
    for name in ("STRU", "INPUT"):
        try:
            info = os.lstat(join_local(directory, name))
        except OSError as exc:
            raise IntegrityError("missing_stage_input:" + name) from exc
        if (not stat.S_ISREG(info.st_mode) or info.st_size <= 0
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)):
            raise IntegrityError("invalid_stage_input:" + name)


def command_context(config: dict) -> dict:
    """返回命令模板变量；Slurm 环境资源值优先，工作流不自行分配 MPI 进程。"""
    threads = os.environ.get("SLURM_CPUS_PER_TASK") or config.get("omp_threads", 1)
    tasks = os.environ.get("SLURM_NTASKS") or config.get("ntasks", 1)
    if isinstance(tasks, bool) or isinstance(threads, bool) or int(tasks) < 1 or int(threads) < 1:
        raise ValueError("Slurm ntasks/omp_threads must be positive integers")
    return {"ntasks": int(tasks), "omp_threads": int(threads), "symprec": 0.001,
            "xc": config.get("xc", "pbesol"), "kspacing": config.get("kspacing", 0.1)}


def command_argv(template: object, context: dict) -> list:
    """命令字符串按 POSIX shlex 拆词，或接收 argv 列表；不调用 shell。"""
    # 先拆分后替换，保证单个路径或传给 zstar 的 launcher 字符串不会再被外层拆开。
    words = shlex.split(template, posix=True) if isinstance(template, str) else list(template)
    if not words or not all(isinstance(word, str) and word for word in words):
        raise ValueError("empty_or_invalid_command")
    return [word.format_map(context) for word in words]


def classify_failure(text: str, *, include_scf: bool = True) -> dict | None:
    """识别明确失败；relax 可关闭无离子步边界的 SCF 匹配，其他调用默认不变。"""
    lower = text.casefold()
    # 明确的物理拒绝与可修复技术错误分开；退出码 137 本身不等同于 OOM。
    for marker, status, reason in (
        ("insulating-path check failed", "physical_rejected", "能带绝缘检查不通过"),
        ("substantive imaginary gamma modes were found", "physical_rejected", "Gamma 点存在明显虚频"),
        ("symmetry reconstruction failed", "technical_failed", "zstar 对称性处理失败"),
        ("out_of_memory", "technical_failed", "内存超限"),
        ("out of memory", "technical_failed", "内存超限"),
        ("oom-kill", "technical_failed", "内存超限"),
        ("convergence has not been achieved", "technical_failed", "SCF 不收敛"),
        ("charge density convergence is not achieved", "technical_failed", "SCF 不收敛"),
        ("charge density convergence has not been achieved", "technical_failed", "SCF 不收敛"),
        ("scf not converged", "technical_failed", "SCF 不收敛"),
        ("scf did not converge", "technical_failed", "SCF 不收敛"),
        ("no completion marker", "technical_failed", "SCF 输出缺少完成标记"),
    ):
        if not include_scf and reason == "SCF 不收敛":
            continue
        if marker in lower:
            return {"status": status, "reason": reason}
    return None


def _tail(path: str, maximum: int = 131072) -> str:
    """只读日志尾部，不把长 ABACUS 输出整体装进内存。"""
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - maximum))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _read_last_relax_step(path: str, block_size: int = 262144) -> tuple[str, int | None]:
    """反向定位完整的最后离子步标题，返回标题至初始 EOF 的文本和步号。"""
    if isinstance(block_size, bool) or not isinstance(block_size, int) or block_size < 1:
        raise ValueError("relax_log_block_size_must_be_positive_integer")
    header = re.compile(
        rb"^[ \t]*STEP[ \t]+OF[ \t]+(?:ION[ \t]+)?RELAXATION[ \t]*:[ \t]*(\d+)\b",
        re.I,
    )
    with open(path, "rb") as handle:
        handle.seek(0, os.SEEK_END)
        end = handle.tell()
        position = end
        partial_line = b""
        found = None
        # 每次只检查新读块及其右侧未补齐的一行。块首不是已知行首，必须等
        # 前一个块补齐；否则截断的长行可能伪装成 STEP 标题。两种标题共用
        # 同一次从后向前遍历，不能分别查找后按标题类型决定优先级。
        while position > 0 and found is None:
            start = max(0, position - block_size)
            handle.seek(start)
            data = handle.read(position - start)
            if len(data) != position - start:
                raise OSError("弛豫日志在读取期间被截断")
            data += partial_line
            position = start
            if start:
                newline = data.find(b"\n")
                if newline < 0:
                    partial_line = data
                    continue
                complete_start = newline + 1
                partial_line = data[:complete_start]
            else:
                complete_start = 0
                partial_line = b""
            offset = start + len(data)
            for line in reversed(data[complete_start:].splitlines(keepends=True)):
                offset -= len(line)
                match = header.match(line)
                if match:
                    found = (offset, int(match.group(1)))
                    break
        if found is None:
            return "", None
        # 定位后仅拼接命中的末步；没有标题时不累积整份历史日志。读取块
        # 大小不是返回文本的内存上限，特别长的最后一步仍需保存其完整文本。
        handle.seek(found[0])
        remaining = end - found[0]
        chunks = []
        while remaining:
            chunk = handle.read(min(block_size, remaining))
            if not chunk:
                raise OSError("弛豫日志在读取期间被截断")
            chunks.append(chunk)
            remaining -= len(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace"), found[1]


def inspect_relax(task_dir: str, config: dict, revision_entry: dict | None = None) -> dict:
    """只读检查完整末步；正常结束且无最终失败时，再核验结束产物和末态。"""
    directory = calculation_directory(task_dir, config, "relax")
    result = {"status": "pending", "step": "cell-relax", "reason": "尚未完成", "evidence": []}
    if not os.path.isdir(directory):
        return result
    try:
        validate_stage_inputs(directory)
    except IntegrityError as exc:
        return dict(result, status="input_failed", reason=str(exc))
    # 声明输入世代改变后不能沿用旧轮次的完成产物。
    if revision_entry is not None:
        try:
            if not revision_matches(task_dir, config, "relax", revision_entry):
                return dict(result, status="input_failed", reason="计算输入声明版本或科学设置与本轮计划不一致")
        except (OSError, ValueError) as exc:
            return dict(result, status="input_failed", reason=str(exc))
    logs = sorted(glob.glob(join_local(directory, "OUT.*", "running_cell-relax.log")))
    structures = sorted(glob.glob(join_local(directory, "OUT.*", "STRU_ION_D")))
    if len(logs) > 1 or len(structures) > 1:
        return dict(result, status="input_failed", reason="多个 OUT 目录导致弛豫输入来源不唯一")
    if not logs:
        return dict(result, status="technical_failed", reason="弛豫日志不存在: running_cell-relax.log")
    path = logs[0]
    evidence = [to_relative(path, task_dir)]
    try:
        text, last_step = _read_last_relax_step(path)
    except OSError as exc:
        return dict(result, status="technical_failed", reason="弛豫日志无法可靠读取: " + str(exc), evidence=evidence)
    # 只在这个完整末步内按词序识别四个短字段；大小写、空白和分隔标点
    # 不影响匹配。SCF 失败一旦出现即保留，后续成功或再次 INIT SCF 不清除。
    words = re.sub(r"[^a-z0-9]+", " ", text.casefold())
    if last_step is not None:
        result["step"] = "cell-relax / ionic step " + str(last_step)
        scf_events = re.finditer(r"\bconvergence (has not been achieved|is achieved)\b", words)
        if any(event.group(1) == "has not been achieved" for event in scf_events):
            return dict(result, status="technical_failed", reason="SCF 不收敛", evidence=evidence)
    # 没有边界时仍保留 OOM 等硬错误的短原因；共享尾部读取器本身不改动。
    failure = classify_failure(text if last_step is not None else _tail(path), include_scf=False)
    if failure:
        return dict(result, **failure, evidence=evidence)
    if last_step is None:
        return dict(result, status="technical_failed", reason="弛豫日志缺少完整的离子步标题", evidence=evidence)
    complete = bool(re.search(r"\b(?:total times?|finish(?:ed)? time)\b", words))
    if not complete:
        return dict(result, status="technical_failed", reason="ABACUS 日志缺少正常结束标记", evidence=evidence)

    # 整体 Relaxation 结论覆盖此前的两个组件结论；组件事件只更新自身。
    # 因而较后的 Ion 成功不能清除 Lattice 失败，而末尾整体成功可以确认
    # 两者最终都已完成。完整末步正常结束且缺少这些字段时，按无负面接受。
    ion_failed = lattice_failed = False
    for event in re.finditer(r"\b(?:(ion|lattice|structural) )?relaxation is (not )?converged\b", words):
        failed = event.group(2) is not None
        if event.group(1) == "ion":
            ion_failed = failed
        elif event.group(1) == "lattice":
            lattice_failed = failed
        else:
            ion_failed = lattice_failed = failed
    converged = not (ion_failed or lattice_failed)
    # 最后允许步数可以正常收敛，只有最终仍有离子/晶格失败时才查上限。
    limit = config.get("relax_max_steps")
    input_path = join_local(directory, "INPUT")
    if limit is None and os.path.isfile(input_path):
        with open(input_path, encoding="utf-8-sig") as handle:
            match = re.search(r"(?m)^\s*relax_nmax\s+(\d+)", handle.read())
        limit = int(match.group(1)) if match else None
    if not converged and limit and last_step >= limit:
        return dict(result, status="technical_failed", reason="relax 达到步数上限仍未收敛", evidence=evidence)
    if not converged:
        return dict(result, status="technical_failed", reason="relax 最后离子步未收敛", evidence=evidence)
    # 原 Bash 的两份结束 JSON 仍保留，并增加机械解析，空文件不能证明完成。
    markers = all(os.path.isfile(join_local(directory, name)) and os.path.getsize(join_local(directory, name)) > 0
                  for name in ("abacus.json", "time.json"))
    if markers and complete and converged and len(structures) == 1:
        try:
            for name in ("abacus.json", "time.json"):
                with open(join_local(directory, name), encoding="utf-8-sig") as handle:
                    json.load(handle)
            validate_geometry(read_structure(structures[0]), config.get("minimum_distance", 0.1))
        except Exception as exc:
            return dict(result, status="input_failed", reason="弛豫产物不可可靠读取: " + str(exc), evidence=evidence)
        return dict(result, status="success", reason="", evidence=evidence,
                    relaxed_structure=to_relative(structures[0], task_dir))
    return dict(result, evidence=evidence)


def prepare_relax(task_dir: str, config: dict) -> dict:
    """初次复制本任务 STRU/INPUT；已有目录只验证，不覆盖续算输入。"""
    directory = calculation_directory(task_dir, config, "relax")
    try:
        existing = config.get("_revision_entry")
        if os.path.isdir(directory) and not revision_matches(task_dir, config, "relax", existing):
            raise IntegrityError("existing_calculation_requires_current_revision_or_new_directory")
        revision = existing or new_stage_revision(task_dir, config, "relax")
        if not os.path.isdir(directory):
            source = join_local(task_dir, "STRU")
            validate_geometry(read_structure(source), config.get("minimum_distance", 0.1))
            input_path = config.get("relax_input_file") or join_local(task_dir, "INPUT")
            with open(source, "rb") as handle:
                structure_bytes = handle.read()
            with open(input_path, "rb") as handle:
                input_bytes = handle.read()
            if not input_bytes.strip():
                raise ValueError("empty_relax_INPUT")
            os.mkdir(directory)
            atomic_write_bytes(join_local(directory, "STRU"), structure_bytes)
            atomic_write_bytes(join_local(directory, "INPUT"), input_bytes)
        else:
            # 已有目录检查缺损但不覆盖；初次复制后的输入不再回读。
            validate_stage_inputs(directory)
        return {"status": "pending", "reason": "", "step": "cell-relax", "evidence": [],
                **{key: revision[key] for key in REVISION_FIELDS if key in revision}}
    except Exception as exc:
        return {"status": "input_failed", "reason": str(exc), "step": "prepare relax", "evidence": []}


def run_relax(task_dir: str, config: dict, on_command: object) -> dict:
    """执行一个 ABACUS 外层命令，结合机械完成检查与本次输出返回最终状态。"""
    directory = calculation_directory(task_dir, config, "relax")
    expected = config.get("_revision_entry")
    validate_stage_inputs(directory)
    if not revision_matches(task_dir, config, "relax", expected):
        raise IntegrityError("calculation_revision_not_associated")
    argv = command_argv(config.get("abacus_command", "mpirun -np {ntasks} abacus"), command_context(config))
    raw = on_command("relax", argv, directory)
    execution = raw if isinstance(raw, dict) else {"returncode": int(raw)}
    outcome = inspect_relax(task_dir, config, expected)
    # 非零退出即使留有旧完成文件也不能成功；优先当前输出的明确错误。
    current = classify_failure(execution.get("output_tail", ""), include_scf=False)
    # worker 的非零退出元数据仍可能来自跨离子步的旧 SCF 尾部提示；它
    # 不能冒充本末步的 SCF 证据。非零退出本身仍在下方归为技术失败。
    if (execution.get("reason") and execution.get("status") in {"technical_failed", "input_failed", "physical_rejected"}
            and execution["reason"] != "SCF 不收敛"):
        current = {"status": execution.get("status", "technical_failed"), "reason": execution["reason"]}
    if outcome["status"] == "technical_failed" and outcome["reason"] == "SCF 不收敛":
        pass  # 完整末步已经确认的 SCF 根因不被后续输出或回调状态覆盖。
    elif current:
        outcome.update(current)
        if execution.get("evidence"):
            outcome["evidence"] = execution["evidence"]
    elif execution["returncode"] and outcome["status"] != "input_failed":
        if outcome["status"] != "technical_failed":
            outcome.update(status="technical_failed", reason=f"ABACUS 意外退出，返回码 {execution['returncode']}")
    elif outcome["status"] == "pending":
        outcome.update(status="technical_failed", reason="ABACUS 结束但弛豫完成/收敛依据不完整")
    outcome["commands"] = [{"command": "relax", "returncode": execution["returncode"]}]
    return outcome
