"""zstar 输入隔离、轻量完成判据和统一的四命令续算入口。"""

import glob
import json
import math
import os
import re
import shlex
import time

from ..common.atomic import atomic_write_bytes, atomic_write_text
from ..common.errors import IntegrityError
from ..common.paths import from_relative, join_local, to_relative
from .relax import (calculation_directory, classify_failure, command_argv, command_context,
                    revision_matches, new_stage_revision, REVISION_FIELDS, inspect_relax, validate_stage_inputs, _tail)
from .symmetry import (read_structure, sanitize_structure,
                       sanitized_stru_text, validate_geometry)


def _strict_json(path: str) -> object:
    """只读严格 JSON，拒绝 Python 默认宽松接受的 NaN/Infinity。"""
    def reject_constant(value: str) -> None:
        """向 json 解码器报告非标准数值常量。"""
        raise ValueError("nonfinite_json_constant:" + value)

    with open(path, encoding="utf-8-sig") as handle:
        return json.load(handle, parse_constant=reject_constant)


def _nonempty(path: str) -> bool:
    """检查普通非空产物，不接受指向外部位置的符号链接。"""
    return not os.path.islink(path) and os.path.isfile(path) and os.path.getsize(path) > 0


def _finite_tensor_file(path: str, born: bool = False) -> None:
    """轻量解析 BEC/BORN 数值载荷，保留 legacy 星标索引及标准科学计数格式。"""
    with open(path, encoding="utf-8-sig") as handle:
        lines = [line.split("#", 1)[0].strip() for line in handle]
    count = 0
    for line in lines:
        if not line:
            continue
        if not born and re.match(r"No\.\s+Atom\b", line, re.I):
            continue
        if born:
            fields = line.split()
            # Phonopy BORN 允许第一行单独写单位换算因子；当前 zstar 使用注释头。
            factor = count == 0 and len(fields) == 1
            if not factor and len(fields) not in (3, 9):
                raise ValueError("invalid_BORN_tensor_row")
        else:
            match = re.match(r"^\*?\s*\d+\s+\S+\s+(.+)$", line)
            if not match:
                raise ValueError("invalid_BEC_tensor_row")
            fields = match.group(1).split()
            if len(fields) != 9:
                raise ValueError("BEC_tensor_requires_nine_components")
            factor = False
        for field in fields:
            value = float(field.replace("D", "e").replace("d", "e"))
            if not math.isfinite(value):
                raise ValueError("nonfinite_tensor_component")
        if not factor:
            count += len(fields)
    if count < (18 if born else 9) or count % 9:
        raise ValueError("missing_or_incomplete_tensor_payload")


