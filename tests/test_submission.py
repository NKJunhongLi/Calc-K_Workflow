"""提交层隔离测试：不调用真实 Slurm，不启动 ABACUS 或 zstar。"""

from contextlib import redirect_stderr, redirect_stdout
import io
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from hpc_workflow.common.atomic import atomic_write_json
from hpc_workflow.common.errors import ConfigError, ConflictError, IntegrityError
from hpc_workflow.common.paths import join_local, project_root
from hpc_workflow.submission.discovery import active_tasks, task_directories
from hpc_workflow.calculations.relax import new_stage_revision, REVISION_FIELDS
from hpc_workflow.records.manifest import new_revision_id
from hpc_workflow.submission.pipeline import run, submit_plan
from hpc_workflow.submission.planner import balanced_groups, scan_tasks
from hpc_workflow.submission.slurm import query_jobs, submit_batch
from hpc_workflow.submission.state import (batch_directory, load_plan, read_jobs, read_progress,
                                           RuntimeLock, work_root_for_plan, write_jobs)
from hpc_workflow.submission.worker import _stream_command, run_worker


class SubmissionTests(unittest.TestCase):
    """全部测试文件建立在新项目 .test-artifacts 内，测试调度接口使用 mock。"""

    def setUp(self):
        """建立单测专属工作根和最小两结构计划。"""
        artifacts = join_local(project_root(), ".test-artifacts")
        os.makedirs(artifacts, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=artifacts)
        self.addCleanup(self.temporary.cleanup)
        self.root = join_local(self.temporary.name, "work")
        os.makedirs(self.root)
        for name in ("a", "b"):
            os.makedirs(join_local(self.root, name, "cell-relax"))
            with open(join_local(self.root, name, "STRU"), "w", encoding="utf-8") as handle:
                handle.write("fake structure")
            atomic_write_json(join_local(self.root, name, "task_manifest.json"), {
                "schema_version": 2, "workflow_version": "0.0.2", "status": "success", "input_revision_id": new_revision_id()})
        self.plan_path = join_local(self.root, ".hpc_runs", "relax", "test-run", "plan.json")
        self.plan = {"schema_version": 2, "workflow_version": "0.0.2", "stage": "relax", "run_id": "test-run", "task_root": "../../..",
                     "created_at": "2026-10-24T00:00:00+00:00", "parallel_jobs": 1,
                     "execution": {"relax_subdir": "cell-relax"},
                     "tasks": [{"path": name, "status": "planned", **new_stage_revision(join_local(self.root, name), {"work_root": self.root}, "relax")} for name in ("a", "b")],
                     "groups": [{"batch": 1, "tasks": ["a", "b"]}]}
        atomic_write_json(self.plan_path, self.plan)
        self.config = {"work_root": self.root, "parallel_jobs": 1, "slurm_script": join_local(project_root(), "slurm", "run_cell-relax.slurm")}

    def row(self, status="SUBMITTED", job_id="123"):
        """返回固定批次的最小提交台账行。"""
        return {"batch": "1", "job_id": job_id, "submit_status": status, "submitted_at": "now",
                "stdout": "batches/0001/slurm-%j.out", "stderr": "batches/0001/slurm-%j.err", "reason": ""}

    def test_parallel_count_balancing_and_bounds(self):
        """0/1/中间并行数精确分组，超范围和布尔值不静默改写。"""
        names = list("abcdefg")
        self.assertEqual([len(item["tasks"]) for item in balanced_groups(names, 3)], [3, 2, 2])
        self.assertEqual(len(balanced_groups(names, 0)), 7)
        self.assertEqual(balanced_groups(names, 1)[0]["tasks"], names)
        self.assertEqual(balanced_groups([], 0), [])
        for count in (-1, 8, True, 1.5):
            with self.assertRaises(ConfigError):
                balanced_groups(names, count)
        with self.assertRaises(ConfigError):
            balanced_groups([], 1)

    def test_plan_relocates_without_old_absolute_root(self):
        """整工作目录复制到另一位置后，原记录仍附着新的任务根。"""
        copied = join_local(self.temporary.name, "cluster-work")
        shutil.copytree(self.root, copied)
        path = join_local(copied, ".hpc_runs", "relax", "test-run", "plan.json")
        self.assertEqual(work_root_for_plan(path), copied)
        self.assertEqual(load_plan(path)["tasks"][0]["path"], "a")

    def test_plan_rejects_duplicate_group_members(self):
        """分组重复结构不能导致同一输入被执行两次。"""
        self.plan["groups"][0]["tasks"] = ["a", "a", "b"]
        atomic_write_json(self.plan_path, self.plan)
        with self.assertRaises(IntegrityError):
            load_plan(self.plan_path)

    def test_sbatch_argv_preserves_paths_and_parses_cluster_ack(self):
        """argv 不经 shell；sbatch 的 jobid;cluster 回执正确展开日志位置。"""
        response = subprocess.CompletedProcess([], 0, "345;cluster\n", "")
        with patch("hpc_workflow.submission.slurm._scheduler", return_value=response) as command:
            row = submit_batch(self.plan_path, self.plan, 1, self.config)
        self.assertEqual(row["job_id"], "345")
        self.assertEqual(row["submit_status"], "SUBMITTED")
        self.assertIn("slurm-345.err", row["stderr"])
        args = command.call_args.args[0]
        self.assertIn(self.plan_path, args)
        self.assertEqual(args[-2:], ["--batch", "1"])

    def test_unknown_ack_never_blindly_resubmits(self):
        """已提交但回执未知的批次只核对唯一名称，不再次调用 sbatch。"""
        write_jobs(self.plan_path, [self.row("UNKNOWN", "")])
        with patch("hpc_workflow.submission.pipeline.reconcile_submission", return_value=None), \
                patch("hpc_workflow.submission.pipeline.submit_batch") as submit:
            result = submit_plan("relax", self.config, self.plan_path)
        submit.assert_not_called()
        self.assertEqual(result["status"], "partial_failed")
        self.assertEqual(read_jobs(self.plan_path)[0]["submit_status"], "UNKNOWN")

    def test_ack_unknown_can_reconcile_existing_job(self):
        """唯一作业名可确认受理时补全 job ID，仍不重复提交。"""
        write_jobs(self.plan_path, [self.row("SUBMITTING", "")])
        with patch("hpc_workflow.submission.pipeline.reconcile_submission", return_value="777"), \
                patch("hpc_workflow.submission.pipeline.submit_batch") as submit:
            result = submit_plan("relax", self.config, self.plan_path)
        submit.assert_not_called()
        self.assertEqual(result["jobs"][0]["job_id"], "777")

    def test_already_submitted_plan_does_not_submit_again(self):
        """同一计划重复 submit 不产生重复作业。"""
        write_jobs(self.plan_path, [self.row()])
        with patch("hpc_workflow.submission.pipeline.submit_batch") as submit:
            submit_plan("relax", self.config, self.plan_path)
        submit.assert_not_called()

    def test_active_job_only_protects_unfinished_members(self):
        """批内已结束结构可单独续算，尚未执行的成员仍受已有作业保护。"""
        write_jobs(self.plan_path, [self.row()])
        atomic_write_json(join_local(batch_directory(self.plan_path, 1), "progress.json"),
                          {"schema_version": 2, "workflow_version": "0.0.2", "batch": 1, "current_task": "b",
                           "finished": [{**self.plan["tasks"][0], "status": "technical_failed"}]})
        with patch("hpc_workflow.submission.discovery.query_jobs", return_value={"123": {"state": "RUNNING"}}):
            active = active_tasks(self.root, "relax", self.config)
        self.assertNotIn("a", active)
        self.assertIn("b", active)

    def test_scheduler_unknown_is_not_terminal(self):
        """Slurm 暂时不可查询时保持 UNKNOWN，防误重提。"""
        with patch("hpc_workflow.submission.slurm._scheduler", side_effect=FileNotFoundError("no scheduler")):
            self.assertEqual(query_jobs(["123"], self.config)["123"]["state"], "UNKNOWN")

    def test_scan_does_not_call_prepare_or_write(self):
        """scan 使用只读分支，既不准备输入也不发布计划。"""
        with patch("hpc_workflow.submission.pipeline.scan_tasks", return_value=[]) as scan:
            result = run("relax", "scan", self.config)
        scan.assert_called_once_with("relax", self.config, prepare=False)
        self.assertEqual(result["tasks"], [])

    def test_worker_continues_next_structure_and_no_reprepare(self):
        """第一结构技术失败仍执行第二个，且 worker 不再次 prepare。"""
        calls = []

        def execute(task_dir, config, callback):
            """模拟两个任务的不同终态，不启动真实计算。"""
            name = os.path.basename(task_dir)
            calls.append(name)
            callback("relax", ["fake"], task_dir)
            return {"status": "technical_failed" if name == "a" else "success", "reason": "SCF 不收敛" if name == "a" else ""}

        with patch("hpc_workflow.submission.worker.calculations_for", return_value=(None, None, execute)), \
                patch("hpc_workflow.calculations.relax.revision_matches", return_value=True), \
                patch("hpc_workflow.submission.worker._stream_command", return_value=(0, "")), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = run_worker("relax", self.plan_path, 1)
        self.assertEqual(calls, ["a", "b"])
        self.assertEqual(result["status"], "partial_failed")
        progress = read_progress(self.plan_path, 1)
        self.assertIsNone(progress["current_task"])
        self.assertEqual(len(progress["finished"]), 2)

    def test_worker_rejects_queue_time_input_mutation(self):
        """排队后输入版本变化时拒绝执行，但仍逐任务写出明确输入失败。"""
        with patch("hpc_workflow.submission.worker.calculations_for", return_value=(None, None, None)), \
                patch("hpc_workflow.calculations.relax.revision_matches", return_value=False), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = run_worker("relax", self.plan_path, 1)
        self.assertEqual([item["status"] for item in result["finished"]], ["input_failed", "input_failed"])

    def test_worker_duplicate_lock_is_rejected(self):
        """同批两个 worker 不允许同时遍历结构。"""
        with RuntimeLock(join_local(batch_directory(self.plan_path, 1), ".worker.lock")), \
                patch("hpc_workflow.submission.worker.calculations_for", return_value=(None, None, None)):
            with self.assertRaises(ConflictError):
                run_worker("relax", self.plan_path, 1)

    def test_runtime_lock_releases_after_process_kill(self):
        """真实子进程被强制终止后，内核锁自动释放，无须删除残留锁文件。"""
        path = join_local(self.root, "kill-test.lock")
        program = ("import sys,time\nfrom hpc_workflow.submission.state import RuntimeLock\n"
                   "with RuntimeLock(sys.argv[1]):\n print('READY',flush=True)\n time.sleep(60)\n")
        process = subprocess.Popen([sys.executable, "-B", "-u", "-c", program, path], cwd=project_root(),
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(process.stdout.readline().strip(), "READY")
            with self.assertRaises(ConflictError):
                with RuntimeLock(path):
                    pass
            process.kill()
            process.wait(timeout=10)
            # Windows TerminateProcess 可先返回进程终态、随后完成文件锁清理；只等内核释放，不删文件。
            deadline = time.monotonic() + 2
            while True:
                try:
                    with RuntimeLock(path):
                        self.assertTrue(os.path.isfile(path))
                    break
                except ConflictError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()

    def test_revision_mismatch_cannot_be_rebound_by_prepare(self):
        """旧声明输入版本变更不能重新准备后把历史成功文件绑定给新输入。"""
        prepare = unittest.mock.Mock(return_value={"status": "pending", "input_revision_id": new_revision_id()})
        inspect = unittest.mock.Mock(return_value={"status": "input_failed", "reason": "计算输入与本轮计划声明版本不一致"})
        with patch("hpc_workflow.submission.planner.calculations_for", return_value=(prepare, inspect, None)), \
                patch("hpc_workflow.calculations.relax.revision_matches", return_value=False):
            tasks = scan_tasks("relax", {**self.config, "relax_subdir": "cell-relax"}, prepare=True)
        prepare.assert_not_called()
        self.assertTrue(all(task["status"] == "input_failed" for task in tasks))
        self.assertEqual([task["input_revision_id"] for task in tasks], [task["input_revision_id"] for task in self.plan["tasks"]])

    def test_command_template_change_requires_new_stage_directory(self):
        """命令模板可含科学参数，整条模板改变不能悄悄复用同目录旧成功。"""
        prepare = unittest.mock.Mock()
        inspect = unittest.mock.Mock(return_value={"status": "success"})
        atomic_write_json(join_local(batch_directory(self.plan_path, 1), "progress.json"),
                          {"schema_version": 2, "workflow_version": "0.0.2", "batch": 1,
                           "finished": [{**task, "status": "success"} for task in self.plan["tasks"]]})
        with patch("hpc_workflow.submission.planner.calculations_for", return_value=(prepare, inspect, None)):
            tasks = scan_tasks("relax", {**self.config, "relax_subdir": "cell-relax", "abacus_command": ["other-mpirun"]}, prepare=True)
        prepare.assert_not_called()
        self.assertTrue(all(task["status"] == "input_failed" for task in tasks))

    def test_planned_but_unfinished_history_cannot_attest_old_success(self):
        """只有计划而没有终态关联时，即使旧产物完整也继续实际执行。"""
        prepare = unittest.mock.Mock(return_value={"status": "pending", "input_revision_id": new_revision_id()})
        inspect = unittest.mock.Mock(return_value={"status": "success"})
        with patch("hpc_workflow.submission.planner.calculations_for", return_value=(prepare, inspect, None)):
            tasks = scan_tasks("relax", {**self.config, "relax_subdir": "cell-relax"}, prepare=True)
        self.assertEqual(prepare.call_count, 2)
        self.assertTrue(all(task["status"] == "planned" for task in tasks))

    def test_physical_rejection_survives_without_native_static_stage(self):
        """明显虚频拒绝只有简短 progress 记录时，仍按绑定输入保留终止状态。"""
        atomic_write_json(join_local(batch_directory(self.plan_path, 1), "progress.json"),
                          {"schema_version": 2, "workflow_version": "0.0.2", "batch": 1,
                           "finished": [{**self.plan["tasks"][0], "status": "physical_rejected",
                                         "reason": "Gamma 点存在明显虚频", "evidence": ["raw.err"]}]})
        prepare = unittest.mock.Mock(return_value={"status": "pending", "input_revision_id": new_revision_id()})
        inspect = unittest.mock.Mock(return_value={"status": "pending"})
        with patch("hpc_workflow.submission.planner.calculations_for", return_value=(prepare, inspect, None)), \
                patch("hpc_workflow.calculations.relax.revision_matches", return_value=True):
            tasks = scan_tasks("relax", {**self.config, "relax_subdir": "cell-relax"}, prepare=True)
        self.assertEqual(tasks[0]["status"], "physical_rejected")
        self.assertEqual(prepare.call_count, 1)

    def test_legacy_plan_cannot_execute_or_submit(self):
        """旧协议允许历史读取，但禁止执行或提交旧冻结输入。"""
        self.plan["schema_version"] = 1
        self.plan.pop("workflow_version")
        atomic_write_json(self.plan_path, self.plan)
        self.assertEqual(load_plan(self.plan_path)["schema_version"], 1)
        with self.assertRaises(IntegrityError):
            run_worker("relax", self.plan_path, 1)
        with self.assertRaises(ConfigError):
            submit_plan("relax", self.config, self.plan_path)

    def test_forged_progress_revision_cannot_skip_worker(self):
        """另一声明版本的结束事件不能让当前 worker 跳过结构。"""
        event = {**self.plan["tasks"][0], "status": "success", "input_revision_id": new_revision_id()}
        atomic_write_json(join_local(batch_directory(self.plan_path, 1), "progress.json"), {
            "schema_version": 2, "workflow_version": "0.0.2", "batch": 1, "finished": [event]})
        with self.assertRaises(IntegrityError):
            run_worker("relax", self.plan_path, 1)

    def test_staging_with_complete_manifest_is_not_a_formal_task(self):
        """遗留 staging 即使有 STRU 和成功 manifest，也不能进入计算计划。"""
        staging = join_local(self.root, "a.staging-" + new_revision_id())
        shutil.copytree(join_local(self.root, "a"), staging)
        self.assertEqual(task_directories(self.root), ["a", "b"])
        inspect = unittest.mock.Mock(return_value={"status": "pending"})
        with patch("hpc_workflow.submission.planner.calculations_for", return_value=(None, inspect, None)):
            tasks = scan_tasks("relax", self.config)
        self.assertEqual([task["path"] for task in tasks], ["a", "b"])

    def test_zstar_scan_builds_one_index_for_the_whole_batch(self):
        """多结构扫描只读一次 relax 历史，索引通过配置交给每个适配器。"""
        index = {"work_root": os.path.normcase(os.path.abspath(self.root)), "relax_subdir": "cell-relax", "entries": {}}
        seen = []
        def inspect(directory, config, revision_entry=None):
            seen.append(config["_relax_revision_index"])
            return {"status": "pending"}
        with patch("hpc_workflow.calculations.relax.build_relax_revision_index", return_value=index) as build, \
                patch("hpc_workflow.submission.planner.calculations_for", return_value=(None, inspect, None)):
            scan_tasks("zstar", self.config)
        build.assert_called_once_with(self.root, "cell-relax")
        self.assertEqual(seen, [index, index])

    def test_unrecorded_legacy_structure_is_input_failed_on_scan(self):
        """只含 STRU 的旧散装目录可显示，但不成为当前可准备或复用任务。"""
        legacy = join_local(self.root, "legacy")
        os.mkdir(legacy)
        with open(join_local(legacy, "STRU"), "w", encoding="utf-8") as handle:
            handle.write("unrecorded")
        inspect = unittest.mock.Mock(return_value={"status": "pending"})
        with patch("hpc_workflow.submission.planner.calculations_for", return_value=(None, inspect, None)):
            report = {entry["path"]: entry for entry in scan_tasks("relax", self.config)}
        self.assertEqual(report["legacy"]["status"], "input_failed")
