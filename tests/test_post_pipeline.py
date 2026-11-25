"""新版后处理真实记录链回归；仅在项目测试临时目录内构造轻量计算产物。"""

import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from uuid import uuid4

from hpc_workflow.calculations.relax import new_stage_revision
from hpc_workflow.common.atomic import atomic_write_json, atomic_write_text, read_json
from hpc_workflow.common.errors import WorkflowError
from hpc_workflow.common.paths import join_local, project_root
from hpc_workflow.preprocess.planner import task_id_for


def file_bytes(path: str) -> bytes:
    """测试专用字节比较，证明只读或失败路径未写文件；生产代码不使用此替代内容摘要。"""
    return Path(path).read_bytes()


def make_revision(task: str, execution: dict, stage: str) -> dict:
    """生成显式版本夹具；zstar 关联已有的最小成功 relax 计划，不新增记录种类。"""
    if stage == "zstar":
        path = join_local(os.path.dirname(task), ".hpc_runs", "relax", "fixture-" + os.path.basename(task), "plan.json")
        if not os.path.exists(path):
            source = make_revision(task, execution, "relax")
            atomic_write_json(path, {"schema_version": 2, "workflow_version": "0.0.2", "stage": "relax",
                                    "task_root": "../../..", "execution": execution, "parallel_jobs": 0,
                                    "tasks": [{"path": os.path.basename(task), "status": "success", **source}], "groups": []})
    return new_stage_revision(task, execution, stage)