def native_status(task_dir: str, config: dict, since: float | None = None, failed_only: bool = False) -> dict:
    """直接读取 .zstar/stages JSON，返回当前内部步骤和短错误；不实例化 native store。"""
    directory = calculation_directory(task_dir, config, "zstar")
    records = []
    for path in glob.glob(join_local(directory, ".zstar", "stages", "*.json")):
        try:
            state = _strict_json(path)
            if not isinstance(state, dict):
                continue
            if since is not None and os.path.getmtime(path) < since:
                continue
            if failed_only and state.get("status") not in ("failed", "error"):
                continue
            # 旧绝对 path 不参与寻址；name 属于相对阶段身份，仅用于显示。
            state = dict(state, evidence=to_relative(path, task_dir))
            records.append((os.path.getmtime(path), state))
        except (OSError, ValueError, TypeError):
            continue
    if not records:
        return {"step": "zstar", "reason": "", "evidence": []}
    records.sort(key=lambda pair: pair[0], reverse=True)
    # 最新变更的阶段优先，旧阶段遗留 running 不能压过本次真正 failed 的阶段。
    current = records[0][1]
    name = str(current.get("name", "zstar"))
    part = next((key for key in ("scf", "band", "pyatb", "dielectric") if current.get(key) == "running"), "")
    text = str(current.get("error") or "")
    failure = classify_failure(text)
    # native 的 log 保留子程序输出，明确 SCF 错误可能只出现在这里；只检查当前失败阶段。
    evidence = [current["evidence"]]
    if current.get("status") in ("failed", "error"):
        safe_name = name.replace("/", "__").replace("\\", "__")
        path = join_local(directory, ".zstar", "logs", safe_name + ".log")
        if os.path.isfile(path):
            log_tail = _tail(path)
            # native 追加日志可能携带上次失败；取最新命令段，避免将旧错误嫁接到本次。
            for separator in ("\n$ ", "\n[command]", "\nCOMMAND:"):
                if separator in log_tail:
                    log_tail = log_tail.rsplit(separator, 1)[-1]
            failure = classify_failure(log_tail) or failure
            evidence.append(to_relative(path, task_dir))
        try:
            stage_dir = from_relative(directory, name)
            for scf_log in glob.glob(join_local(stage_dir, "OUT.*", "running_scf.log")) if current.get("scf") in ("running", "failed", "error") else []:
                if since is not None and os.path.getmtime(scf_log) < since:
                    continue
                scf_failure = classify_failure(_tail(scf_log))
                if scf_failure:
                    failure = scf_failure
                    evidence.append(to_relative(scf_log, task_dir))
        except (ValueError, OSError):
            # 不使用 native 的历史绝对路径，也不允许非法阶段名越出当前计算目录。
            pass
    result = {"step": name + (" / " + part.upper() if part else ""),
              "reason": text[:300], "evidence": evidence}
    if failure:
        result.update(failure)
    elif current.get("status") in ("failed", "error"):
        result["status"] = "technical_failed"
        result["reason"] = text[:300] or "zstar 内部阶段失败"
    return result


def inspect_zstar(task_dir: str, config: dict, revision_entry: dict | None = None) -> dict:
    """只读完成检查：带隙结论、非空 BEC/BORN、有限 3×3 静态张量及输入关联。"""
    directory = calculation_directory(task_dir, config, "zstar")
    result = {"status": "pending", "step": "zstar", "reason": "尚未完成", "evidence": []}
    if not os.path.isdir(directory):
        return result
    try:
        validate_stage_inputs(directory)
    except IntegrityError as exc:
        return dict(result, status="input_failed", reason=str(exc))
    if revision_entry is not None:
        try:
            if not revision_matches(task_dir, config, "zstar", revision_entry):
                return dict(result, status="input_failed", reason="zstar 声明版本或科学设置与本轮计划不一致")
        except (OSError, ValueError) as exc:
            return dict(result, status="input_failed", reason=str(exc))
    native = native_status(task_dir, config)
    gate = join_local(directory, "0.no-move", "zstar_insulation.json")
    gap = None
    if os.path.isfile(gate):
        try:
            gap = _strict_json(gate)
        except (ValueError, OSError):
            gap = None
    # 物理拒绝只有与已记录当前输入声明关联时才能跳过；旧未绑定输出应交 native 续算判断。
    if revision_entry is not None and isinstance(gap, dict) and gap.get("insulating") is False:
        return dict(result, status="physical_rejected", reason="能带绝缘检查不通过",
                    evidence=[to_relative(gate, task_dir)])
    if native.get("status") and revision_entry is not None:
        return dict(result, **native)
    result.update({key: native[key] for key in ("step", "evidence")})

    # 成功检查限定用户指定的产物；不增加 qpoints.yaml / irreps.yaml 等冗余保险。
    band_root = join_local(directory, "0.no-move", "pyatb-band", "Out")
    band_pairs = []
    if os.path.isdir(band_root):
        for parent, _, names in os.walk(band_root, followlinks=False):
            if "band.dat" in names and "band_info.dat" in names and all(_nonempty(join_local(parent, name)) for name in ("band.dat", "band_info.dat")):
                band_pairs.append(parent)
    required = ("BEC.dat", "BEC.rep.dat", "BORN", "dielectric_response/static_response.json")
    if not band_pairs or not all(_nonempty(join_local(directory, *name.split("/"))) for name in required):
        return result
    if not isinstance(gap, dict) or gap.get("insulating") is not True:
        return dict(result, reason="能带产物存在，但缺少有效绝缘检查结论")
    for name in ("BEC.dat", "BEC.rep.dat", "BORN"):
        try:
            _finite_tensor_file(join_local(directory, name), born=name == "BORN")
        except (ValueError, OSError) as exc:
            return dict(result, status="technical_failed", reason=f"{name} 张量载荷无效: {exc}",
                        evidence=[to_relative(join_local(directory, name), task_dir)])
    try:
        response = _strict_json(join_local(directory, "dielectric_response", "static_response.json"))
        tensor = response["tensor"]
        if not isinstance(tensor, list) or len(tensor) != 3:
            raise ValueError("static_tensor_shape")
        for row in tensor:
            if not isinstance(row, list) or len(row) != 3 or any(type(value) not in (int, float) or not math.isfinite(value) for value in row):
                raise ValueError("static_tensor_nonfinite_or_shape")
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return dict(result, status="technical_failed", reason="静态响应 JSON/张量无效: " + str(exc),
                    evidence=[to_relative(join_local(directory, "dielectric_response", "static_response.json"), task_dir)])
    # 第一次接管旧任务不能只给旧产物贴上新输入版本；必须有原轮次版本或实际重跑四命令。
    if revision_entry is None:
        return dict(result, reason="历史产物尚未与当前输入版本关联，需交 zstar 续算核验")
    return dict(result, status="success", step="finished", reason="")


