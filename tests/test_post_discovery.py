"""后处理的新版身份、冻结计划、续算优先级和只读批次边界回归。"""

import os
import shutil
import tempfile
import unittest
from test_post_pipeline import file_bytes, make_revision
from uuid import uuid4
from types import SimpleNamespace

from hpc_workflow.common.atomic import atomic_write_csv, atomic_write_json, read_json
from hpc_workflow.common.errors import IntegrityError
from hpc_workflow.common.paths import join_local, project_root, to_relative
from hpc_workflow.postprocess.discovery import discover_tasks
from hpc_workflow.postprocess.metadata import resolve_metadata
from hpc_workflow.preprocess.planner import task_id_for
from hpc_workflow.submission.models import JOB_FIELDS


class PostDiscoveryTests(unittest.TestCase):
    """用已封存的小文件替代数值计算，断言只读关联不写状态或接管旧散装产物。"""

    def setUp(self):
        """为每项测试创建项目内独占工作根，不向旧工作流或外部缓存写入。"""
        directory = join_local(project_root(), ".test-artifacts")
        os.makedirs(directory, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(self.temporary.cleanup)
        self.work = join_local(self.temporary.name, "work")
        os.mkdir(self.work)
        self.execution = {"zstar_subdir": "zstar", "relax_subdir": "cell-relax",
                          "xc": "pbesol", "kspacing": 0.1, "zstar_commands": None}

    def make_task(self, source="MJM", identity="1", namespace="batch", nominal=None):
        """建立通过 inspect_existing 的新版封存任务；资源内容仅作为字符串，不被访问。"""
        name = task_id_for(SimpleNamespace(input_source=source, source_record_id=identity), namespace)
        task = join_local(self.work, name)
        os.mkdir(task)
        for filename, text in (("STRU", "remote resource paths are opaque\n"), ("INPUT", "INPUT_PARAMETERS\n"),
                               ("primitive.cif", "data_test\n")):
            with open(join_local(task, filename), "w", encoding="utf-8") as handle:
                handle.write(text)
        components = {"identity": {"source": source, "namespace": namespace, "id": identity}}
        manifest = {"schema_version": 2, "workflow_version": "0.0.2", "status": "success", "task_id": name,
                    "input_source": source, "dataset_namespace": namespace, "source_record_id": identity,
                    "formula": "Si", "elements": ["Si"], "global_sample_id": identity if source == "MJM" else "999",
                    "dft_priority": int(identity) + 1 if source == "MJM" else None,
                    "source_metadata": nominal or {}, "input_description": components,
                    "input_revision_id": str(uuid4()), "source_file": "source.cif",
                    "products": ["STRU", "INPUT", "primitive.cif"]}
        atomic_write_json(join_local(task, "task_manifest.json"), manifest)
        os.mkdir(join_local(task, "zstar"))
        for filename in ("STRU", "INPUT"):
            shutil.copyfile(join_local(task, filename), join_local(task, "zstar", filename))
        return task

    def plan(self, task, run, outcome="success", finished=True, status="planned", execution=None, pointer=None):
        """写一轮固定计划和可选 worker 终态，返回计划绝对路径。"""
        execution = execution or self.execution
        name = os.path.basename(task)
        directory = join_local(self.work, ".hpc_runs", "zstar", run)
        os.makedirs(directory)
        revision = make_revision(task, execution, "zstar")
        item = {"path": name, "status": status, **revision}
        if pointer:
            item.update(plan=to_relative(pointer, self.work), input_revision_id="")
        groups = [{"batch": 1, "tasks": [name]}] if status == "planned" else []
        value = {"schema_version": 2, "workflow_version": "0.0.2", "stage": "zstar", "task_root": "../../..", "execution": execution,
                 "tasks": [item], "groups": groups, "parallel_jobs": 1}
        path = join_local(directory, "plan.json")
        atomic_write_json(path, value)
        if finished and groups:
            progress = {"schema_version": 2, "workflow_version": "0.0.2", "batch": 1, "status": "finished", "current_task": None,
                        "finished": [{"path": name, "status": outcome, **revision,
                                      "reason": "SCF 不收敛" if outcome == "technical_failed" else ""}]}
            atomic_write_json(join_local(directory, "batches", "0001", "progress.json"), progress)
        return path

    def collect(self):
        """使用只包含后处理定位设置的配置调用只读发现，不提供当前 zstar 科学参数。"""
        return discover_tasks({"work_root": self.work, "zstar_subdir": "zstar"})

    def test_mp_global_sample_always_empty_and_nominal_unknown(self):
        """MP 即使封存了意外样本号，输出也为空；不把未知 nominal 冒充 False。"""
        task = self.make_task(source="MP", identity="mp-1")
        value = resolve_metadata(task)
        self.assertEqual(value["global_sample_id"], "")
        self.assertEqual(value["dft_priority"], "")
        self.assertEqual(value["nominal_k_available"], "")
        self.assertEqual(value["source_record_id"], "mp-1")

    def test_mjm_source_metadata_keeps_false_or_true_nominal(self):
        """MJM 直接使用已封存源行，不依赖旧优先级表。"""
        first = self.make_task(nominal={"nominal_k_available": "False", "nominal_k_value": ""})
        second = self.make_task(identity="2", nominal={"nominal_k_available": "True", "nominal_k_value": "2.5D+1"})
        self.assertEqual(resolve_metadata(first)["nominal_k_available"], "False")
        self.assertEqual(resolve_metadata(second)["nominal_k_value"], 25.0)

    def test_nominal_inconsistent_false_is_rejected(self):
        """明确 False 与非空数值不可同时存在，避免错误物性混入 CSV。"""
        task = self.make_task(nominal={"nominal_k_available": "False", "nominal_k_value": "3"})
        with self.assertRaisesRegex(IntegrityError, "False"):
            resolve_metadata(task)

    def test_nominal_rejects_underscores_and_nonfinite_tokens(self):
        """nominal 数值沿用旧表严格词法，不接受 Python float 的下划线扩展或 NaN/Inf。"""
        for index, token in enumerate(("1_000", "NaN", "Infinity", "1e9999"), start=1):
            with self.subTest(token=token):
                task = self.make_task(identity=str(index), nominal={"nominal_k_available": "True",
                                                                   "nominal_k_value": token})
                with self.assertRaisesRegex(IntegrityError, "nominal_k_value"):
                    resolve_metadata(task)

    def test_no_calculation_plan_is_unfinished_even_with_result_directory(self):
        """有目录或旧产物本身不构成新版已结束证据。"""
        self.make_task()
        result = self.collect()[0]
        self.assertFalse(result["terminal"])
        self.assertEqual(result["provenance_status"], "unavailable")

    def test_frozen_commands_and_xc_used_instead_of_current_template(self):
        """版本关联使用实际轮次冻结的自定义科学命令，而不是外部默认配置。"""
        task = self.make_task()
        execution = {**self.execution, "xc": "lda", "zstar_commands": {"pre": ["custom", "--science", "old"]}}
        self.plan(task, "001", execution=execution)
        result = self.collect()[0]
        self.assertTrue(result["terminal"])
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["provenance_status"], "revision_linked")
        self.assertEqual(result["execution"]["xc"], "lda")

    def test_latest_unfinished_attempt_does_not_resurrect_old_success(self):
        """旧成功后新建续算计划尚未完成时，整批最终导出必须等待新轮次。"""
        task = self.make_task()
        self.plan(task, "001")
        self.plan(task, "002", finished=False)
        result = self.collect()[0]
        self.assertFalse(result["terminal"])

    def test_same_second_random_name_does_not_reverse_attempt_order(self):
        """同秒运行目录的 UUID 字典序可能倒置，后处理必须选实际较新的计划。"""
        task = self.make_task()
        first = self.plan(task, "same-second-zzzz")
        second = self.plan(task, "same-second-aaaa", finished=False)
        for index, path in enumerate((first, second)):
            value = read_json(path)
            value["created_at"] = "2026-10-24T12:00:00+00:00"
            atomic_write_json(path, value)
            os.utime(path, ns=(1_800_000_000_000_000_000 + index * 1_000_000,
                               1_800_000_000_000_000_000 + index * 1_000_000))
        self.assertFalse(self.collect()[0]["terminal"])

    def test_latest_active_snapshot_follows_completed_owner_progress(self):
        """较新计划的 active 只是观察快照，真正执行计划结束后不能永远卡在 active。"""
        task = self.make_task()
        old = self.plan(task, "001")
        self.plan(task, "002", status="active", pointer=old)
        result = self.collect()[0]
        self.assertTrue(result["terminal"])
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["provenance_status"], "revision_linked")

    def test_finished_result_with_changed_source_revision_becomes_failure(self):
        """新来源版本不能借用旧终态，保留失败行同时禁止读取旧物性。"""
        task = self.make_task()
        self.plan(task, "001")
        path = join_local(task, "task_manifest.json")
        manifest = read_json(path)
        manifest["input_revision_id"] = str(uuid4())
        atomic_write_json(path, manifest)
        result = self.collect()[0]
        self.assertTrue(result["terminal"])
        self.assertEqual(result["outcome"], "input_failed")
        self.assertEqual(result["provenance_status"], "revision_mismatch")

    def test_corrupt_pre_metadata_terminal_keeps_failure_row(self):
        """前处理必需文件缺失保留可定位终态失败，不丢失该任务。"""
        task = self.make_task()
        self.plan(task, "001")
        os.remove(join_local(task, "primitive.cif"))
        result = self.collect()[0]
        self.assertTrue(result["terminal"])
        self.assertEqual(result["outcome"], "input_failed")
        self.assertEqual(result["provenance_status"], "invalid_record")
        self.assertTrue(result["metadata_errors"])

    def test_plan_missing_task_is_global_batch_integrity_error(self):
        """已计划任务被移出工作根时，不能输出悄悄少一行的所谓整批最终 CSV。"""
        task = self.make_task()
        self.plan(task, "001")
        os.rename(join_local(task, "task_manifest.json"), join_local(task, "old_manifest.json"))
        with self.assertRaisesRegex(IntegrityError, "缺失"):
            self.collect()

    def test_progress_from_other_input_cannot_supply_terminal_result(self):
        """进度中的完成项必须与其所属计划的版本一一对应。"""
        task = self.make_task()
        path = self.plan(task, "001")
        progress_path = join_local(os.path.dirname(path), "batches", "0001", "progress.json")
        progress = read_json(progress_path)
        progress["finished"][0]["input_revision_id"] = str(uuid4())
        atomic_write_json(progress_path, progress)
        with self.assertRaisesRegex(IntegrityError, "版本"):
            self.collect()

    def test_input_preparation_failure_can_end_without_zstar_stru(self):
        """未能产生可靠输入的任务也有一行终态失败，但没有可读物性或虚构版本。"""
        task = self.make_task()
        path = self.plan(task, "001", status="input_failed")
        plan = read_json(path)
        plan["tasks"][0].update(input_revision_id="", reason="relax 结构映射失败")
        atomic_write_json(path, plan)
        os.remove(join_local(task, "zstar", "STRU"))
        result = self.collect()[0]
        self.assertTrue(result["terminal"])
        self.assertEqual(result["outcome"], "input_failed")
        self.assertEqual(result["provenance_status"], "unavailable")

    def test_distinct_namespace_allows_same_mjm_number(self):
        """独立数据集命名空间允许各自包含相同的样本号与 priority。"""
        self.make_task(namespace="set-a")
        self.make_task(namespace="set-b")
        self.assertEqual(len(self.collect()), 2)

    def test_duplicate_priority_in_same_dataset_is_global_error(self):
        """即使前处理记录自洽，同数据集两条样本复用 priority 仍属于表级关联错误。"""
        self.make_task(identity="1")
        second = self.make_task(identity="2")
        path = join_local(second, "task_manifest.json")
        manifest = read_json(path)
        manifest["dft_priority"] = 2
        atomic_write_json(path, manifest)
        with self.assertRaisesRegex(IntegrityError, "重复"):
            self.collect()

    def test_failed_sbatch_is_terminal_without_queue_query(self):
        """提交明确失败是当前轮的技术终态；不必也不应该靠队列探测猜测。"""
        task = self.make_task()
        plan = self.plan(task, "001", finished=False)
        row = {key: "" for key in JOB_FIELDS}
        row.update(batch="1", submit_status="FAILED", reason="invalid partition")
        atomic_write_csv(join_local(os.path.dirname(plan), "jobs.csv"), JOB_FIELDS, [row])
        result = self.collect()[0]
        self.assertTrue(result["terminal"])
        self.assertEqual(result["outcome"], "technical_failed")
        self.assertIn("invalid partition", result["reason"])

    def test_relax_failure_preserved_when_zstar_never_planned(self):
        """上游 cell-relax 失败仍应占整批一行，而不是因为没有 zstar 目录就丢弃。"""
        task = self.make_task()
        name = os.path.basename(task)
        path = join_local(self.work, ".hpc_runs", "relax", "001", "plan.json")
        plan = {"schema_version": 2, "workflow_version": "0.0.2", "stage": "relax", "task_root": "../../..", "execution": self.execution,
                "tasks": [{"path": name, "status": "input_failed", "input_revision_id": "",
                           "reason": "relax 结构读取失败"}], "groups": [], "parallel_jobs": 0}
        atomic_write_json(path, plan)
        result = self.collect()[0]
        self.assertTrue(result["terminal"])
        self.assertEqual(result["outcome"], "input_failed")
        self.assertEqual(result["expected_input_revision_id"], "")
        self.assertEqual(result["provenance_status"], "unavailable")

    def test_relax_failed_revision_change_replaces_old_scf_reason(self):
        """上游失败后显式换来源版本，不再沿用旧 SCF 根因。"""
        task = self.make_task()
        name = os.path.basename(task)
        relax_dir = join_local(task, "cell-relax")
        os.mkdir(relax_dir)
        for filename in ("STRU", "INPUT"):
            shutil.copyfile(join_local(task, filename), join_local(relax_dir, filename))
        revision = make_revision(task, self.execution, "relax")
        path = join_local(self.work, ".hpc_runs", "relax", "001", "plan.json")
        value = {"schema_version": 2, "workflow_version": "0.0.2", "stage": "relax", "task_root": "../../..", "execution": self.execution,
                 "tasks": [{"path": name, "status": "technical_failed", **revision,
                            "reason": "SCF 不收敛"}], "groups": [], "parallel_jobs": 0}
        atomic_write_json(path, value)
        manifest_path = join_local(task, "task_manifest.json")
        manifest = read_json(manifest_path)
        manifest["input_revision_id"] = str(uuid4())
        atomic_write_json(manifest_path, manifest)
        result = self.collect()[0]
        self.assertTrue(result["terminal"])
        self.assertEqual(result["outcome"], "input_failed")
        self.assertIn("relax", result["reason"])
        self.assertIn("版本", result["reason"])
        self.assertNotIn("SCF 不收敛", result["reason"])
        self.assertEqual(result["provenance_status"], "unavailable")
        self.assertTrue(all(join_local(relax_dir, filename) in result["record_paths"] for filename in ("STRU", "INPUT")))

    def test_relax_failure_uses_frozen_layout_and_remains_zstar_unavailable(self):
        """冻结的非默认弛豫子目录不受当前模板影响，匹配也不能冒充 zstar 已绑定。"""
        task = self.make_task()
        name = os.path.basename(task)
        execution = {**self.execution, "relax_subdir": "frozen-relax"}
        relax_dir = join_local(task, "frozen-relax")
        os.mkdir(relax_dir)
        for filename in ("STRU", "INPUT"):
            shutil.copyfile(join_local(task, filename), join_local(relax_dir, filename))
        revision = make_revision(task, execution, "relax")
        # 故意准备不同的默认目录：版本关联必须仍使用冻结阶段目录。
        os.mkdir(join_local(task, "cell-relax"))
        with open(join_local(task, "cell-relax", "STRU"), "w", encoding="utf-8") as handle:
            handle.write("different current template input\n")
        path = join_local(self.work, ".hpc_runs", "relax", "001", "plan.json")
        value = {"schema_version": 2, "workflow_version": "0.0.2", "stage": "relax", "task_root": "../../..", "execution": execution,
                 "tasks": [{"path": name, "status": "technical_failed", **revision,
                            "reason": "SCF 不收敛"}], "groups": [], "parallel_jobs": 0}
        atomic_write_json(path, value)
        result = self.collect()[0]
        self.assertTrue(result["terminal"])
        self.assertEqual(result["outcome"], "technical_failed")
        self.assertEqual(result["reason"], "SCF 不收敛")
        self.assertEqual(result["provenance_status"], "unavailable")
        self.assertEqual(result["expected_input_revision_id"], "")
        self.assertTrue(all(join_local(relax_dir, filename) in result["record_paths"] for filename in ("STRU", "INPUT")))

    def test_migrated_root_resolves_everything_from_relative_records(self):
        """工作树迁移后旧输入已改变，仍只核验新根下的相对记录与输入。"""
        task = self.make_task()
        self.plan(task, "001")
        moved = join_local(self.temporary.name, "cluster-work")
        shutil.copytree(self.work, moved)
        # 原根声明新版本；错误回退原机器路径将无法关联迁移后的旧版本记录。
        path = join_local(task, "task_manifest.json")
        manifest = read_json(path)
        manifest["input_revision_id"] = str(uuid4())
        atomic_write_json(path, manifest)
        self.work = moved
        result = self.collect()[0]
        self.assertTrue(result["terminal"])
        self.assertEqual(result["provenance_status"], "revision_linked")
        self.assertTrue(result["zstar_dir"].startswith(moved))

    def test_old_scattered_directory_is_not_imported(self):
        """旧目录只有 STRU 不属于新版批次，不接管、不生成任何记录。"""
        task = join_local(self.work, "old-scattered")
        os.mkdir(task)
        with open(join_local(task, "STRU"), "w", encoding="utf-8") as handle:
            handle.write("not a managed task")
        self.assertEqual(self.collect(), [])

    def test_discovery_is_read_only(self):
        """发现前后逐文件摘要一致，查询不会创建 .zstar 状态、日志或队列快照。"""
        task = self.make_task()
        self.plan(task, "001", outcome="physical_rejected")
        before = {os.path.relpath(os.path.join(parent, name), self.work): file_bytes(os.path.join(parent, name))
                  for parent, _, names in os.walk(self.work) for name in names}
        result = self.collect()
        after = {os.path.relpath(os.path.join(parent, name), self.work): file_bytes(os.path.join(parent, name))
                 for parent, _, names in os.walk(self.work) for name in names}
        self.assertEqual(before, after)
        self.assertEqual(result[0]["outcome"], "physical_rejected")

    def test_legacy_protocol_does_not_supply_current_terminal_state(self):
        """旧协议只读可见，但不自动转成当前版本的终态或重标其UUID。"""
        task = self.make_task()
        path = self.plan(task, "legacy")
        value = read_json(path)
        value["schema_version"] = 1
        value.pop("workflow_version")
        atomic_write_json(path, value)
        before = file_bytes(path)
        result = self.collect()[0]
        self.assertFalse(result["terminal"])
        self.assertEqual(file_bytes(path), before)

    def test_declared_revision_does_not_claim_content_integrity(self):
        """只改字节而不声明新版本不会被UUID识别；此测试固定公开的保证边界。"""
        task = self.make_task()
        self.plan(task, "001")
        path = join_local(task, "zstar", "INPUT")
        before = os.stat(path)
        text = file_bytes(path)
        with open(path, "wb") as handle:
            handle.write(b"X" * len(text))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        result = self.collect()[0]
        self.assertEqual(result["provenance_status"], "revision_linked")

    def test_missing_stage_input_does_not_borrow_finished_properties(self):
        """UUID不能掩盖必要阶段输入缺失，普通stat检查仍阻止读取残留物性。"""
        task = self.make_task()
        self.plan(task, "001")
        os.remove(join_local(task, "zstar", "STRU"))
        result = self.collect()[0]
        self.assertEqual(result["outcome"], "input_failed")
        self.assertEqual(result["provenance_status"], "revision_mismatch")