class PostPipelineTests(unittest.TestCase):
    """把前处理签名、计划冻结参数、worker 终态及物性读取串起来验证。"""

    def setUp(self):
        """建立新版项目内部临时工作根和显式输出地址，不触碰真实批次。"""
        artifacts = join_local(project_root(), ".test-artifacts")
        os.makedirs(artifacts, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=artifacts)
        self.addCleanup(self.temporary.cleanup)
        self.base = self.temporary.name
        self.work = join_local(self.base, "one batch")
        os.mkdir(self.work)
        self.output = join_local(self.base, "results.csv")
        self.config = {"work_root": self.work, "output_csv": self.output,
                       "zstar_subdir": "zstar",
                       "band_relative_root": "0.no-move/pyatb-band/Out"}
        self.execution = {"relax_subdir": "cell-relax", "zstar_subdir": "zstar", "xc": "pbesol",
                          "kspacing": 0.1, "symprec": 0.001, "zstar_commands": None}
        self.sequence = 0

    def make_task(self, source: str, source_id: str, priority: int = 1,
                  nominal: dict | None = None) -> str:
        """生成通过既有 inspect_existing 校验的前处理任务，不依赖 ASE 写出器。"""
        namespace = "post-test"
        record = SimpleNamespace(input_source=source, source_record_id=source_id)
        name = task_id_for(record, namespace)
        task = join_local(self.work, name)
        os.mkdir(task)
        # 前处理只检查当前协议及三份普通非空产物；载荷不交给数值计算程序。
        for filename, text in (("STRU", "isolated test structure\n"), ("INPUT", "INPUT_PARAMETERS\n"),
                               ("primitive.cif", "data_test\n")):
            atomic_write_text(join_local(task, filename), text)
        components = {"identity": {"source": source, "namespace": namespace, "id": source_id}}
        manifest = {"schema_version": 2, "workflow_version": "0.0.2", "status": "success", "task_id": name,
                    "input_source": source, "dataset_namespace": namespace, "source_record_id": source_id,
                    "formula": "Si", "elements": ["Si"], "global_sample_id": source_id if source == "MJM" else None,
                    "dft_priority": priority if source == "MJM" else None,
                    "source_metadata": nominal or {}, "input_description": components,
                    "input_revision_id": str(uuid4()), "source_file": "source.cif",
                    "products": ["STRU", "INPUT", "primitive.cif"]}
        atomic_write_json(join_local(task, "task_manifest.json"), manifest)
        directory = join_local(task, "zstar")
        os.mkdir(directory)
        atomic_write_text(join_local(directory, "STRU"), "frozen zstar test structure\n")
        atomic_write_text(join_local(directory, "INPUT"), "INPUT_PARAMETERS\n")
        return task

    def make_products(self, task: str, total: float = 20.0, gap: float = 1.2,
                      include_static: bool = True, insulating: bool = True) -> None:
        """写出最小真实格式产物；无需 qpoints、irreps 或任何赝势轨道资源。"""
        directory = join_local(task, "zstar")
        band = join_local(directory, "0.no-move", "pyatb-band", "Out", "Band_Structure")
        os.makedirs(band, exist_ok=True)
        atomic_write_text(join_local(band, "band.dat"), "0.0 1.0\n")
        atomic_write_text(join_local(band, "band_info.dat"), f"Band gap (eV): {gap}\n")
        atomic_write_json(join_local(directory, "0.no-move", "zstar_insulation.json"),
                          {"insulating": insulating, "gap_eV": gap, "threshold_eV": 0.01})
        atomic_write_text(join_local(directory, "BORN"),
                          "# epsilon and Born\n2 0 0 0 2 0 0 0 2\n1 0 0 0 1 0 0 0 1\n")
        for filename in ("BEC.dat", "BEC.rep.dat"):
            atomic_write_text(join_local(directory, filename), "# atom species tensor\n1 Si 1 2 3 4 5 6 7 8 9\n")
        if include_static:
            atomic_write_json(join_local(directory, "dielectric_response", "static_response.json"),
                              {"frequency_cm-1": 0.0, "response_kind": "total relative dielectric tensor",
                               "response_unit": "1", "response_convention": "relative permittivity",
                               "electronic_response_included": True,
                               "tensor": [[total, 0, 0], [0, total, 0], [0, 0, total]]})

    def make_plan(self, entries: list[tuple[str, str, str]], execution: dict | None = None) -> str:
        """发布真实布局的冻结计划与每批进度；空状态表示该结构尚未执行结束。"""
        self.sequence += 1
        frozen = dict(self.execution if execution is None else execution)
        directory = join_local(self.work, ".hpc_runs", "zstar", f"20261024-{self.sequence:04d}")
        os.makedirs(directory)
        tasks, finished = [], []
        for task, state, reason in entries:
            name = os.path.basename(task)
            revision = make_revision(task, frozen, "zstar")
            tasks.append({"path": name, "status": "planned", **revision})
            if state:
                finished.append({"path": name, "status": state, "reason": reason,
                                 **revision, "evidence": []})
        plan = {"schema_version": 2, "workflow_version": "0.0.2", "stage": "zstar", "run_id": os.path.basename(directory),
                "task_root": "../../..", "created_at": f"2026-10-24T00:{self.sequence:02d}:00+00:00",
                "parallel_jobs": 1, "execution": frozen, "tasks": tasks,
                "groups": [{"batch": 1, "tasks": [entry["path"] for entry in tasks]}]}
        path = join_local(directory, "plan.json")
        atomic_write_json(path, plan)
        atomic_write_json(join_local(directory, "batches", "0001", "progress.json"),
                          {"schema_version": 2, "workflow_version": "0.0.2", "batch": 1,
                           "status": "finished" if len(finished) == len(tasks) else "running",
                           "current_task": next((entry["path"] for entry in tasks
                                                 if entry["path"] not in {value["path"] for value in finished}), None),
                           "current_command": "run", "finished": finished})
        return path

    def read_rows(self) -> list[dict[str, str]]:
        """用标准 CSV 读取器回读最终文件，检查 JSON/中文备注的引用没有破坏列数。"""
        with open(self.output, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(len(reader.fieldnames), 46)
            rows = list(reader)
        self.assertTrue(all(None not in row and all(value is not None for value in row.values()) for row in rows))
        return rows

    def test_auto_cleanup_reuses_one_collection_and_one_parse(self):
        """自动清理共享一次发现和物性解析，父版本与历史JSON均不重复读取。"""
        from hpc_workflow.postprocess import pipeline
        task = self.make_task("MP", "mp-one-parse")
        self.make_products(task)
        self.make_plan([(task, "success", "")])
        with patch.object(pipeline, "_collect_batch", wraps=pipeline._collect_batch) as collected, \
                patch.object(pipeline, "discover_tasks", wraps=pipeline.discover_tasks) as discovered, \
                patch.object(pipeline, "read_task_data", wraps=pipeline.read_task_data) as parsed, \
                patch("hpc_workflow.postprocess.cleanup.load_plan",
                      side_effect=AssertionError("清理不得重读本轮已读计划")), \
                patch("hpc_workflow.postprocess.cleanup.read_jobs",
                      side_effect=AssertionError("清理不得重读本轮已读提交台账")), \
                patch("hpc_workflow.postprocess.cleanup.read_progress",
                      side_effect=AssertionError("清理不得重读本轮已读进度")), \
                patch("hpc_workflow.calculations.relax.build_relax_revision_index",
                      side_effect=AssertionError("post 必须复用已读取历史，不得再次全批读取")):
            result = pipeline.run_post(self.config)
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(collected.call_count, 1)
        self.assertEqual(discovered.call_count, 1)
        self.assertEqual(parsed.call_count, 1)

    def test_mixed_batch_sort_and_all_mp_global_ids_blank(self):
        """一根一表同时适配 MP/MJM；按总迹平均数值降序，编号和 priority 不影响排序。"""
        from hpc_workflow.postprocess.pipeline import run_post
        low = self.make_task("MP", "mp-101")
        high = self.make_task("MJM", "27", priority=99,
                              nominal={"nominal_k_available": "True", "nominal_k_value": "45.5"})
        mid = self.make_task("MP", "mp-202")
        # MP 来源即使历史元数据误携带同名编号，也不能把它冒充 MJM 全局样本号。
        manifest_path = join_local(mid, "task_manifest.json")
        with open(manifest_path, encoding="utf-8") as handle:
            manifest = json.load(handle)
        manifest["global_sample_id"] = "123456"
        atomic_write_json(manifest_path, manifest)
        for task, value in ((low, 9.0), (high, 100.0), (mid, 20.0)):
            self.make_products(task, value)
        self.make_plan([(task, "success", "") for task in (low, high, mid)])
        report = run_post(self.config)
        self.assertEqual(report["status"], "success", report)
        rows = self.read_rows()
        self.assertEqual([float(row["dielectric_total_trace_mean"]) for row in rows], [100.0, 20.0, 9.0])
        self.assertEqual(rows[0]["global_sample_id"], "27")
        self.assertEqual(rows[0]["nominal_k_value"], "45.5")
        self.assertTrue(all(row["global_sample_id"] == "" for row in rows if row["input_source"] == "MP"))
        self.assertTrue(all(row["remarks"] == "" for row in rows))
        self.assertEqual(json.loads(rows[0]["born_effective_charges_json"])[0]["z_displacement_polarization"],
                         [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0], [7.0, 8.0, 9.0]])

    def test_physical_and_scf_failures_remain_one_partial_row_each(self):
        """虚频保留已完成带隙/BEC，SCF 是根因；缺结果不伪装为零，也不额外生成三行。"""
        from hpc_workflow.postprocess.pipeline import run_post
        imaginary = self.make_task("MJM", "10")
        scf = self.make_task("MP", "mp-301")
        self.make_products(imaginary, include_static=False)
        plan = self.make_plan([(imaginary, "physical_rejected", "Gamma 点存在明显虚频"),
                               (scf, "technical_failed", "SCF 不收敛")])
        # 后续 post/static 缺文件只作为辅助证据，不把它们提升为新的根因或新的任务。
        progress_path = join_local(os.path.dirname(plan), "batches", "0001", "progress.json")
        with open(progress_path, encoding="utf-8") as handle:
            progress = json.load(handle)
        progress["finished"][1]["supplementary"] = [{"command": "post", "reason": "缺少输入结果"},
                                                   {"command": "static", "reason": "缺少 BORN"}]
        atomic_write_json(progress_path, progress)
        report = run_post(self.config)
        self.assertEqual(report["status"], "success", report)
        rows = {row["source_record_id"]: row for row in self.read_rows()}
        self.assertEqual(len(rows), 2)
        self.assertIn("虚频", rows["10"]["remarks"])
        self.assertEqual(rows["10"]["band_gap_ev"], "1.2")
        self.assertEqual(rows["10"]["bec_inequivalent_atom_count"], "1")
        self.assertEqual(rows["10"]["dielectric_total_trace_mean"], "")
        self.assertIn("SCF 不收敛", rows["mp-301"]["remarks"])
        self.assertEqual(rows["mp-301"]["dielectric_total_trace_mean"], "")
        self.assertEqual(rows["mp-301"]["global_sample_id"], "")

    def test_gap_rejection_preserves_zero_gap_and_empty_static(self):
        """零带隙是有效数值；明确绝缘拒绝仍导出，缺失的物性单元格保持空白。"""
        from hpc_workflow.postprocess.pipeline import run_post
        task = self.make_task("MP", "mp-metal")
        self.make_products(task, gap=0.0, include_static=False, insulating=False)
        self.make_plan([(task, "physical_rejected", "能带绝缘检查不通过")])
        self.assertEqual(run_post(self.config)["status"], "success")
        row = self.read_rows()[0]
        self.assertEqual(float(row["band_gap_ev"]), 0.0)
        self.assertIn("能带", row["remarks"])
        self.assertEqual(row["dielectric_total_trace_mean"], "")

    def test_unfinished_task_blocks_final_csv_and_directory_creation(self):
        """同批只要还有当前结构/批内等待成员，整表不发布且不预先建立输出目录。"""
        from hpc_workflow.postprocess.pipeline import run_post
        completed = self.make_task("MP", "mp-401")
        pending = self.make_task("MJM", "41")
        self.make_products(completed)
        self.make_plan([(completed, "success", ""), (pending, "", "")])
        output = join_local(self.base, "not-created", "results.csv")
        report = run_post({**self.config, "output_csv": output})
        self.assertEqual(report["status"], "incomplete", report)
        self.assertFalse(os.path.exists(os.path.dirname(output)))
        self.assertFalse(os.path.exists(output))

    def test_bad_static_preserves_task_and_other_valid_values(self):
        """已结束成功任务中的损坏 JSON 记为后处理失败，保留有效带隙、电子项和 BEC。"""
        from hpc_workflow.postprocess.pipeline import run_post
        task = self.make_task("MJM", "51")
        self.make_products(task)
        atomic_write_text(join_local(task, "zstar", "dielectric_response", "static_response.json"),
                          '{"tensor": [broken}\n')
        self.make_plan([(task, "success", "")])
        self.assertEqual(run_post(self.config)["status"], "success")
        row = self.read_rows()[0]
        self.assertIn("后处理失败", row["remarks"])
        self.assertEqual(row["dielectric_total_trace_mean"], "")
        self.assertEqual(row["dielectric_electronic_xx"], "2.0")
        self.assertEqual(row["bec_inequivalent_atom_count"], "1")

    def test_static_parent_regular_file_is_isolated_to_one_result_row(self):
        """静态响应父位置误为普通文件时保留任务行和其他物性，不使整批提取异常退出。"""
        from hpc_workflow.postprocess.pipeline import run_post
        task = self.make_task("MJM", "52")
        self.make_products(task, include_static=False)
        # 这是已有输出损坏，不是新任务；快照和解析都应把它归入该行的后处理失败。
        atomic_write_text(join_local(task, "zstar", "dielectric_response"), "not a directory\n")
        self.make_plan([(task, "success", "")])
        report = run_post(self.config)
        self.assertEqual(report["status"], "success", report)
        self.assertEqual(report["counts"].get("post_failed"), 1)
        rows = self.read_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIn("后处理失败", row["remarks"])
        self.assertEqual(row["band_gap_ev"], "1.2")
        self.assertEqual(row["dielectric_electronic_xx"], "2.0")
        self.assertEqual(row["bec_inequivalent_atom_count"], "1")
        self.assertEqual(row["dielectric_total_trace_mean"], "")

    def test_recorded_success_cannot_hide_current_insulation_rejection(self):
        """历史成功记录与当前明确绝缘拒绝冲突时，不能输出一行无备注的成功结果。"""
        from hpc_workflow.postprocess.pipeline import run_post
        task = self.make_task("MP", "mp-gate-conflict")
        self.make_products(task, insulating=False)
        self.make_plan([(task, "success", "")])
        self.assertEqual(run_post(self.config)["status"], "success")
        self.assertIn("绝缘", self.read_rows()[0]["remarks"])

    def test_frozen_scientific_commands_are_used_for_provenance(self):
        """计算计划冻结的科学命令是输入关联依据，不读取当前可修改的 zstar 配置模板。"""
        from hpc_workflow.postprocess.pipeline import run_post
        task = self.make_task("MP", "mp-frozen")
        self.make_products(task)
        frozen = {**self.execution, "kspacing": 0.2,
                  "zstar_commands": {"pre": ["zstar", "bec", "pre", "--force", "--kspacing", "0.2"],
                                     "run": ["zstar", "bec", "run"], "post": ["zstar", "bec", "post"],
                                     "static": ["zstar", "dielectric", "static"]}}
        self.make_plan([(task, "success", "")], frozen)
        # 不允许绕道加载集群模板或调用队列查询来确定后处理身份。
        with patch("runpy.run_path", side_effect=AssertionError("post must not execute another config")), \
                patch("hpc_workflow.submission.slurm.query_jobs", side_effect=AssertionError("post must not query scheduler")):
            report = run_post(self.config)
        self.assertEqual(report["status"], "success", report)
        row = self.read_rows()[0]
        self.assertEqual(row["remarks"], "")
        self.assertEqual(row["provenance_status"], "revision_linked")
        self.assertEqual(len(row["zstar_input_revision_id"]), 36)

    def test_latest_unfinished_attempt_does_not_reuse_old_success(self):
        """同一任务前轮成功、后轮尚未结束时，不用旧终态伪造本批最终表。"""
        from hpc_workflow.postprocess.pipeline import run_post
        task = self.make_task("MP", "mp-attempt")
        self.make_products(task)
        self.make_plan([(task, "success", "")])
        self.make_plan([(task, "", "")])
        report = run_post(self.config)
        self.assertEqual(report["status"], "incomplete", report)
        self.assertFalse(os.path.exists(self.output))

    def test_multiple_finished_attempts_produce_one_row(self):
        """同一工作根的续算轮次合并为一个结构行，不把一次提交当成独立数据批次。"""
        from hpc_workflow.postprocess.pipeline import run_post
        task = self.make_task("MP", "mp-retry")
        self.make_products(task, total=75.0)
        self.make_plan([(task, "technical_failed", "SCF 不收敛")])
        self.make_plan([(task, "success", "")])
        self.assertEqual(run_post(self.config)["status"], "success")
        rows = self.read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["remarks"], "")
        self.assertEqual(float(rows[0]["dielectric_total_trace_mean"]), 75.0)

    def test_changed_revision_keeps_identity_without_stale_properties(self):
        """来源显式换版本后不采用旧带隙、介电或 BEC，但仍保留错误任务行。"""
        from hpc_workflow.postprocess.pipeline import run_post
        task = self.make_task("MP", "mp-changed")
        self.make_products(task, total=999.0)
        self.make_plan([(task, "success", "")])
        manifest_path = join_local(task, "task_manifest.json")
        manifest = read_json(manifest_path)
        manifest["input_revision_id"] = str(uuid4())
        atomic_write_json(manifest_path, manifest)
        self.assertEqual(run_post(self.config)["status"], "success")
        row = self.read_rows()[0]
        self.assertEqual(row["source_record_id"], "mp-changed")
        self.assertNotEqual(row["remarks"], "")
        for field in ("band_gap_ev", "dielectric_total_trace_mean", "born_effective_charges_json"):
            self.assertEqual(row[field], "", field)

    def test_validate_only_and_existing_output_leave_inputs_untouched(self):
        """validate 不发布结果；重复导出拒绝覆盖，已生成表格与计算输入字节保持不变。"""
        from hpc_workflow.postprocess.pipeline import run_post
        task = self.make_task("MP", "mp-validate")
        self.make_products(task)
        self.make_plan([(task, "success", "")])
        before = file_bytes(join_local(task, "zstar", "INPUT"))
        report = run_post(self.config, validate_only=True)
        self.assertIn(report["status"], {"valid", "success"}, report)
        self.assertFalse(os.path.exists(self.output))
        run_post(self.config)
        written = file_bytes(self.output)
        try:
            report = run_post(self.config)
        except (WorkflowError, FileExistsError):
            pass
        else:
            self.assertNotEqual(report.get("status"), "success", report)
        self.assertEqual(file_bytes(self.output), written)
        self.assertEqual(file_bytes(join_local(task, "zstar", "INPUT")), before)

    def test_relocated_work_root_requires_no_record_path_rewrite(self):
        """整棵工作根迁移后，任务、计划和证据以相对路径重新定位，不依赖旧机器地址。"""
        from hpc_workflow.postprocess.pipeline import run_post
        task = self.make_task("MP", "mp-moved")
        self.make_products(task)
        self.make_plan([(task, "success", "")])
        moved = join_local(self.base, "cluster batch")
        shutil.copytree(self.work, moved)
        report = run_post({**self.config, "work_root": moved})
        self.assertEqual(report["status"], "success", report)
        row = self.read_rows()[0]
        self.assertEqual(row["zstar_relative_path"], os.path.basename(task) + "/zstar")
        self.assertFalse(os.path.isabs(row["zstar_relative_path"]))

    def test_input_change_during_collection_prevents_publication(self):
        """读取物性后若输入变化，不能发布由两个不同时间版本拼出的整批表格。"""
        from hpc_workflow.postprocess import pipeline
        task = self.make_task("MP", "mp-race")
        self.make_products(task)
        self.make_plan([(task, "success", "")])
        original = pipeline.read_task_data

        def change_after_read(directory: str, config: dict) -> dict:
            """在模拟的并发写入点更改本测试任务输入，返回原读取结果供一致性检查识别。"""
            result = original(directory, config)
            atomic_write_text(join_local(directory, "INPUT"), "concurrent input version\n")
            return result

        try:
            with patch.object(pipeline, "read_task_data", side_effect=change_after_read):
                report = pipeline.run_post(self.config)
        except (WorkflowError, ValueError):
            pass
        else:
            self.assertNotEqual(report.get("status"), "success", report)
        self.assertFalse(os.path.exists(self.output))

    def test_input_change_after_discovery_cannot_become_snapshot_baseline(self):
        """发现阶段验证后、物性快照建立前的输入变化也必须阻止旧结果发布。"""
        from hpc_workflow.postprocess import pipeline
        task = self.make_task("MP", "mp-discovery-race")
        self.make_products(task)
        self.make_plan([(task, "success", "")])
        original = pipeline.discover_tasks
        changed = False

        def change_after_discovery(config: dict) -> list[dict]:
            """第一次发现返回可信旧版本后立即变更输入，模拟真正的校验/快照边界竞争。"""
            nonlocal changed
            tasks = original(config)
            if not changed:
                changed = True
                manifest_path = join_local(task, "task_manifest.json")
                manifest = read_json(manifest_path)
                manifest["input_revision_id"] = str(uuid4())
                atomic_write_json(manifest_path, manifest)
            return tasks

        try:
            with patch.object(pipeline, "discover_tasks", side_effect=change_after_discovery):
                report = pipeline.run_post(self.config)
        except (WorkflowError, ValueError):
            pass
        else:
            self.assertNotEqual(report.get("status"), "success", report)
        self.assertFalse(os.path.exists(self.output))

    def test_real_cli_validate_then_default_collect_from_custom_config(self):
        """真实 CLI 从自定义配置相对路径加载一整批，验证只读、收集混合结果并拒绝覆盖。"""
        task = self.make_task("MP", "mp-cli")
        rejected = self.make_task("MJM", "71")
        self.make_products(task, total=35.0)
        self.make_products(rejected, gap=0.0, include_static=False, insulating=False)
        self.make_plan([(task, "success", ""), (rejected, "physical_rejected", "能带绝缘检查不通过")])
        config_path = join_local(self.base, "post_custom.py")
        # 两个 I/O 根均使用配置文件相对地址；从不同 CWD 执行也不能解释到其他位置。
        atomic_write_text(config_path, "CONFIG = " + repr({"work_root": "one batch", "output_csv": "results.csv"}) + "\n")
        cli = join_local(project_root(), "cli.py")
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
                           TMP=self.base, TEMP=self.base, TMPDIR=self.base)
        validated = subprocess.run([sys.executable, "-B", cli, "post", "validate", "--config", config_path],
                                   cwd=project_root(), env=environment, stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   check=False, timeout=45)
        self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
        self.assertIn("未写出CSV", validated.stdout)
        self.assertFalse(os.path.exists(self.output))
        collected = subprocess.run([sys.executable, "-B", cli, "post", "--config", config_path],
                                   cwd=project_root(), env=environment, stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   check=False, timeout=45)
        self.assertEqual(collected.returncode, 0, collected.stdout + collected.stderr)
        self.assertIn("写出行数：2", collected.stdout)
        self.assertIn("成功=1", collected.stdout)
        self.assertIn("物理终止=1", collected.stdout)
        self.assertEqual(len(self.read_rows()), 2)
        original = file_bytes(self.output)
        repeated = subprocess.run([sys.executable, "-B", cli, "post", "collect", "--config", config_path],
                                  cwd=project_root(), env=environment, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, encoding="utf-8", errors="replace",
                                  check=False, timeout=45)
        self.assertEqual(repeated.returncode, 1, repeated.stdout + repeated.stderr)
        self.assertIn("拒绝覆盖", repeated.stderr)
        self.assertEqual(file_bytes(self.output), original)


if __name__ == "__main__":
    unittest.main()