def prepare_zstar(task_dir: str, config: dict) -> dict:
    """新任务由可信 relaxed 原胞对称化；既有 zstar/STRU 只读验证，绝不重写。"""
    directory = calculation_directory(task_dir, config, "zstar")
    summary = None
    try:
        revision = config.get("_revision_entry")
        if os.path.isdir(directory) and not revision_matches(task_dir, config, "zstar", revision):
            raise ValueError("existing_calculation_requires_current_revision_or_new_directory")
        revision = revision or new_stage_revision(task_dir, config, "zstar")
        if os.path.isdir(directory):
            # 续算无论 native 当前模式如何都保持原 STRU 字节不变。
            validate_stage_inputs(directory)
        else:
            relaxed_info = inspect_relax(task_dir, config)
            if relaxed_info["status"] != "success":
                raise ValueError("relax_not_ready: " + relaxed_info["reason"])
            relaxed_path = from_relative(task_dir, relaxed_info["relaxed_structure"])
            initial_path = join_local(calculation_directory(task_dir, config, "relax"), "STRU")
            initial = read_structure(initial_path)
            relaxed = read_structure(relaxed_path)
            output, summary = sanitize_structure(initial, relaxed, config)
            with open(initial_path, encoding="utf-8-sig") as handle:
                resource_text = handle.read()
            with open(relaxed_path, encoding="utf-8-sig") as handle:
                relaxed_text = handle.read()
            text = sanitized_stru_text(output, resource_text, relaxed_text, summary)
            # 所有可失败的物理转换先在内存完成，再创建计算目录并发布；旧目录不覆盖。
            os.mkdir(directory)
            atomic_write_text(join_local(directory, "STRU"), text)
            input_source = join_local(calculation_directory(task_dir, config, "relax"), "INPUT")
            with open(input_source, "rb") as handle:
                atomic_write_bytes(join_local(directory, "INPUT"), handle.read())
        return {"status": "pending", "step": "zstar", "reason": "", "evidence": [],
                **{key: revision[key] for key in REVISION_FIELDS if key in revision}, "symmetry": summary}
    except Exception as exc:
        return {"status": "input_failed", "step": "prepare zstar", "reason": str(exc), "evidence": []}


