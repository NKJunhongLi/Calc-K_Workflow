"""计算入口、只读状态及配置约束的离线回归；不调用 Slurm/ABACUS/zstar。"""

from contextlib import ExitStack, redirect_stdout
import io
import os
import tempfile
import unittest
from unittest.mock import patch

import cli
from hpc_workflow.common.atomic import atomic_write_json, read_json
from hpc_workflow.common.errors import ConfigError
from hpc_workflow.common.paths import join_local, project_root
from hpc_workflow.submission.config import load_config
from hpc_workflow.submission.status import collect_status, format_status
from hpc_workflow.records.manifest import new_revision_id
from hpc_workflow.calculations.relax import scientific_input, REVISION_FIELDS


class RunStatusConfigTests(unittest.TestCase):
    """所有临时计划位于新项目测试目录，队列和科学计算使用明确替身。"""

    def setUp(self):
        """建立本测试独享根，并自动清理本次测试产生的内容。"""
        base = join_local(project_root(), ".test-artifacts")
        os.makedirs(base, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temporary.cleanup)
        self.root = self.temporary.name
        self.config = load_config("zstar")
        self.config["work_root"] = self.root

    def make_plan(self, name="20261024-01", rows=None, stage="zstar"):
        """生成指定阶段的三任务同批计划和可选台账，返回计划地址。"""
        from hpc_workflow.submission.state import write_jobs
        # 状态夹具复用本机配置，仅按阶段提取声明；不依赖用户 relax 的集群绝对路径。
        config = self.config
        path = join_local(self.root, ".hpc_runs", stage, name, "plan.json")
        plan = {"schema_version": 2, "workflow_version": "0.0.2", "stage": stage, "task_root": "../../..", "run_id": name,
                "created_at": "2026-10-24T00:00:00+00:00", "parallel_jobs": 1, "execution": {},
                "tasks": [{"path": key, "status": "planned", "input_revision_id": new_revision_id(), "source_input_revision_id": new_revision_id(),
                           "source_relax_revision_id": new_revision_id(), "calculation_subdir": config[stage + "_subdir"],
                           "scientific_input": scientific_input(config, stage)} for key in ("a", "b", "c")],
                "groups": [{"batch": 1, "tasks": ["a", "b", "c"]}]}
        atomic_write_json(path, plan)
        if rows:
            write_jobs(path, rows)
        return path

    def write_progress(self, path, batch, value):
        """让测试事件与该计划版本关联，保持状态测试聚焦于调度语义。"""
        from hpc_workflow.submission.state import load_plan, write_progress
        entries = {entry["path"]: entry for entry in load_plan(path)["tasks"]}
        value.update(schema_version=2, workflow_version="0.0.2")
        value["finished"] = [{**{key: entries[event["path"]][key] for key in REVISION_FIELDS if key in entries[event["path"]]}, **event}
                             for event in value.get("finished", [])]
        write_progress(path, batch, value)

    def job(self, job_id="123", status="SUBMITTED"):
        """返回一条最小合法提交台账行。"""
        return {"batch": "1", "job_id": job_id, "submit_status": status, "submitted_at": "2026-10-24T00:00:01Z",
                "stdout": "batches/0001/slurm-123.out", "stderr": "batches/0001/slurm-123.err", "reason": ""}

    def test_positional_run_commands_and_default(self):
        """功能采用位置参数，默认执行扫描计划提交；帮助不依赖科学库。"""
        args = cli.build_parser().parse_args(["run", "relax"])
        self.assertEqual((args.stage, args.action), ("relax", "run"))
        args = cli.build_parser().parse_args(["run", "zstar", "status"])
        self.assertEqual(args.action, "status")
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["help", "run"]), 0)
        self.assertIn("relax", output.getvalue())
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["help", "run", "zstar"]), 0)
        self.assertIn("--plan", output.getvalue())

    def test_default_config_and_overlay_path_anchor(self):
        """自定义配置未覆盖的默认脚本仍按默认模板定位。"""
        path = join_local(self.root, "config.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("CONFIG = {'work_root': 'new_work', 'parallel_jobs': 0}\n")
        loaded = load_config("relax", path)
        self.assertEqual(loaded["work_root"], join_local(self.root, "new_work"))
        self.assertEqual(loaded["slurm_script"], join_local(project_root(), "slurm", "run_cell-relax.slurm"))

    def test_configuration_rejects_unsafe_and_nonfinite_values(self):
        """尽早拒绝拼错键、越界目录、非整数作业数和非有限数。"""
        path = join_local(self.root, "bad.py")
        for body in ("{'parallel_jobs': True}", "{'parallel_jobs': -1}", "{'symprec': .01}",
                     "{'minimum_distance': float('nan')}", "{'relax_subdir': '../x'}",
                     "{'zstar_subdir': 'cell-relax'}", "{'abacus_command': ['abacus', '{ntask}']}",
                     "{'unknown': 3}"):
            with self.subTest(body=body):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("CONFIG = " + body)
                with self.assertRaises(ConfigError):
                    load_config("zstar", path)

    def test_missing_state_status_does_not_create_directories(self):
        """无计划时也保持纯只读，不创建 work 或 .hpc_runs。"""
        absent = join_local(self.root, "not_created")
        self.config["work_root"] = absent
        with patch("hpc_workflow.submission.slurm.query_jobs") as query:
            report = collect_status("zstar", self.config)
        query.assert_not_called()
        self.assertEqual(report["total"], 0)
        self.assertFalse(os.path.exists(absent))

    def test_running_job_only_current_structure_is_running(self):
        """同批一个完成、一个计算、一个等待；父作业状态不能平铺到所有结构。"""
        path = self.make_plan(rows=[self.job()])
        self.write_progress(path, 1, {"batch": 1, "current_task": "b", "current_command": "run",
                                "status": "running", "finished": [{"path": "a", "status": "success", "reason": ""}]})
        with patch("hpc_workflow.submission.slurm.query_jobs", return_value={"123": {"state": "RUNNING"}}), patch("hpc_workflow.calculations.zstar.inspect_zstar", side_effect=AssertionError("status 不得复验科学产物")):
            report = collect_status("zstar", self.config)
        states = {item["path"]: item["status"] for item in report["tasks"]}
        self.assertEqual(states, {"a": "success", "b": "running", "c": "waiting_in_batch"})
        self.assertEqual(report["counts"]["running"], 1)
        for name in ("a", "b", "c"):
            self.assertIn(join_local(self.root, name), format_status(report))

    def test_submission_failure_is_failure_and_not_waiting(self):
        """sbatch 从未受理时按提交失败列出，不能误显示为批内等待。"""
        self.make_plan(rows=[self.job("", "FAILED")])
        report = collect_status("zstar", self.config)
        self.assertEqual(report["counts"], {"technical_failed": 3})

    def test_parent_oom_only_marks_interrupted_current_structure(self):
        """批次 OOM 不能改变先前完成或尚未执行结构的状态。"""
        path = self.make_plan(rows=[self.job()])
        self.write_progress(path, 1, {"batch": 1, "current_task": "b", "current_command": "run", "finished": []})
        with patch("hpc_workflow.submission.slurm.query_jobs", return_value={"123": {"state": "OUT_OF_MEMORY"}}):
            report = collect_status("zstar", self.config)
        by_path = {item["path"]: item for item in report["tasks"]}
        self.assertEqual(by_path["b"]["reason"], "内存超限")
        self.assertEqual(by_path["a"]["status"], "waiting_in_batch")
        self.assertEqual(by_path["c"]["status"], "waiting_in_batch")

    def test_unknown_scheduler_state_never_claims_running(self):
        """队列查询失败仍保留待核实状态，既不猜完成也不猜运行。"""
        self.make_plan(rows=[self.job()])
        with patch("hpc_workflow.submission.slurm.query_jobs", return_value={"123": {"state": "UNKNOWN"}}):
            report = collect_status("zstar", self.config)
        self.assertEqual(report["counts"], {"unknown": 3})

    def test_all_nonterminal_statuses_are_protected_from_duplicate_submission(self):
        """显示层的排队/活动状态必须都被发现层识别，含重排队暂挂状态。"""
        from hpc_workflow.submission.models import ACTIVE_SLURM_STATES
        from hpc_workflow.submission.status import ACTIVE_STATES, QUEUED_STATES
        self.assertEqual(ACTIVE_SLURM_STATES, ACTIVE_STATES | QUEUED_STATES)

    def test_status_read_only_snapshot(self):
        """已有计划查询前后文件名和内容完全一致。"""
        path = self.make_plan()
        before = self.snapshot()
        report = collect_status("zstar", self.config, path)
        after = self.snapshot()
        self.assertEqual(before, after)
        self.assertEqual(report["counts"], {"planned": 3})

    def snapshot(self):
        """读取本测试全部文件字节并及时关闭句柄，返回相对名到内容的字典。"""
        result = {}
        for parent, _, files in os.walk(self.root):
            for name in files:
                path = os.path.join(parent, name)
                with open(path, "rb") as handle:
                    result[os.path.relpath(path, self.root)] = handle.read()
        return result

    def test_recorded_imaginary_stop_needs_input_binding_not_native_copy(self):
        """static 虚频只在 Slurm stdout 留痕时，已记录拒绝仍保持独立终态。"""
        path = self.make_plan(rows=[self.job()])
        self.write_progress(path, 1, {"batch": 1, "current_task": None, "current_command": None,
                                "finished": [{"path": "a", "status": "physical_rejected", "reason": "Gamma 点存在明显虚频"}]})
        with patch("hpc_workflow.submission.slurm.query_jobs", return_value={"123": {"state": "COMPLETED"}}), patch("hpc_workflow.calculations.relax.revision_matches", side_effect=AssertionError("status 不得回读输入关联")):
            report = collect_status("zstar", self.config)
        self.assertEqual(report["tasks"][0]["status"], "physical_rejected")

    def test_recorded_terminals_do_not_read_scientific_files(self):
        """匹配记录直接展示完成/物理拒绝；任务目录缺失也不得触发科学或输入复验。"""
        forbidden = ("hpc_workflow.calculations.relax.inspect_relax",
                     "hpc_workflow.calculations.zstar.inspect_zstar",
                     "hpc_workflow.calculations.relax.revision_matches",
                     "hpc_workflow.calculations.relax.build_relax_revision_index")
        for stage, outcome in (("relax", "success"), ("zstar", "success"), ("zstar", "physical_rejected")):
            with self.subTest(stage=stage, outcome=outcome), ExitStack() as stack:
                config = {**self.config, "stage": stage}
                path = self.make_plan(name=f"recorded-{stage}-{outcome}", rows=[self.job()], stage=stage)
                reason = "Gamma 点存在明显虚频" if outcome == "physical_rejected" else ""
                self.write_progress(path, 1, {"batch": 1, "finished": [{"path": "a", "status": outcome, "reason": reason}]})
                stack.enter_context(patch("hpc_workflow.submission.slurm.query_jobs", return_value={"123": {"state": "COMPLETED"}}))
                for target in forbidden:
                    stack.enter_context(patch(target, side_effect=AssertionError(f"status 不得调用 {target}")))
                before = self.snapshot()
                report = collect_status(stage, config, path)
                item = next(item for item in report["tasks"] if item["path"] == "a")
                self.assertEqual((item["status"], item["reason"]), (outcome, reason))
                self.assertFalse(os.path.exists(join_local(self.root, "a")))
                self.assertEqual(before, self.snapshot())

    def test_finished_revision_and_legacy_rules_remain_record_checks(self):
        """五字段不匹配或旧进度不能借用成功；旧计划成功仍显示待核实。"""
        from hpc_workflow.submission.state import read_progress, write_progress
        for variant in (*REVISION_FIELDS, "legacy_progress", "legacy_plan"):
            with self.subTest(variant=variant):
                path = self.make_plan(name=f"version-{variant}", rows=[self.job()])
                self.write_progress(path, 1, {"batch": 1, "finished": [{"path": "a", "status": "success", "reason": ""}]})
                progress = read_progress(path, 1)
                if variant == "legacy_plan":
                    plan = read_json(path)
                    plan.update(schema_version=1, workflow_version="0.0.1")
                    atomic_write_json(path, plan)
                    progress.update(schema_version=1, workflow_version="0.0.1")
                elif variant == "legacy_progress":
                    progress.update(schema_version=1, workflow_version="0.0.1")
                else:
                    progress["finished"][0][variant] = ({"changed": True} if variant == "scientific_input"
                                                        else "another-stage" if variant == "calculation_subdir"
                                                        else new_revision_id())
                write_progress(path, 1, progress)
                with patch("hpc_workflow.submission.slurm.query_jobs", return_value={"123": {"state": "COMPLETED"}}):
                    report = collect_status("zstar", self.config, path)
                item = next(item for item in report["tasks"] if item["path"] == "a")
                self.assertEqual(item["status"], "unknown" if variant == "legacy_plan" else "waiting_in_batch")
                if variant == "legacy_plan":
                    self.assertIn("旧协议", item["reason"])

    def test_status_reuses_records_without_crossing_plan_or_batch(self):
        """多批次与活动历史引用共享一次读取，索引不混用不同计划/批次的终态。"""
        from hpc_workflow.submission.state import load_plan, read_jobs, read_progress

        class SinglePassFinished(list):
            """仅统计状态汇总对已完成列表的遍历，不计底层读取器的格式校验。"""
            iteration_count = 0

            def __iter__(self):
                """同一批的状态索引只建立一次，重复遍历立即使本回归失败。"""
                self.iteration_count += 1
                if self.iteration_count > 1:
                    raise AssertionError("status 重复遍历同批 finished")
                return super().__iter__()

        old = self.make_plan(rows=[self.job(), {**self.job("124"), "batch": "2"}])
        plan = read_json(old)
        plan.update(parallel_jobs=2, groups=[{"batch": 1, "tasks": ["a", "b"]}, {"batch": 2, "tasks": ["c"]}])
        atomic_write_json(old, plan)
        self.write_progress(old, 1, {"batch": 1, "finished": [{"path": "a", "status": "success", "reason": ""},
                                                            {"path": "b", "status": "technical_failed", "reason": "旧批次失败"}]})
        self.write_progress(old, 2, {"batch": 2, "finished": [{"path": "c", "status": "success", "reason": ""}]})
        new = self.make_plan(name="20261024-02", rows=[self.job("999")])
        plan = read_json(new)
        plan.update(created_at="2026-10-24T00:01:00+00:00", groups=[{"batch": 1, "tasks": ["c"]}])
        for entry in plan["tasks"][:2]:
            entry.update(status="active", input_revision_id="", job_id="123", plan=".hpc_runs/zstar/20261024-01/plan.json")
        atomic_write_json(new, plan)
        self.write_progress(new, 1, {"batch": 1, "finished": [{"path": "c", "status": "technical_failed", "reason": "新批次失败"}]})

        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                finished_lists = []

                def counted_progress(path, batch):
                    """先调用真实只读解析，再为状态层返回可统计遍历次数的列表。"""
                    value = read_progress(path, batch)
                    value["finished"] = SinglePassFinished(value.get("finished", []))
                    finished_lists.append(value["finished"])
                    return value

                with patch("hpc_workflow.submission.state.load_plan", wraps=load_plan) as plans, \
                        patch("hpc_workflow.submission.state.read_jobs", wraps=read_jobs) as jobs, \
                        patch("hpc_workflow.submission.state.read_progress", side_effect=counted_progress) as progress, \
                        patch("hpc_workflow.submission.slurm.query_jobs", return_value={key: {"state": "COMPLETED"} for key in ("123", "124", "999")}):
                    report = collect_status("zstar", self.config, new if explicit else None)
                observed = {item["path"]: (item["status"], item["reason"]) for item in report["tasks"]}
                self.assertEqual(observed, {"a": ("success", ""), "b": ("technical_failed", "旧批次失败"),
                                            "c": ("technical_failed", "新批次失败")})
                for reader in (plans, jobs):
                    self.assertCountEqual([call.args[0] for call in reader.call_args_list], [old, new])
                expected = [(old, 1), (new, 1)] if explicit else [(old, 1), (old, 2), (new, 1)]
                self.assertCountEqual([call.args for call in progress.call_args_list], expected)
                self.assertEqual([value.iteration_count for value in finished_lists], [1] * len(expected))

    def test_stale_native_running_file_not_displayed(self):
        """pre 外层运行时旧 disp 不得冒充当前正在执行的有限差分阶段。"""
        path = self.make_plan(rows=[self.job()])
        native = join_local(self.root, "a", "zstar", ".zstar", "stages", "disp-001.json")
        atomic_write_json(native, {"name": "disp-001", "status": "running", "scf": "running", "path": "Z:/obsolete"})
        self.write_progress(path, 1, {"batch": 1, "current_task": "a", "current_command": "pre", "finished": []})
        with patch("hpc_workflow.submission.slurm.query_jobs", return_value={"123": {"state": "RUNNING"}}):
            report = collect_status("zstar", self.config)
        self.assertEqual(report["tasks"][0]["step"], "pre")

    def test_active_reference_tracks_original_batch_and_native_displacement(self):
        """新轮占用条目仍沿相对引用展示原批次的真实当前结构和 disp。"""
        old = self.make_plan(rows=[self.job()])
        native = join_local(self.root, "b", "zstar", ".zstar", "stages", "disp-003.json")
        atomic_write_json(native, {"name": "disp-003", "status": "running", "scf": "running", "path": "Z:/obsolete/disp-003"})
        self.write_progress(old, 1, {"batch": 1, "current_task": "b", "current_command": "run", "finished": []})
        new = join_local(self.root, ".hpc_runs", "zstar", "20261024-02", "plan.json")
        atomic_write_json(new, {"schema_version": 2, "workflow_version": "0.0.2", "stage": "zstar", "task_root": "../../..", "run_id": "20261024-02",
                               "created_at": "2026-10-24T00:01:00+00:00", "parallel_jobs": 0, "execution": {}, "groups": [],
                               "tasks": [{"path": key, "status": "active", "input_revision_id": "", "job_id": "123", "plan": ".hpc_runs/zstar/20261024-01/plan.json"} for key in ("a", "b", "c")]})
        with patch("hpc_workflow.submission.slurm.query_jobs", return_value={"123": {"state": "RUNNING"}}):
            report = collect_status("zstar", self.config, new)
        self.assertEqual(report["counts"], {"waiting_in_batch": 2, "running": 1})
        self.assertIn("disp-003 → SCF", report["tasks"][1]["step"])

    def test_failure_evidence_and_supplementary_print_current_absolute_paths(self):
        """根因保持一个，后续原因显示为补充，三种相对证据均展开为当前地址。"""
        path = self.make_plan(rows=[self.job()])
        self.write_progress(path, 1, {"batch": 1, "current_task": None, "current_command": None,
                                "finished": [{"path": "a", "status": "technical_failed", "reason": "SCF 不收敛",
                                              "evidence": ["zstar/.zstar/logs/disp-003.log"],
                                              "supplementary": [{"command": "post", "reason": "缺少输入结果", "evidence": [".hpc_runs/zstar/20261024-01/batches/0001/slurm-123.err"]}]}]})
        with patch("hpc_workflow.submission.slurm.query_jobs", return_value={"123": {"state": "COMPLETED"}}):
            report = collect_status("zstar", self.config)
        text = format_status(report)
        self.assertIn("原因：SCF 不收敛", text)
        self.assertIn("后续依据：post：缺少输入结果", text)
        self.assertIn(join_local(self.root, "a", "zstar", ".zstar", "logs", "disp-003.log"), text)
        self.assertIn(join_local(os.path.dirname(path), "batches", "0001", "slurm-123.err"), text)


if __name__ == "__main__":
    unittest.main()
