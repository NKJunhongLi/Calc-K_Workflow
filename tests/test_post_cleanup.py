"""已完成任务中间文件清理回归；所有数据和删除目标均限定在新版测试临时树。"""

from contextlib import redirect_stdout
import csv
import io
import os
import subprocess
import sys
import unittest
from test_post_pipeline import file_bytes
from types import SimpleNamespace
from unittest.mock import patch

import test_post_pipeline as fixtures
import cli
from hpc_workflow.common.atomic import atomic_write_csv, atomic_write_text, atomic_write_json, read_json
from uuid import uuid4
from hpc_workflow.common.errors import ConfigError, IntegrityError
from hpc_workflow.common.paths import join_local, project_root
from hpc_workflow.postprocess.config import validate_config
from hpc_workflow.submission.models import JOB_FIELDS
from hpc_workflow.submission.state import RuntimeLock


class PostCleanupTests(unittest.TestCase):
    """组合已有真实记录夹具，验证清理边界、只读预览及失败后的独立重试。"""

    def setUp(self):
        """借用夹具构造器而不继承其测试类，避免发现器重复执行整套已有测试。"""
        self.fixture = fixtures.PostPipelineTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.config = {**self.fixture.config, "cleanup_completed": True}

    def make_terminal(self, identifier: str = "mp-clean", state: str = "success",
                      reason: str = "", products: bool = True,
                      insulating: bool = True, static: bool = True) -> str:
        """生成与冻结计划关联的终态任务；失败任务可有意不具备完整介电产物。"""
        task = self.fixture.make_task("MP", identifier)
        if products:
            self.fixture.make_products(task, include_static=static, insulating=insulating)
        self.fixture.make_plan([(task, state, reason)])
        return task

    def make_intermediates(self, task: str) -> tuple[list[str], list[str]]:
        """在弛豫、参考及位移目录放小型同名载荷，同时放置必须保留的相近文件名。"""
        targets, retained = [], []
        # 精确CSR白名单和任意前缀DENSITY.restart可以递归命中；不使用真实稀疏矩阵。
        names = ("data-rR-sparse.csr", "data-HR-sparse_SPIN0.csr",
                 "data-SR-sparse_SPIN0.csr", "SPIN1_CHG.DENSITY.restart", "DENSITY.restart")
        others = ("data-HR-sparse_SPIN1.csr", "other.csr", "data-rR-sparse.csr.bak",
                  "other.restart", "DENSITY.restart.bak", "running_scf.log", "BEC.keep.dat")
        for parts in (("cell-relax", "OUT.ABACUS"), ("zstar", "0.no-move", "OUT.ABACUS"),
                      ("zstar", "1.disp", "OUT.ABACUS")):
            directory = join_local(task, *parts)
            os.makedirs(directory, exist_ok=True)
            for name in names + others:
                path = join_local(directory, name)
                atomic_write_text(path, "small isolated intermediate\n")
                (targets if name in names else retained).append(path)
        return targets, retained

    def publish_without_cleanup(self) -> dict:
        """先发布真实46列结果，明确禁用自动清理，以测试独立clean重试路径。"""
        from hpc_workflow.postprocess.pipeline import run_post
        report = run_post({**self.config, "cleanup_completed": False})
        self.assertEqual(report["status"], "success", report)
        return report

    def tree_snapshot(self) -> dict[str, bytes]:
        """记录整个隔离根内的文件及字节，检测预览是否偷偷建锁、目录或写出记录。"""
        result = {}
        for parent, directories, filenames in os.walk(self.fixture.base, followlinks=False):
            directories[:] = sorted(directories)
            for name in sorted(filenames):
                path = os.path.join(parent, name)
                if not os.path.islink(path):
                    with open(path, "rb") as handle:
                        result[os.path.relpath(path, self.fixture.base)] = handle.read()
        return result

    def assert_present(self, paths: list[str]) -> None:
        """逐个断言测试目标仍存在，防止终态不可信时误删一部分文件。"""
        for path in paths:
            self.assertTrue(os.path.isfile(path), path)

    def exercise_posix_unlink(self, scenario: str) -> dict:
        """用受限目录fd模型覆盖POSIX分支；文件和最终删除仍是真实隔离夹具内操作。"""
        from hpc_workflow.postprocess import cleanup
        task = self.fixture.make_task("MP", "mp-posix-" + scenario)
        parent = join_local(task, "zstar", "model-output")
        os.mkdir(parent)
        target = join_local(parent, "data-rR-sparse.csr")
        retained = join_local(parent, "other.csr")
        atomic_write_text(target, "real model target\n")
        atomic_write_text(retained, "real protected neighbor\n")
        signature = cleanup._signature(os.lstat(target))
        descriptor = 54321
        observed = {"opened": [], "closed": [], "stat": [], "unlink": [], "live": False}

        def open_directory(path: str, flags: int) -> int:
            """仅将真实目标父目录注册为模型fd，不允许用此模型打开别的路径。"""
            self.assertEqual(path, parent)
            self.assertTrue(os.path.isdir(path))
            self.assertFalse(observed["live"])
            self.assertTrue(flags & directory_flag)
            self.assertTrue(flags & nofollow_flag)
            observed["opened"].append((path, flags))
            observed["live"] = True
            return descriptor

        def fstat_directory(fd: int) -> object:
            """读取模型fd对应真实父目录身份，指定场景仅伪造inode不匹配。"""
            self.assertEqual(fd, descriptor)
            self.assertTrue(observed["live"])
            info = os.lstat(parent)
            return SimpleNamespace(st_dev=info.st_dev,
                                   st_ino=info.st_ino + (1 if scenario == "parent_changed" else 0))

        def stat_relative(name: str, *, dir_fd: int, follow_symlinks: bool) -> object:
            """只解析已登记父目录fd下的basename，保留真实文件签名或注入身份失配。"""
            self.assertEqual(dir_fd, descriptor)
            self.assertTrue(observed["live"])
            self.assertEqual(name, os.path.basename(target))
            self.assertFalse(os.path.isabs(name))
            self.assertIs(follow_symlinks, False)
            observed["stat"].append((name, dir_fd))
            info = os.lstat(join_local(parent, name))
            return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino,
                                   st_size=info.st_size + (1 if scenario == "file_changed" else 0),
                                   st_mtime_ns=info.st_mtime_ns, st_mode=info.st_mode)

        def unlink_relative(name: str, *, dir_fd: int) -> None:
            """相对模型fd定位并实际删除唯一夹具目标；相邻文件永远不作为删除参数。"""
            self.assertEqual(dir_fd, descriptor)
            self.assertTrue(observed["live"])
            self.assertEqual(name, os.path.basename(target))
            self.assertFalse(os.path.isabs(name))
            observed["unlink"].append((name, dir_fd))
            os.unlink(join_local(parent, name))

        def close_directory(fd: int) -> None:
            """释放且只释放已打开的模型fd，覆盖成功和身份不匹配异常路径。"""
            self.assertEqual(fd, descriptor)
            self.assertTrue(observed["live"])
            observed["closed"].append(fd)
            observed["live"] = False

        # 只替换被测模块的os引用，保留真实os.path及lstat，不改全局主机类型或系统函数。
        modeled_os = SimpleNamespace(**vars(os))
        directory_flag = getattr(os, "O_DIRECTORY", 0x1000000)
        nofollow_flag = getattr(os, "O_NOFOLLOW", 0x2000000)
        modeled_os.name = "posix"
        modeled_os.O_DIRECTORY = directory_flag
        modeled_os.O_NOFOLLOW = nofollow_flag
        modeled_os.open, modeled_os.fstat = open_directory, fstat_directory
        modeled_os.stat, modeled_os.unlink, modeled_os.close = stat_relative, unlink_relative, close_directory
        modeled_os.supports_dir_fd = {stat_relative, unlink_relative}
        result, error = None, None
        with patch.object(cleanup, "os", modeled_os):
            try:
                result = cleanup._unlink_candidate(self.fixture.work, task, target, signature)
            except IntegrityError as exc:
                error = str(exc)
        self.assertEqual(len(observed["opened"]), 1)
        self.assertEqual(observed["closed"], [descriptor])
        self.assertFalse(observed["live"])
        with open(retained, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "real protected neighbor\n")
        return {"target": target, "result": result, "error": error, "observed": observed}

    def test_posix_dir_fd_deletes_real_target_relative_to_verified_parent(self):
        """POSIX模型分支确实删除目标且保留相邻文件，unlink只得到basename和已核验fd。"""
        outcome = self.exercise_posix_unlink("success")
        self.assertIs(outcome["result"], True)
        self.assertIsNone(outcome["error"])
        self.assertFalse(os.path.lexists(outcome["target"]))
        self.assertEqual(outcome["observed"]["unlink"], [("data-rR-sparse.csr", 54321)])
        self.assertEqual(outcome["observed"]["stat"], [("data-rR-sparse.csr", 54321)])

    def test_posix_dir_fd_parent_identity_mismatch_preserves_real_target(self):
        """父目录fd身份不匹配时零unlink，关闭句柄并保留真实目标字节。"""
        outcome = self.exercise_posix_unlink("parent_changed")
        self.assertIn("父目录", outcome["error"])
        self.assertEqual(outcome["observed"]["unlink"], [])
        with open(outcome["target"], encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "real model target\n")

    def test_posix_dir_fd_file_identity_mismatch_preserves_real_target(self):
        """相对fd复核发现目标身份变化时零unlink，同样释放目录fd并保留目标。"""
        outcome = self.exercise_posix_unlink("file_changed")
        self.assertIn("文件身份", outcome["error"])
        self.assertEqual(outcome["observed"]["unlink"], [])
        with open(outcome["target"], encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "real model target\n")

    def test_automatic_cleanup_exact_names_all_subdirectories(self):
        """成功CSV发布后递归清理四类中间文件，保留其他矩阵、结果、输入和原生日志。"""
        from hpc_workflow.postprocess.pipeline import run_post
        task = self.make_terminal()
        targets, retained = self.make_intermediates(task)
        protected = {join_local(task, "zstar", name): file_bytes(join_local(task, "zstar", name))
                     for name in ("STRU", "INPUT", "BORN", "BEC.dat", "BEC.rep.dat")}
        report = run_post(self.config)
        self.assertEqual(report["status"], "success", report)
        self.assertTrue(os.path.isfile(self.fixture.output))
        self.assertEqual(report["cleanup"]["deleted_files"], len(targets), report)
        self.assertGreater(report["cleanup"]["deleted_bytes"], 0)
        self.assertFalse(any(os.path.lexists(path) for path in targets))
        self.assert_present(retained)
        for path, digest in protected.items():
            self.assertEqual(file_bytes(path), digest, path)

    def test_physical_gap_and_imaginary_terminal_tasks_are_cleaned(self):
        """明确带隙拒绝和虚频拒绝无需static结果；其输入关联通过时同样可清理。"""
        from hpc_workflow.postprocess.cleanup import run_cleanup
        gap = self.fixture.make_task("MP", "mp-gap")
        imaginary = self.fixture.make_task("MP", "mp-imaginary")
        self.fixture.make_products(gap, gap=0.0, include_static=False, insulating=False)
        self.fixture.make_products(imaginary, include_static=False)
        self.fixture.make_plan([(gap, "physical_rejected", "能带绝缘检查不通过"),
                                (imaginary, "physical_rejected", "Gamma 点存在明显虚频")])
        targets = []
        for task in (gap, imaginary):
            paths, _ = self.make_intermediates(task)
            targets.extend(paths)
        self.publish_without_cleanup()
        report = run_cleanup(self.config)
        self.assertEqual(report["status"], "success", report)
        self.assertEqual(report["eligible_tasks"], 2, report)
        self.assertEqual(report["deleted_files"], len(targets), report)
        self.assertFalse(any(os.path.lexists(path) for path in targets))

    def test_technical_symmetry_and_unknown_physical_failures_are_retained(self):
        """SCF、对称性及不明物理终止不能仅凭terminal标志删掉可续算中间结果。"""
        from hpc_workflow.postprocess.cleanup import run_cleanup
        entries, targets = [], []
        cases = (("mp-scf", "technical_failed", "SCF 不收敛"),
                 ("mp-symmetry", "technical_failed", "zstar 对称性处理失败"),
                 ("mp-unknown", "physical_rejected", "未知原因，需人工检查"),
                 ("mp-killed", "technical_failed", "意外退出"))
        for identifier, state, reason in cases:
            task = self.fixture.make_task("MP", identifier)
            self.fixture.make_products(task)
            paths, _ = self.make_intermediates(task)
            targets.extend(paths)
            entries.append((task, state, reason))
        self.fixture.make_plan(entries)
        self.publish_without_cleanup()
        report = run_cleanup(self.config)
        self.assertEqual(report["eligible_tasks"], 0, report)
        self.assertEqual(report["deleted_files"], 0, report)
        self.assert_present(targets)

    def test_success_with_bad_static_or_insulation_conflict_is_not_cleaned(self):
        """worker虽记成功，产物解析失败或绝缘结论冲突时仍保留全部中间文件。"""
        from hpc_workflow.postprocess.cleanup import run_cleanup
        broken = self.fixture.make_task("MP", "mp-bad-static")
        conflict = self.fixture.make_task("MP", "mp-conflicting-gate")
        self.fixture.make_products(broken)
        self.fixture.make_products(conflict, insulating=False)
        atomic_write_text(join_local(broken, "zstar", "dielectric_response", "static_response.json"), "broken json\n")
        self.fixture.make_plan([(broken, "success", ""), (conflict, "success", "")])
        targets = []
        for task in (broken, conflict):
            paths, _ = self.make_intermediates(task)
            targets.extend(paths)
        self.publish_without_cleanup()
        report = run_cleanup(self.config)
        self.assertEqual(report["deleted_files"], 0, report)
        self.assertEqual(report["eligible_tasks"], 0, report)
        self.assert_present(targets)

    def test_revision_mismatch_before_publication_is_retained(self):
        """输入失配会导出失败行；CSV存在并不能把失配任务升级为可删除对象。"""
        from hpc_workflow.postprocess.cleanup import run_cleanup
        task = self.make_terminal()
        targets, _ = self.make_intermediates(task)
        path = join_local(task, "task_manifest.json")
        manifest = read_json(path)
        manifest["input_revision_id"] = str(uuid4())
        atomic_write_json(path, manifest)
        self.publish_without_cleanup()
        report = run_cleanup(self.config)
        self.assertEqual(report["deleted_files"], 0, report)
        self.assert_present(targets)

    def test_preview_is_read_only_and_reports_candidates_without_csv(self):
        """独立preview无需CSV，不建锁、不写文件，只汇报候选文件和大小。"""
        from hpc_workflow.postprocess.cleanup import run_cleanup
        task = self.make_terminal()
        targets, _ = self.make_intermediates(task)
        before = self.tree_snapshot()
        report = run_cleanup(self.config, preview=True)
        self.assertEqual(report["status"], "preview", report)
        self.assertEqual(report["candidate_files"], len(targets), report)
        self.assertGreater(report["candidate_bytes"], 0)
        self.assertEqual(report["deleted_files"], 0)
        self.assertEqual(self.tree_snapshot(), before)
        self.assertFalse(os.path.exists(self.fixture.output))

    def test_validate_only_previews_without_creating_locks_or_csv(self):
        """post validate给出预计清理量，但整个操作保持只读。"""
        from hpc_workflow.postprocess.pipeline import run_post
        task = self.make_terminal()
        targets, _ = self.make_intermediates(task)
        before = self.tree_snapshot()
        report = run_post(self.config, validate_only=True)
        self.assertEqual(report["status"], "valid", report)
        self.assertEqual(report["cleanup"]["candidate_files"], len(targets), report)
        self.assertEqual(self.tree_snapshot(), before)
        self.assert_present(targets)

    def test_disabled_automatic_cleanup_still_allows_explicit_clean(self):
        """配置False只关闭collect自动删除；显式clean仍按完整资格重新核验执行。"""
        from hpc_workflow.postprocess.cleanup import run_cleanup
        task = self.make_terminal()
        targets, _ = self.make_intermediates(task)
        self.publish_without_cleanup()
        self.assert_present(targets)
        report = run_cleanup({**self.config, "cleanup_completed": False})
        self.assertEqual(report["deleted_files"], len(targets), report)

    def test_cleanup_switch_requires_bool_and_legacy_four_keys_default_on(self):
        """删除开关只接受真实布尔值；旧四键完整配置继续按默认开启执行。"""
        from hpc_workflow.postprocess.pipeline import run_post
        for value in (1, 0, "False", "True", None):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                validate_config({**self.config, "cleanup_completed": value})
        task = self.make_terminal()
        targets, _ = self.make_intermediates(task)
        # 老配置未声明新键，不能因配置白名单扩展而失去原有可执行性。
        report = run_post(self.fixture.config)
        self.assertEqual(report["status"], "success", report)
        self.assertEqual(report["cleanup"]["deleted_files"], len(targets), report)

    def test_cli_clean_and_validate_help_are_positional_actions(self):
        """清理和验证仍采用位置动作，帮助可以直接列出clean而不读取默认工作根。"""
        self.assertEqual(cli.build_parser().parse_args(["post", "clean"]).action, "clean")
        self.assertEqual(cli.build_parser().parse_args(["post", "validate"]).action, "validate")
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["help", "post"]), 0)
        self.assertIn("clean", output.getvalue())
        self.assertIn("--config", output.getvalue())

    def test_explicit_clean_is_idempotent_and_never_rewrites_csv(self):
        """CSV已存在时可重试清理，重复调用不重导表格、不把缺文件当作失败。"""
        from hpc_workflow.postprocess.cleanup import run_cleanup
        task = self.make_terminal()
        self.make_intermediates(task)
        self.publish_without_cleanup()
        digest = file_bytes(self.fixture.output)
        first = run_cleanup(self.config)
        second = run_cleanup(self.config)
        self.assertEqual(first["status"], "success", first)
        self.assertEqual(second["status"], "success", second)
        self.assertEqual(second["deleted_files"], 0, second)
        self.assertEqual(file_bytes(self.fixture.output), digest)

    def test_missing_or_modified_csv_blocks_all_deletion(self):
        """独立clean不能绕过结果发布，已有人修改的任一物性列也必须拒绝使用。"""
        from hpc_workflow.postprocess.cleanup import run_cleanup
        task = self.make_terminal()
        targets, _ = self.make_intermediates(task)
        report = run_cleanup(self.config)
        self.assertEqual(report["status"], "blocked", report)
        self.assert_present(targets)
        self.publish_without_cleanup()
        with open(self.fixture.output, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields, rows = reader.fieldnames, list(reader)
        rows[0]["band_gap_ev"] = "987.65"
        with open(self.fixture.output, "w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        report = run_cleanup(self.config)
        self.assertEqual(report["status"], "blocked", report)
        self.assertEqual(report["deleted_files"], 0, report)
        self.assert_present(targets)

    def test_csv_change_after_initial_comparison_cannot_become_new_baseline(self):
        """CSV刚完成逐列比较后被修改，也不能把新字节当作快照基线继续删除。"""
        from hpc_workflow.postprocess import cleanup
        task = self.make_terminal()
        targets, _ = self.make_intermediates(task)
        self.publish_without_cleanup()
        original = cleanup._read_published_csv
        changed = False

        def change_after_comparison(path: str) -> dict[str, tuple[str, ...]]:
            """首次读取返回旧可信内容后立即改物性，模拟真实比对与下一快照的竞争。"""
            nonlocal changed
            result = original(path)
            if not changed:
                changed = True
                with open(path, encoding="utf-8-sig", newline="") as handle:
                    reader = csv.DictReader(handle)
                    fields, rows = reader.fieldnames, list(reader)
                rows[0]["band_gap_ev"] = "123.456"
                with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)
            return result

        with patch.object(cleanup, "_read_published_csv", side_effect=change_after_comparison):
            report = cleanup.run_cleanup(self.config)
        self.assertEqual(report["deleted_files"], 0, report)
        self.assertIn(report["status"], {"blocked", "partial_failed"}, report)
        self.assert_present(targets)

    def test_csv_publication_failure_never_starts_cleanup(self):
        """CSV写出发生磁盘错误时，中间文件和原始计算现场全部保留。"""
        from hpc_workflow.postprocess.pipeline import run_post
        task = self.make_terminal()
        targets, _ = self.make_intermediates(task)
        with patch("hpc_workflow.postprocess.pipeline.write_results_csv", side_effect=OSError("test disk full")):
            with self.assertRaises(OSError):
                run_post(self.config)
        self.assertFalse(os.path.lexists(self.fixture.output))
        self.assert_present(targets)

    def test_unfinished_batch_does_not_delete_already_finished_member(self):
        """仍有批内等待结构时不发布整表，也不提前删除已完成成员的中间结果。"""
        from hpc_workflow.postprocess.pipeline import run_post
        completed = self.fixture.make_task("MP", "mp-finished")
        waiting = self.fixture.make_task("MP", "mp-waiting")
        self.fixture.make_products(completed)
        self.fixture.make_plan([(completed, "success", ""), (waiting, "", "")])
        targets, _ = self.make_intermediates(completed)
        report = run_post(self.config)
        self.assertEqual(report["status"], "incomplete", report)
        self.assertFalse(os.path.lexists(self.fixture.output))
        self.assert_present(targets)

    def test_input_or_plan_change_after_publication_blocks_clean(self):
        """CSV发布后出现新输入或新续算计划时，旧表不再授权当前计算树的清理。"""
        from hpc_workflow.postprocess.cleanup import run_cleanup
        task = self.make_terminal()
        targets, _ = self.make_intermediates(task)
        self.publish_without_cleanup()
        self.fixture.make_plan([(task, "", "")])
        report = run_cleanup(self.config)
        self.assertEqual(report["status"], "blocked", report)
        self.assert_present(targets)

    def test_revision_changed_after_publication_cannot_use_old_csv(self):
        """已发布表格对应旧声明版本，不能据此删除新版本的现场。"""
        from hpc_workflow.postprocess.cleanup import run_cleanup
        task = self.make_terminal()
        targets, _ = self.make_intermediates(task)
        self.publish_without_cleanup()
        path = join_local(task, "task_manifest.json")
        manifest = read_json(path)
        manifest["input_revision_id"] = str(uuid4())
        atomic_write_json(path, manifest)
        report = run_cleanup(self.config)
        self.assertEqual(report["status"], "blocked", report)
        self.assertEqual(report["deleted_files"], 0, report)
        self.assert_present(targets)

    def test_independent_clean_collects_and_parses_once(self):
        """独立清理只发现和解析一次，锁内复用历史与父版本索引并检查stat。"""
        from hpc_workflow.postprocess import pipeline
        from hpc_workflow.postprocess.cleanup import run_cleanup
        task = self.make_terminal()
        targets, _ = self.make_intermediates(task)
        self.publish_without_cleanup()
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
                      side_effect=AssertionError("独立清理必须复用本轮已读父版本索引")):
            result = run_cleanup(self.config)
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(collected.call_count, 1)
        self.assertEqual(discovered.call_count, 1)
        self.assertEqual(parsed.call_count, 1)
        self.assertEqual(result["deleted_files"], len(targets))

    def test_older_submitted_unfinished_plan_blocks_cleanup(self):
        """新轮记录成功不能掩盖旧轮已提交未结束作业；未知队列状态时保守保留文件。"""
        from hpc_workflow.postprocess.cleanup import run_cleanup
        task = self.fixture.make_task("MP", "mp-old-submitted")
        self.fixture.make_products(task)
        previous = self.fixture.make_plan([(task, "", "")])
        # 不伪造队列快照；唯一提交台账已经受理而该批进度未结束，足以阻止删除。
        atomic_write_csv(join_local(os.path.dirname(previous), "jobs.csv"), JOB_FIELDS,
                         [{"batch": "1", "job_id": "12345", "submit_status": "SUBMITTED",
                           "submitted_at": "2026-10-24T00:01:00+00:00",
                           "stdout": "batches/0001/slurm-12345.out",
                           "stderr": "batches/0001/slurm-12345.err", "reason": ""}])
        self.fixture.make_plan([(task, "success", "")])
        targets, _ = self.make_intermediates(task)
        self.publish_without_cleanup()
        with patch("hpc_workflow.submission.slurm.query_jobs",
                   side_effect=AssertionError("cleanup cannot query scheduler")):
            report = run_cleanup(self.config)
        self.assertEqual(report["deleted_files"], 0, report)
        self.assertIn(report["status"], {"blocked", "partial_failed"}, report)
        self.assert_present(targets)

    def test_historical_frozen_calculation_directory_lock_is_respected(self):
        """历史计划使用过不同阶段目录时也要取得其锁，不能只锁当前模板目录。"""
        from hpc_workflow.postprocess.cleanup import run_cleanup
        task = self.fixture.make_task("MP", "mp-layout-history")
        self.fixture.make_products(task)
        old_directory = join_local(task, "zstar-old")
        os.mkdir(old_directory)
        atomic_write_text(join_local(old_directory, "STRU"), "old frozen structure\n")
        atomic_write_text(join_local(old_directory, "INPUT"), "INPUT_PARAMETERS\n")
        old_target = join_local(old_directory, "data-rR-sparse.csr")
        atomic_write_text(old_target, "historic output\n")
        self.fixture.make_plan([(task, "success", "")],
                               {**self.fixture.execution, "zstar_subdir": "zstar-old"})
        self.fixture.make_plan([(task, "success", "")])
        targets, _ = self.make_intermediates(task)
        self.publish_without_cleanup()
        with RuntimeLock(join_local(old_directory, ".hpc-worker.lock")):
            report = run_cleanup(self.config)
        self.assertEqual(report["deleted_files"], 0, report)
        self.assertTrue(report["issues"], report)
        self.assert_present(targets + [old_target])

    def test_controller_lock_conflict_preserves_every_target(self):
        """提交控制器持锁时禁止清理，避免检查结束后新计划同时开始改写现场。"""
        from hpc_workflow.postprocess.cleanup import run_cleanup
        task = self.make_terminal()
        targets, _ = self.make_intermediates(task)
        self.publish_without_cleanup()
        for stage in ("relax", "zstar"):
            lock = join_local(self.fixture.work, ".hpc_runs", f".{stage}.controller.lock")
            with self.subTest(stage=stage), RuntimeLock(lock):
                report = run_cleanup(self.config)
            self.assertIn(report["status"], {"blocked", "partial_failed"}, report)
            self.assertEqual(report["deleted_files"], 0, report)
            self.assert_present(targets)

    def test_worker_lock_conflict_preserves_current_structure(self):
        """worker还持有结构锁时，记录中虽有终态也不删除其正在使用的中间文件。"""
        from hpc_workflow.postprocess.cleanup import run_cleanup
        task = self.make_terminal()
        targets, _ = self.make_intermediates(task)
        self.publish_without_cleanup()
        for stage in ("cell-relax", "zstar"):
            with self.subTest(stage=stage), RuntimeLock(join_local(task, stage, ".hpc-worker.lock")):
                report = run_cleanup(self.config)
            self.assertEqual(report["deleted_files"], 0, report)
            self.assertTrue(report["issues"], report)
            self.assert_present(targets)

    def test_single_unlink_failure_does_not_stop_other_candidates(self):
        """一个文件权限不足只影响该文件，后续目录和候选仍遍历，便于重试释放空间。"""
        from hpc_workflow.postprocess import cleanup
        task = self.make_terminal()
        targets, retained = self.make_intermediates(task)
        self.publish_without_cleanup()
        original = cleanup._unlink_candidate
        denied = targets[0]

        def deny_one(root: str, task_dir: str, path: str, signature: tuple) -> bool:
            """只对一个本测试中间文件模拟权限错误，其余调用真实受限删除实现。"""
            if path == denied:
                raise PermissionError("isolated test permission failure")
            return original(root, task_dir, path, signature)

        with patch.object(cleanup, "_unlink_candidate", side_effect=deny_one):
            report = cleanup.run_cleanup(self.config)
        self.assertEqual(report["status"], "partial_failed", report)
        self.assertEqual(report["deleted_files"], len(targets) - 1, report)
        self.assertTrue(os.path.isfile(denied))
        self.assertFalse(any(os.path.lexists(path) for path in targets if path != denied))
        self.assert_present(retained)
        self.assertTrue(any("permission" in item["reason"].lower() for item in report["issues"]), report)

    def test_candidate_missing_after_enumeration_is_idempotent(self):
        """枚举后文件被另一清理者移除属于幂等缺失，其余目标仍正常释放。"""
        from hpc_workflow.postprocess import cleanup
        task = self.make_terminal()
        targets, _ = self.make_intermediates(task)
        self.publish_without_cleanup()
        original = cleanup._unlink_candidate
        disappearing = targets[0]

        def remove_before_unlink(root: str, task_dir: str, path: str, signature: tuple) -> bool:
            """仅删除本隔离夹具的一个目标，再让真实helper识别已不存在。"""
            if path == disappearing:
                os.unlink(path)
            return original(root, task_dir, path, signature)

        with patch.object(cleanup, "_unlink_candidate", side_effect=remove_before_unlink):
            report = cleanup.run_cleanup(self.config)
        self.assertEqual(report["status"], "success", report)
        self.assertEqual(report["deleted_files"], len(targets) - 1, report)
        self.assertEqual(report["missing_files"], 1, report)
        self.assertFalse(any(os.path.lexists(path) for path in targets))

    def test_candidate_replaced_after_enumeration_is_preserved(self):
        """候选被改写后不能按旧签名删除；保留新文件并继续处理其余中间文件。"""
        from hpc_workflow.postprocess import cleanup
        task = self.make_terminal()
        targets, _ = self.make_intermediates(task)
        self.publish_without_cleanup()
        original = cleanup._unlink_candidate
        replaced = targets[0]

        def replace_before_unlink(root: str, task_dir: str, path: str, signature: tuple) -> bool:
            """用不同长度的新字节替代一个隔离目标，确保跨平台stat签名必然变化。"""
            if path == replaced:
                atomic_write_text(path, "replacement content must be kept\n")
            return original(root, task_dir, path, signature)

        with patch.object(cleanup, "_unlink_candidate", side_effect=replace_before_unlink):
            report = cleanup.run_cleanup(self.config)
        self.assertEqual(report["status"], "partial_failed", report)
        self.assertEqual(report["deleted_files"], len(targets) - 1, report)
        with open(replaced, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "replacement content must be kept\n")
        self.assertFalse(any(os.path.lexists(path) for path in targets if path != replaced))

    def test_target_and_directory_links_never_delete_external_file(self):
        """不跟随目录软链接，也不删除具有匹配名称的链接目标；外部仅指测试树内其他目录。"""
        from hpc_workflow.postprocess import cleanup
        task = self.make_terminal()
        outside = join_local(self.fixture.base, "outside-work")
        os.mkdir(outside)
        target = join_local(outside, "data-rR-sparse.csr")
        atomic_write_text(target, "outside fixture must survive\n")
        directory_link = join_local(task, "zstar", "linked-output")
        file_link = join_local(task, "zstar", "data-rR-sparse.csr")
        modeled = False
        try:
            os.symlink(outside, directory_link, target_is_directory=True)
            os.symlink(target, file_link)
        except (OSError, NotImplementedError):
            # Windows无创建软链接权限时，不跳过边界回归；用受控reparse判定建模两种入口。
            modeled = True
            if not os.path.lexists(directory_link):
                os.mkdir(directory_link)
                atomic_write_text(join_local(directory_link, "data-rR-sparse.csr"), "modeled linked output\n")
            if not os.path.lexists(file_link):
                atomic_write_text(file_link, "modeled linked file\n")
        self.publish_without_cleanup()
        before = file_bytes(target)
        original = cleanup._is_reparse

        def identify_test_links(path: str) -> bool:
            """只有本测试两个模型入口替代系统reparse标志；其他路径仍走真实检查。"""
            return (modeled and path in (directory_link, file_link)) or original(path)

        with patch.object(cleanup, "_is_reparse", side_effect=identify_test_links):
            report = cleanup.run_cleanup(self.config)
        self.assertEqual(report["deleted_files"], 0, report)
        self.assertEqual(file_bytes(target), before)
        self.assertTrue(os.path.lexists(directory_link))
        self.assertTrue(os.path.lexists(file_link))
        if modeled and not os.path.islink(directory_link):
            self.assertTrue(os.path.isfile(join_local(directory_link, "data-rR-sparse.csr")))

    def test_real_cli_clean_after_collect_and_repeated_clean(self):
        """真实CLI支持collect后显式clean并重复重试，既不覆盖CSV也不重跑zstar。"""
        task = self.make_terminal()
        targets, _ = self.make_intermediates(task)
        config_path = join_local(self.fixture.base, "cleanup_custom.py")
        atomic_write_text(config_path, "CONFIG = " + repr({"work_root": self.fixture.work,
                          "output_csv": self.fixture.output, "cleanup_completed": False}) + "\n")
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8",
                           TMP=self.fixture.base, TEMP=self.fixture.base, TMPDIR=self.fixture.base)
        cli = join_local(project_root(), "cli.py")
        # 独立进程走完整配置入口，防止只有直接Python调用能够使用新功能。
        for action in ("collect", "clean", "clean"):
            completed = subprocess.run([sys.executable, "-B", cli, "post", action, "--config", config_path],
                                       cwd=project_root(), env=environment, stdin=subprocess.DEVNULL,
                                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                                       check=False, timeout=45)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            if action == "collect":
                self.assert_present(targets)
                digest = file_bytes(self.fixture.output)
            else:
                self.assertEqual(file_bytes(self.fixture.output), digest)
                self.assertFalse(any(os.path.lexists(path) for path in targets))


if __name__ == "__main__":
    unittest.main()