def zstar_command_set(config: dict) -> list:
    """生成 pre/run/post/static 有序 argv；默认保留 --force 与统一 1e-3 容差。"""
    context = command_context(config)
    # launcher 作为一个字符串参数交给 native；外层执行始终 shell=False。
    for key, default in (("abacus_command", "mpirun -np {ntasks} abacus"), ("pyatb_command", "mpirun -np {ntasks} pyatb")):
        context[key] = shlex.join(command_argv(config.get(key, default), context))
    executable = command_argv(config.get("zstar_command", ["zstar"]), context)
    defaults = {
        "pre": executable + ["bec", "pre", "--stru", "STRU", "--xc", "{xc}", "--kspacing", "{kspacing}", "--symmprec", "{symprec}", "--force"],
        "run": executable + ["bec", "run", "--root", ".", "--abacus-command", "{abacus_command}", "--pyatb-command", "{pyatb_command}", "--omp-threads", "{omp_threads}"],
        "post": executable + ["bec", "post", "--root", ".", "--symmprec", "{symprec}"],
        "static": executable + ["dielectric", "static"],
    }
    overrides = config.get("zstar_commands") or {}
    return [(name, command_argv(overrides.get(name, defaults[name]), context)) for name in ("pre", "run", "post", "static")]


def run_zstar(task_dir: str, config: dict, on_command: object) -> dict:
    """逐一执行四条命令，单条失败仍继续；首个明确根因优先且不被后续成功覆盖。"""
    expected = config.get("_revision_entry")
    directory = calculation_directory(task_dir, config, "zstar")
    validate_stage_inputs(directory)
    if not revision_matches(task_dir, config, "zstar", expected):
        raise IntegrityError("calculation_revision_not_associated")
    commands = []
    failures = []
    for name, argv in zstar_command_set(config):
        started = time.time()
        raw = on_command(name, argv, directory)
        execution = raw if isinstance(raw, dict) else {"returncode": int(raw)}
        code = int(execution["returncode"])
        commands.append({"command": name, "returncode": code})
        text = execution.get("output_tail", "")
        failure = classify_failure(text)
        if execution.get("reason") and execution.get("status"):
            failure = {"status": execution.get("status", "technical_failed"), "reason": execution["reason"]}
        # pre 的已存在提示完全交给 native；识别已知提示只影响最终归因，不改变执行顺序。
        expected_existing = name == "pre" and "existing ensemble files" in text.casefold()
        if failure or (code and not expected_existing):
            explicit = failure is not None
            if failure is None:
                failure = {"status": "technical_failed", "reason": f"zstar {name} 返回码 {code}",
                           "detail": str(execution.get("reason") or "")[:300]}
            failures.append(dict(failure, command=name, explicit=explicit, evidence=execution.get("evidence", [])))
        # 及时读取本次 run 的 native 根因，防止 post/static 的缺文件错误覆盖 SCF 原因。
        if name == "run" and code:
            native = native_status(task_dir, config, since=started, failed_only=True)
            if native.get("reason"):
                detail = {"status": native.get("status", "technical_failed"), "reason": native["reason"],
                          "command": name, "explicit": classify_failure(native["reason"]) is not None
                          or native["reason"] in ("SCF 不收敛", "能带绝缘检查不通过", "内存超限", "zstar 对称性处理失败"),
                          "evidence": native.get("evidence", [])}
                if failures and failures[-1].get("command") == name and failures[-1]["reason"].startswith("zstar run 返回码"):
                    failures[-1] = detail
                elif not failures:
                    failures.append(detail)
    outcome = inspect_zstar(task_dir, config, expected)
    if failures:
        # 最早的可定位原因是主因，其余命令失败只作为补充，不能变成多条独立任务失败。
        # 泛化的“非零返回码”只有退出事实，明确 SCF/OOM 等才提供可定位根因；保留
        # 较早的泛化错误为补充，但不能让它压过真正从当前命令/原生日志读出的原因。
        primary = next((item for item in failures if item.get("explicit")), failures[0])
        outcome.update(status=primary["status"], reason=primary["reason"], step=primary["command"])
        outcome["evidence"] = primary.get("evidence", [])
        outcome["supplementary"] = [{key: item[key] for key in ("command", "reason", "evidence")} for item in failures if item is not primary]
    elif outcome["status"] == "pending":
        outcome.update(status="technical_failed", reason="四条命令结束，但必要产物或绝缘结论不完整")
    outcome["commands"] = commands
    return outcome
