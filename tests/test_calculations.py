"""计算适配层回归：使用小晶胞和模拟命令，不调用 ABACUS、zstar 或 Slurm。"""

import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

import numpy as np
from ase import Atoms
from ase.units import Bohr

from hpc_workflow.common.atomic import atomic_write_json, atomic_write_text
from hpc_workflow.common.errors import IntegrityError
from hpc_workflow.common.paths import join_local, project_root
from hpc_workflow.calculations import relax, symmetry, zstar
from hpc_workflow.records.manifest import new_revision_id


class CalculationTests(unittest.TestCase):
    """全部临时输入和产物限定在新版项目测试目录，真实任务不参与回归。"""

    def setUp(self):
        """建立单原子简单立方原胞与独立任务根。"""
        root = join_local(project_root(), ".test-artifacts")
        os.makedirs(root, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.task = join_local(self.temporary.name, "task")
        os.mkdir(self.task)
        self.config = {"work_root": self.temporary.name, "relax_subdir": "cell-relax", "zstar_subdir": "zstar", "minimum_distance": 0.1}
        atomic_write_json(join_local(self.task, "task_manifest.json"), {"schema_version": 2,
                          "workflow_version": "0.0.2", "status": "success", "input_revision_id": new_revision_id()})
        self.text = ("ATOMIC_SPECIES\nSi 28.085 /cluster/private/Si.upf\n\nNUMERICAL_ORBITAL\n/cluster/private/Si.orb\n\n"
                     f"LATTICE_CONSTANT\n{1 / Bohr:.17g}\n\nLATTICE_VECTORS\n4 0 0\n0 4 0\n0 0 4\n\n"
                     "ATOMIC_POSITIONS\nDirect\n\nSi\n0\n1\n0 0 0 1 1 1\n")
        atomic_write_text(join_local(self.task, "STRU"), self.text)
        atomic_write_text(join_local(self.task, "INPUT"), "INPUT_PARAMETERS\ncalculation cell-relax\nrelax_nmax 500\n")

    def make_relaxed(self, log=None):
        """准备含 v3.10.1 离子步标题、短收敛字段和结束标记的产物，返回阶段目录。"""
        prepared = relax.prepare_relax(self.task, {**self.config, "_revision_entry": getattr(self, "relax_revision", None)})
        self.assertEqual(prepared["status"], "pending", prepared)
        self.relax_revision = {key: prepared[key] for key in relax.REVISION_FIELDS if key in prepared}
        self.config["_revision_entry"] = self.relax_revision
        atomic_write_json(join_local(self.temporary.name, ".hpc_runs", "relax", "test", "plan.json"), {
            "schema_version": 2, "workflow_version": "0.0.2", "stage": "relax", "task_root": "../../..",
            "run_id": "test", "execution": {}, "groups": [],
            "tasks": [{"path": "task", "status": "success", **self.relax_revision}]})
        directory = join_local(self.task, "cell-relax")
        output = join_local(directory, "OUT.ABACUS")
        os.makedirs(output, exist_ok=True)
        atomic_write_text(join_local(output, "STRU_ION_D"), self.text)
        default_log = ("STEP OF ION RELAXATION : 3\ncharge density convergence is achieved\n"
                       "Relaxation is converged!\nTotal Time : 1\n")
        atomic_write_text(join_local(output, "running_cell-relax.log"), default_log if log is None else log)
        atomic_write_json(join_local(directory, "abacus.json"), {})
        atomic_write_json(join_local(directory, "time.json"), {})
        return directory

    def make_products(self):
        """建立用户要求的最小完成产物，不添加 qpoints/irreps。"""
        if not hasattr(self, "relax_revision"):
            self.make_relaxed()
        if not hasattr(self, "zstar_revision"):
            self.zstar_revision = relax.new_stage_revision(self.task, self.config, "zstar")
        self.config["_revision_entry"] = self.zstar_revision
        directory = join_local(self.task, "zstar")
        os.makedirs(directory, exist_ok=True)
        if not os.path.exists(join_local(directory, "STRU")):
            atomic_write_text(join_local(directory, "STRU"), self.text)
        atomic_write_text(join_local(directory, "INPUT"), "INPUT_PARAMETERS\ncalculation scf\n")
        band = join_local(directory, "0.no-move", "pyatb-band", "Out", "Band_Structure")
        os.makedirs(band, exist_ok=True)
        for name in ("band.dat", "band_info.dat"):
            atomic_write_text(join_local(band, name), "test\n")
        atomic_write_json(join_local(directory, "0.no-move", "zstar_insulation.json"), {"insulating": True, "gap_eV": 1, "threshold_eV": 0.01})
        for name in ("BEC.dat", "BEC.rep.dat"):
            atomic_write_text(join_local(directory, name), "# atom species tensor\n1 Si 1 0 0 0 1 0 0 0 1\n")
        atomic_write_text(join_local(directory, "BORN"), "# epsilon and Born\n1 0 0 0 1 0 0 0 1\n1 0 0 0 1 0 0 0 1\n")
        atomic_write_json(join_local(directory, "dielectric_response", "static_response.json"), {"tensor": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]})
        return directory

    def test_relax_prepare_no_overwrite(self):
        """已有弛豫 STRU 不因根 STRU 改变而覆盖。"""
        first = relax.prepare_relax(self.task, self.config)
        atomic_write_text(join_local(self.task, "STRU"), "changed root")
        second = relax.prepare_relax(self.task, {**self.config, "_revision_entry": first})
        self.assertEqual(first["input_revision_id"], second["input_revision_id"])
        with open(join_local(self.task, "cell-relax", "STRU"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), self.text)

    def test_relax_completion_requires_positive_convergence(self):
        """第 500 步存在明确未收敛结论时失败，不能只凭正常结束误判成功。"""
        self.make_relaxed("STEP OF RELAXATION : 500\nRelaxation is not converged!\nTotal Time : 1\n")
        result = relax.inspect_relax(self.task, self.config)
        self.assertEqual(result["status"], "technical_failed")
        self.assertIn("步数上限", result["reason"])

    def test_relax_at_limit_but_converged_is_success(self):
        """最后允许步收敛仍正常成功，保留旧检查意图并修正其粗判。"""
        self.make_relaxed("STEP OF RELAXATION : 500\nRelaxation is converged!\nTotal Time : 1\n")
        self.assertEqual(relax.inspect_relax(self.task, self.config)["status"], "success")

    def test_ion_convergence_does_not_replace_lattice_convergence(self):
        """真实 ABACUS 离子已收敛但最后晶格未收敛，不能标记 cell-relax 成功。"""
        for events in ("Lattice relaxation is not converged yet\nIon relaxation is converged!\n",
                       "Ion relaxation is converged!\nLattice relaxation is not converged yet\n"):
            with self.subTest(events=events):
                self.make_relaxed("STEP OF RELAXATION : 500\n" + events + "Total Time : 1\n")
                result = relax.inspect_relax(self.task, self.config)
                self.assertEqual(result["status"], "technical_failed")
                self.assertIn("步数上限", result["reason"])

    def test_final_lattice_convergence_is_success(self):
        """中间步晶格未收敛、最后晶格收敛时应成功。"""
        self.make_relaxed("STEP OF RELAXATION : 3\nLattice relaxation is not converged yet\n"
                          "Ion relaxation is converged!\nLattice relaxation is converged!\nTotal Time : 1\n")
        self.assertEqual(relax.inspect_relax(self.task, self.config)["status"], "success")

    def test_missing_short_fields_do_not_invent_failure(self):
        """末步与正常结束完整、无负面结论时，缺少某类短字段不额外制造失败。"""
        for events in ("", "convergence is achieved\n", "Ion relaxation is converged!\n"):
            with self.subTest(events=events):
                self.make_relaxed("STEP OF RELAXATION : 3\n" + events + "Total Time : 1\n")
                self.assertEqual(relax.inspect_relax(self.task, self.config)["status"], "success")

    def test_relax_step_labels_supported_across_versions(self):
        """两种明确支持的 STEP 标题都能定位第 500 步并保留未收敛判断。"""
        for label in ("STEP OF RELAXATION : 500", "STEP OF ION RELAXATION : 500"):
            self.make_relaxed(label + "\nIon relaxation is converged!\nLattice relaxation is not converged yet\nTotal Time : 1\n")
            result = relax.inspect_relax(self.task, self.config)
            self.assertEqual(result["status"], "technical_failed")
            self.assertIn("步数上限", result["reason"])

    def test_latest_header_wins_for_both_label_orders(self):
        """两种标题合并按文件先后选最新，不能固定优先一种标题或取最大步号。"""
        labels = ("STEP OF RELAXATION", "STEP OF ION RELAXATION")
        for older, latest in (labels, labels[::-1]):
            for final_failed in (False, True):
                with self.subTest(older=older, latest=latest, final_failed=final_failed):
                    old_event = "Relaxation is converged!" if final_failed else "Relaxation is not converged!"
                    final_event = "Relaxation is not converged!" if final_failed else "Relaxation is converged!"
                    self.make_relaxed(f"{older} : 500\n{old_event}\n{latest} : 3\n{final_event}\nTotal Time : 1\n")
                    result = relax.inspect_relax(self.task, self.config)
                    self.assertEqual(result["status"], "technical_failed" if final_failed else "success", result)
                    if final_failed:
                        self.assertNotIn("步数上限", result["reason"])

    def test_previous_step_scf_failure_does_not_poison_final_step(self):
        """前一离子步 SCF 失败不影响后续末步成功，日志内旧完成标记也不改变选段。"""
        self.make_relaxed("STEP OF RELAXATION : 1\nconvergence has not been achieved @_@\nTotal Time : 1\n"
                          "STEP OF ION RELAXATION : 2\nconvergence is achieved\nRelaxation is converged!\nTotal Time : 2\n")
        self.assertEqual(relax.inspect_relax(self.task, self.config)["status"], "success")

    def test_final_step_scf_failure_is_sticky_after_success(self):
        """同一末步中 SCF 失败不可被其后成功覆盖，成功后再失败同样明确失败。"""
        failed = "convergence has not been achieved @_@\n"
        passed = "convergence is achieved\n"
        for events in (failed + passed, passed + failed, failed + "OUT_OF_MEMORY\n"):
            with self.subTest(events=events):
                self.make_relaxed("STEP OF ION RELAXATION : 7\n" + events + "Relaxation is converged!\nTotal Time : 1\n")
                result = relax.inspect_relax(self.task, self.config)
                self.assertEqual(result["status"], "technical_failed")
                self.assertEqual(result["reason"], "SCF 不收敛")
                execution = {"returncode": 7, "output_tail": "OUT_OF_MEMORY",
                             "status": "technical_failed", "reason": "内存超限"}
                executed = relax.run_relax(self.task, self.config, lambda label, argv, cwd: execution)
                self.assertEqual(executed["reason"], "SCF 不收敛")

    def test_relax_events_follow_text_order_even_on_one_line(self):
        """整体弛豫结论取最后一次出现，同行先负后正可恢复，反向则失败。"""
        for separator in ("\n", " ; "):
            for failed_last in (False, True):
                with self.subTest(separator=separator, failed_last=failed_last):
                    events = ["Relaxation is not converged yet!", "Relaxation is converged!"]
                    if failed_last:
                        events.reverse()
                    self.make_relaxed("STEP OF ION RELAXATION : 4\n" + separator.join(events) + "\nTotal Time : 1\n")
                    self.assertEqual(relax.inspect_relax(self.task, self.config)["status"],
                                     "technical_failed" if failed_last else "success")

    def test_whole_relaxation_can_resolve_prior_lattice_failure(self):
        """明确整体 Relaxation 最终成功可证明完成，单独 Ion 成功仍不能覆盖晶格失败。"""
        self.make_relaxed("STEP OF ION RELAXATION : 4\nLattice relaxation is not converged yet\n"
                          "Ion relaxation is converged!\nRelaxation is converged!\nTotal Time : 1\n")
        self.assertEqual(relax.inspect_relax(self.task, self.config)["status"], "success")

    def test_missing_header_or_normal_end_is_explicit_failure(self):
        """无受支持末步标题或末步无正常结束标记时，即使其他产物齐全也失败。"""
        samples = ("", "Relaxation is converged!\nTotal Time : 1\n",
                   "RELAX CELL : 3\nRelaxation is converged!\nTotal Time : 1\n",
                   "STEP OF ION RELAXATION : 3\nRelaxation is converged!\n",
                   "STEP OF RELAXATION : 2\nTotal Time : 1\nSTEP OF ION RELAXATION : 3\nRelaxation is converged!\n")
        for text in samples:
            with self.subTest(text=text):
                self.make_relaxed(text)
                self.assertEqual(relax.inspect_relax(self.task, self.config)["status"], "technical_failed")

    def test_last_step_keywords_allow_case_whitespace_and_punctuation(self):
        """标题及四个短字段大小写、横向空白和外围标点宽容，否定字段仍正确识别。"""
        prefix = " \tStEp\t OF   iOn\tReLaXaTiOn \t: \t8\r\n"
        for events, expected in (("!! cOnVeRgEnCe\t is   achieved !!!\r\nReLaXaTiOn  is\tconverged!!!\r\n", "success"),
                                 ("!! CONVERGENCE  has\tnot  been\tachieved @_@\r\nRelaxation is converged!\r\n", "technical_failed"),
                                 ("Relaxation\tIS  NOT\tCONVERGED yet!\r\n", "technical_failed")):
            with self.subTest(events=events):
                self.make_relaxed(prefix + events + "TOTAL\t TIME : 1\r\n")
                self.assertEqual(relax.inspect_relax(self.task, self.config)["status"], expected)

    def test_final_step_larger_than_one_reverse_block_is_not_truncated(self):
        """末步正文超过 256 KiB 时仍读取标题起全部内容，不遗漏靠前的 SCF 失败。"""
        padding = "diagnostic padding without conclusions\n" * 9000
        self.assertGreater(len(padding.encode("utf-8")), 262144)
        for scf, expected in (("convergence is achieved\n", "success"),
                              ("convergence has not been achieved\n", "technical_failed")):
            with self.subTest(scf=scf):
                self.make_relaxed("STEP OF RELAXATION : 1\nRelaxation is not converged!\n"
                                  "STEP OF ION RELAXATION : 2\n" + scf + padding
                                  + "convergence is achieved\nRelaxation is converged!\nTotal Time : 1\n")
                result = relax.inspect_relax(self.task, self.config)
                self.assertEqual(result["status"], expected, result)
                if expected == "technical_failed":
                    self.assertEqual(result["reason"], "SCF 不收敛")

    def test_last_header_split_across_default_reverse_blocks(self):
        """真实 256 KiB 逆读边界落在末步标题内部时，必须拼接得到该完整标题。"""
        header = "STEP OF ION RELAXATION : 12\n"
        body = "convergence is achieved\nRelaxation is converged!\n"
        ending = "\nTotal Time : 1\n"
        # 首块从 header 第 13 个字节开始；旧标题的失败用于识别错误回退。
        suffix_size = 262144 - (len(header) - 13)
        suffix = body + "x" * (suffix_size - len(body) - len(ending)) + ending
        self.make_relaxed("STEP OF RELAXATION : 1\nconvergence has not been achieved\n" + header + suffix)
        self.assertEqual(relax.inspect_relax(self.task, self.config)["status"], "success")

    def test_reverse_block_suffix_is_not_a_complete_header(self):
        """逆读块开头只是某行后缀时，不能把行中 STEP 文本误认成完整标题。"""
        false_suffix = "STEP OF RELAXATION : 99\nTotal Time : 1\n"
        text = "STEP OF ION RELAXATION : 7\nRelaxation is converged!\nnoise " + false_suffix
        directory = self.make_relaxed(text)
        path = join_local(directory, "OUT.ABACUS", "running_cell-relax.log")
        selected, step = relax._read_last_relax_step(path, block_size=len(false_suffix.encode("utf-8")))
        self.assertEqual(step, 7)
        self.assertEqual(selected, text)

    def test_reverse_reader_uses_bounded_io_and_stops_at_latest_header(self):
        """记录真实二进制读取，默认单次不超过 256 KiB 且找到末步后不读完整旧历史。"""
        block_size = 262144
        text = "old detail\n" * 90000 + "STEP OF ION RELAXATION : 9\nRelaxation is converged!\nTotal Time : 1\n"
        directory = self.make_relaxed(text)
        path = join_local(directory, "OUT.ABACUS", "running_cell-relax.log")
        real_open = open
        sizes = []

        @contextmanager
        def observed_open(filename: str, mode: str = "r", **kwargs):
            """只代理本测试日志的 rb 打开，保留真实文件与 seek/tell 行为并观察 read 大小。"""
            self.assertEqual(filename, path)
            self.assertEqual(mode, "rb")
            with real_open(filename, mode, **kwargs) as handle:
                proxy = Mock(wraps=handle)

                def observed_read(size: int = -1) -> bytes:
                    """拒绝无界 read 或超过块上限的读取，其余返回真实文件字节。"""
                    self.assertGreater(size, 0)
                    self.assertLessEqual(size, block_size)
                    sizes.append(size)
                    return handle.read(size)

                proxy.read.side_effect = observed_read
                yield proxy

        with patch.object(relax, "open", observed_open, create=True):
            selected, step = relax._read_last_relax_step(path)
        self.assertEqual(step, 9)
        self.assertTrue(selected.startswith("STEP OF ION RELAXATION : 9\n"))
        self.assertEqual(sizes[0], block_size)
        self.assertLess(sum(sizes), len(text.encode("utf-8")))

    def test_log_read_error_is_explicit_failure(self):
        """逆向读取发生 I/O 错误时明确技术失败，不伪装成仍在等待或成功。"""
        self.make_relaxed()
        with patch.object(relax, "_read_last_relax_step", side_effect=OSError("fixture read failure")):
            self.assertEqual(relax.inspect_relax(self.task, self.config)["status"], "technical_failed")

    def test_missing_log_fails_only_after_stage_directory_exists(self):
        """尚未准备的阶段仍 pending；已有阶段缺少弛豫日志必须明确技术失败。"""
        self.assertEqual(relax.inspect_relax(self.task, self.config)["status"], "pending")
        directory = self.make_relaxed()
        os.unlink(join_local(directory, "OUT.ABACUS", "running_cell-relax.log"))
        result = relax.inspect_relax(self.task, self.config)
        self.assertEqual(result["status"], "technical_failed")
        self.assertIn("running_cell-relax.log", result["reason"])

    def test_generic_tail_keeps_128_kib_limit(self):
        """其他调用者使用的 _tail 仍只返回末尾 128 KiB，不被弛豫逆读需求扩大。"""
        path = join_local(self.temporary.name, "bounded-tail.log")
        atomic_write_text(path, "x" * (131072 + 100) + "END")
        tail = relax._tail(path)
        self.assertEqual(len(tail.encode("utf-8")), 131072)
        self.assertTrue(tail.endswith("END"))

    def test_failure_classifier_can_exclude_scf_without_losing_oom(self):
        """分类器默认保留 SCF 识别；显式关闭仅屏蔽 SCF，不屏蔽 OOM。"""
        scf = "convergence has not been achieved"
        self.assertEqual(relax.classify_failure(scf)["reason"], "SCF 不收敛")
        self.assertIsNone(relax.classify_failure(scf, include_scf=False))
        for include_scf in (False, True):
            self.assertIsNotNone(relax.classify_failure("OUT_OF_MEMORY", include_scf=include_scf))

    def test_relax_command_old_scf_tail_and_metadata_do_not_override_final_step(self):
        """当前末步成功时，命令尾部及 worker 预分类的旧 SCF 失败不能重新污染终态。"""
        self.make_relaxed()
        for metadata in ({}, {"status": "technical_failed", "reason": "SCF 不收敛"}):
            with self.subTest(metadata=metadata):
                execution = {"returncode": 0, "output_tail": "convergence has not been achieved", **metadata}
                result = relax.run_relax(self.task, self.config, lambda label, argv, cwd: execution)
                self.assertEqual(result["status"], "success", result)

    def test_relax_nonzero_exit_and_oom_still_fail_with_complete_products(self):
        """末步和产物成功不能掩盖非零返回或明确 OOM，屏蔽旧 SCF 不会放宽这些失败。"""
        self.make_relaxed()
        cases = ({"returncode": 7, "output_tail": "convergence has not been achieved"},
                 {"returncode": 0, "output_tail": "OUT_OF_MEMORY"},
                 {"returncode": 7, "output_tail": "", "status": "technical_failed", "reason": "SCF 不收敛"})
        for execution in cases:
            with self.subTest(execution=execution):
                result = relax.run_relax(self.task, self.config, lambda label, argv, cwd: execution)
                self.assertEqual(result["status"], "technical_failed", result)

    def test_relax_input_change_conflicts(self):
        """声明输入世代变化不能沿用完成状态。"""
        self.make_relaxed()
        expected = self.relax_revision
        atomic_write_json(join_local(self.task, "task_manifest.json"), {"schema_version": 2,
                          "workflow_version": "0.0.2", "status": "success", "input_revision_id": new_revision_id()})
        self.assertEqual(relax.inspect_relax(self.task, self.config, expected)["status"], "input_failed")

    def test_relax_duplicate_sources_rejected(self):
        """多个末态来源明确拒绝，不任取第一个。"""
        self.make_relaxed()
        atomic_write_text(join_local(self.task, "cell-relax", "OUT.OTHER", "STRU_ION_D"), self.text)
        self.assertEqual(relax.inspect_relax(self.task, self.config)["status"], "input_failed")

    def test_symmetry_primitive_and_resources(self):
        """真实 spglib 理想化产物可读取，保留私人资源绝对路径文本。"""
        self.make_relaxed()
        result = zstar.prepare_zstar(self.task, {**self.config, "_revision_entry": None})
        self.assertEqual(result["status"], "pending", result)
        self.assertEqual(result["symmetry"]["output_group"], 221)
        output = symmetry.read_structure(join_local(self.task, "zstar", "STRU"))
        self.assertEqual(len(output), 1)
        self.assertEqual(result["symmetry"]["symprec"], 0.001)
        with open(join_local(self.task, "zstar", "STRU"), encoding="utf-8") as handle:
            self.assertIn("/cluster/private/Si.upf", handle.read())
        self.assertFalse(os.path.exists(join_local(project_root(), "ase_sort.dat")))

    def test_zstar_prepare_accepts_abacus_move_marker(self):
        """真实末态的 m 1 1 1 能进入原胞准备；手写 STRU 不调用 ASE writer 或生成排序文件。"""
        directory = self.make_relaxed()
        # 初始 STRU 保持 ASE 风格的裸标志，仅末态模拟 ABACUS 原生输出。
        atomic_write_text(join_local(directory, "OUT.ABACUS", "STRU_ION_D"),
                          self.text.replace("0 0 0 1 1 1", "0 0 0 m 1 1 1"))
        with patch("ase.io.write", side_effect=AssertionError("对称化 STRU 不应调用 ASE writer")):
            result = zstar.prepare_zstar(self.task, {**self.config, "_revision_entry": None})
        self.assertEqual(result["status"], "pending", result)
        self.assertEqual(result["symmetry"]["output_group"], 221)
        with open(join_local(self.task, "zstar", "STRU"), encoding="utf-8") as handle:
            written = handle.read()
        self.assertIn(" m 1 1 1", written)
        self.assertIn("/cluster/private/Si.upf", written)
        for _, _, files in os.walk(self.task):
            self.assertNotIn("ase_sort.dat", files)
        self.assertFalse(os.path.exists(join_local(project_root(), "ase_sort.dat")))

    def test_real_symmetry_lowering_flag_continues(self):
        """立方到四方真实降对称按 beta 理想化，FLAG 不拒绝。"""
        initial = Atoms("Si", scaled_positions=[[0, 0, 0]], cell=[4, 4, 4], pbc=True)
        relaxed = Atoms("Si", scaled_positions=[[0, 0, 0]], cell=[4, 4, 4.03], pbc=True)
        output, summary = symmetry.sanitize_structure(initial, relaxed, self.config)
        self.assertEqual(summary["initial_group"], 221)
        self.assertEqual(summary["output_group"], 123)
        self.assertEqual(summary["flag"], "symmetry_changed")
        self.assertEqual(len(output), 1)

    def test_conventional_cell_output_reduced_to_primitive(self):
        """金刚石常规胞经标准化后输出两原子原胞。"""
        from ase.build import bulk
        atoms = bulk("Si", "diamond", a=5.43, cubic=True)
        output, summary = symmetry.sanitize_structure(atoms, atoms.copy(), self.config)
        self.assertEqual(len(output), 2)
        self.assertNotIn("mapping", summary)

    def test_hard_geometry_errors(self):
        """非有限坐标和重叠都在计算前拒绝。"""
        for positions in ([[float("nan"), 0, 0]], [[0, 0, 0], [0, 0, 0]]):
            atoms = Atoms("Si" * len(positions), positions=positions, cell=[4, 4, 4], pbc=True)
            with self.subTest(positions=positions), self.assertRaises(IntegrityError):
                symmetry.validate_geometry(atoms)

    def test_lattice_qc_independent_of_mapping_affine(self):
        """晶格修正不能被恒等 Cartesian 映射隐藏。"""
        atoms = Atoms("Si", scaled_positions=[[0, 0, 0]], cell=[4, 4.0005, 4], pbc=True)
        with self.assertRaisesRegex(IntegrityError, "lattice_correction"):
            symmetry.sanitize_structure(atoms, atoms.copy(), {**self.config, "max_symmetry_strain": 1e-8})

    def test_resume_does_not_resanitize(self):
        """已有计算续算不再次处理或覆盖 STRU，继续绑定原 relax 声明版本。"""
        directory = self.make_products()
        with open(join_local(directory, "STRU"), "rb") as handle:
            before = handle.read()
        with patch.object(zstar, "sanitize_structure", side_effect=AssertionError("must not sanitize")):
            result = zstar.prepare_zstar(self.task, self.config)
        self.assertEqual(result["status"], "pending")
        with open(join_local(directory, "STRU"), "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_minimum_products_complete_with_binding(self):
        """最小产物组合即可成功；无旧绑定时不接受历史产物。"""
        self.make_products()
        expected = self.zstar_revision
        self.assertEqual(zstar.inspect_zstar(self.task, self.config)["status"], "pending")
        self.assertEqual(zstar.inspect_zstar(self.task, self.config, expected)["status"], "success")

    def test_invalid_static_tensor(self):
        """JSON 非有限数值、布尔值和维数错误均拒绝。"""
        directory = self.make_products()
        expected = self.zstar_revision
        for text in ('{"tensor":[[NaN,0,0],[0,1,0],[0,0,1]]}', '{"tensor":[[true,0,0],[0,1,0],[0,0,1]]}', '{"tensor":[1,2,3]}'):
            atomic_write_text(join_local(directory, "dielectric_response", "static_response.json"), text)
            self.assertEqual(zstar.inspect_zstar(self.task, self.config, expected)["status"], "technical_failed")

    def test_physics_stop_separate_from_success(self):
        """明确带隙拒绝归类物理终止，不能混入成功。"""
        directory = self.make_products()
        atomic_write_json(join_local(directory, "0.no-move", "zstar_insulation.json"), {"insulating": False})
        expected = self.zstar_revision
        self.assertEqual(zstar.inspect_zstar(self.task, self.config, expected)["status"], "physical_rejected")

    def test_tensor_payload_nonfinite_or_gibberish_rejected(self):
        """BEC/BORN 不能只靠非空，拒绝非有限数值和没有张量载荷的文本。"""
        directory = self.make_products()
        expected = self.zstar_revision
        for name, text in (("BEC.dat", "1 Si NaN 0 0 0 1 0 0 0 1\n"),
                           ("BEC.rep.dat", "*    1 Si 1e999 0 0 0 1 0 0 0 1\n"),
                           ("BORN", "not a tensor\n")):
            self.make_products()
            atomic_write_text(join_local(directory, name), text)
            self.assertEqual(zstar.inspect_zstar(self.task, self.config, expected)["status"], "technical_failed")

    def test_legacy_starred_bec_and_born_factor_accepted(self):
        """legacy 星标、非注释表头和 Phonopy 换算因子格式仍可使用。"""
        directory = self.make_products()
        for name in ("BEC.dat", "BEC.rep.dat"):
            atomic_write_text(join_local(directory, name), "No. Atom xx xy xz yx yy yz zx zy zz\n*    1 Si 1D0 0 0 0 1 0 0 0 1\n")
        atomic_write_text(join_local(directory, "BORN"), "14.3996\n1 0 0 0 1 0 0 0 1\n1 0 0 0 1 0 0 0 1\n")
        expected = self.zstar_revision
        self.assertEqual(zstar.inspect_zstar(self.task, self.config, expected)["status"], "success")

    def test_scientific_setting_changes_identity(self):
        """修改 XC/kspacing 改变输入版本，修改 MPI ranks 不改变版本。"""
        self.make_products()
        expected = self.zstar_revision
        self.assertFalse(relax.revision_matches(self.task, {**self.config, "xc": "pbe"}, "zstar", expected))
        self.assertTrue(relax.revision_matches(self.task, {**self.config, "ntasks": 64}, "zstar", expected))

    def test_four_commands_continue_and_root_cause_survives(self):
        """SCF 失败后 post/static 都继续，后续零返回码不能掩盖主因。"""
        self.make_products()
        calls = []
        def callback(label, argv, cwd):
            """模拟 SCF 失败及两个下游缺输入错误。"""
            calls.append(label)
            return {"returncode": 2 if label == "run" else 0,
                    "output_tail": "SCF did not converge" if label == "run" else ""}
        result = zstar.run_zstar(self.task, self.config, callback)
        self.assertEqual(calls, ["pre", "run", "post", "static"])
        self.assertEqual(result["status"], "technical_failed")
        self.assertEqual(result["reason"], "SCF 不收敛")

    def test_pre_existing_is_delegated_and_all_commands_run(self):
        """已存在 pre 的非零提示交 native 处理，完整后续结果仍成功。"""
        self.make_products()
        calls = []
        def callback(label, argv, cwd):
            """模拟 native pre 已存在提示。"""
            calls.append(label)
            return {"returncode": 1 if label == "pre" else 0,
                    "reason": "Existing ensemble files were found" if label == "pre" else "",
                    "output_tail": "Existing ensemble files were found" if label == "pre" else ""}
        result = zstar.run_zstar(self.task, self.config, callback)
        self.assertEqual(len(calls), 4)
        self.assertEqual(result["status"], "success")

    def test_native_scf_reason_beats_generic_command_errors(self):
        """native 当前失败日志中的 SCF 主因优先于普通退出码或下游缺文件。"""
        directory = self.make_products()
        calls = []
        def callback(label, argv, cwd):
            """run 时写原生状态，模拟 stdout 只有泛化子命令退出码。"""
            calls.append(label)
            if label == "run":
                atomic_write_json(join_local(directory, ".zstar", "stages", "disp-001.json"), {
                    "name": "disp-001", "path": "/obsolete/cluster/path/disp-001", "status": "failed",
                    "scf": "running", "error": "Command failed with exit code 1"})
                atomic_write_text(join_local(directory, ".zstar", "logs", "disp-001.log"),
                                  "\n$ mpirun abacus\n!! convergence has not been achieved @_@\n")
            return {"returncode": 1 if label in ("pre", "run", "post") else 0,
                    "reason": "missing result", "output_tail": ""}
        # 本用例只验证根因优先级；固定运行起点，避免 Windows 新文件 mtime
        # 比 time.time() 略早的微秒精度差异使当前原生失败被误当作历史记录。
        with patch.object(zstar.time, "time", return_value=1.0):
            result = zstar.run_zstar(self.task, self.config, callback)
        self.assertEqual(result["reason"], "SCF 不收敛")
        self.assertEqual(result["step"], "run")
        self.assertEqual(calls, ["pre", "run", "post", "static"])
        self.assertEqual(len(result["supplementary"]), 2)

    def test_unknown_pre_failure_cannot_be_hidden_by_success(self):
        """仅广义 FileExistsError 不能自动视为 native 正常跳过。"""
        self.make_products()
        def callback(label, argv, cwd):
            """模拟与 ensemble 无关的 pre 锁文件失败。"""
            return {"returncode": 1 if label == "pre" else 0,
                    "output_tail": "FileExistsError: lock already exists" if label == "pre" else ""}
        result = zstar.run_zstar(self.task, self.config, callback)
        self.assertEqual(result["status"], "technical_failed")
        self.assertEqual(result["step"], "pre")

    def test_native_latest_failure_beats_stale_running(self):
        """陈旧 running 状态不覆盖最新已失败结构的状态。"""
        directory = self.make_products()
        stale = join_local(directory, ".zstar", "stages", "disp-001.json")
        atomic_write_json(stale, {"name": "disp-001", "status": "running", "scf": "running"})
        os.utime(stale, (1, 1))
        atomic_write_json(join_local(directory, ".zstar", "stages", "disp-002.json"), {
            "name": "disp-002", "status": "failed", "error": "Symmetry reconstruction failed"})
        result = zstar.native_status(self.task, self.config)
        self.assertTrue(result["step"].startswith("disp-002"))
        self.assertEqual(result["status"], "technical_failed")

    def test_technical_symmetry_error_not_physical(self):
        """zstar 对称性异常始终可技术续算。"""
        # ABACUS 的明确电荷密度否定结论属于 SCF 不收敛；缺结束标记仍单独分类。
        for text in ("charge density convergence is NOT achieved", "charge density convergence has not been achieved"):
            self.assertEqual(relax.classify_failure(text)["reason"], "SCF 不收敛")
        self.assertEqual(relax.classify_failure("[symm][ERROR] Symmetry reconstruction failed")["status"], "technical_failed")
        self.assertIsNone(relax.classify_failure("process exited with 137"))

    def test_slurm_resources_and_force_formal_tolerance(self):
        """并行资源来自 Slurm，pre/post 正式容差和 --force 保持统一。"""
        with patch.dict(os.environ, {"SLURM_NTASKS": "48", "SLURM_CPUS_PER_TASK": "2"}):
            commands = dict(zstar.zstar_command_set(self.config))
        self.assertIn("--force", commands["pre"])
        for key in ("pre", "post"):
            self.assertEqual(commands[key][commands[key].index("--symmprec") + 1], "0.001")
        self.assertEqual(commands["run"][commands["run"].index("--omp-threads") + 1], "2")
        self.assertIn("48", commands["run"][commands["run"].index("--abacus-command") + 1])

    def test_full_scan_loose_restoration_is_diagnostic_only(self):
        """全部五档扫描保留；宽松恢复初始群仍采用正式降对称原胞。"""
        initial = Atoms("Si", scaled_positions=[[0, 0, 0]], cell=[4, 4, 4], pbc=True)
        relaxed = Atoms("Si", scaled_positions=[[0, 0, 0]], cell=[4, 4, 4.03], pbc=True)
        output, summary = symmetry.sanitize_structure(initial, relaxed, {**self.config, "symmetry_scan_tolerances": [1e-3]})
        self.assertEqual(set(map(float, summary["symmetry_scan"])), {1e-5, 1e-4, 1e-3, 1e-2, 1e-1})
        self.assertEqual(summary["output_group"], 123)
        self.assertIn(1e-1, summary["loose_matches_initial"])
        self.assertIn("loose_tolerance_restores_initial_group", summary["flags"])
        self.assertEqual(len(output), 1)

    def test_metadata_transfer_requires_uniform_same_element_values(self):
        """删除映射后，同元素不同磁矩拒绝；统一标量字段按元素保留。"""
        output = Atoms("Si", scaled_positions=[[0, 0, 0]], cell=[4, 4, 4], pbc=True)
        source = self.text.replace("1\n0 0 0 1 1 1", "2\n0 0 0 1 1 1 mag 1\n.5 .5 .5 1 1 1 mag 2")
        with self.assertRaisesRegex(IntegrityError, "site_dependent_metadata"):
            symmetry.sanitized_stru_text(output, source, source, {})
        source = source.replace("mag 2", "mag 1")
        self.assertIn("mag 1", symmetry.sanitized_stru_text(output, source, source, {}))
        # 同元素一致不代表非有限磁矩具有物理含义，块标量与逐位点标量均应拒绝。
        for invalid in (source.replace("mag 1", "mag nan"), source.replace("mag 1", "mag inf"),
                        source.replace("\nSi\n0\n", "\nSi\nnan\n")):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(IntegrityError, "nonfinite_scalar_magnetism"):
                symmetry.sanitized_stru_text(output, invalid, invalid, {})

    def test_metadata_canonical_movement_and_scalar_preservation(self):
        """移动标志两种语法、标量别名与等价数值规范化后按元素保留有效属性。"""
        output = Atoms("Si", scaled_positions=[[0, 0, 0]], cell=[4, 4, 4], pbc=True)
        cases = (
            ("0", "1 1 1", "m 1 1 1", ["1", "1", "1"], 0.0),
            ("0", "0 0 0", "m 0 0 0", ["0", "0", "0"], 0.0),
            ("1.2500", "m 1 1 1", "1 1 1 mag 1.25e0", ["1", "1", "1"], 1.25),
            ("0", "m 1 1 1 mag 1.5000", "1 1 1 magmom 1.5e0", ["1", "1", "1"], 1.5),
            ("0", "magmom -0.000", "", ["1", "1", "1"], 0.0),
            ("2.0", "mag 2.000e0", "", ["1", "1", "1"], 2.0),
        )
        for block_mag, first, second, flags, expected_mag in cases:
            with self.subTest(block_mag=block_mag, first=first, second=second):
                source = self.text.replace("Si\n0\n1\n0 0 0 1 1 1",
                                           f"Si\n{block_mag}\n2\n0 0 0 {first}\n.5 .5 .5 {second}")
                written = symmetry.sanitized_stru_text(output, self.text, source, {})
                lines = [line.strip() for line in written.split("ATOMIC_POSITIONS", 1)[1].splitlines() if line.strip()]
                self.assertEqual(float(lines[2]), float(block_mag))
                extra = lines[4].split()[3:]
                self.assertEqual(extra[:4], ["m", *flags])
                self.assertIn(len(extra), (4, 6))
                # 显式磁矩等于块默认时允许省略；检查有效磁矩而非要求重复写零。
                effective_mag = float(lines[2])
                if len(extra) == 6:
                    self.assertEqual(extra[4], "mag")
                    effective_mag = float(extra[5])
                self.assertEqual(effective_mag, expected_mag)

    def test_metadata_rejects_unmapped_and_vector_properties(self):
        """语法兼容不放宽方向约束、位点差异、速度、角度及矢量磁矩的拒绝。"""
        output = Atoms("Si", scaled_positions=[[0, 0, 0]], cell=[4, 4, 4], pbc=True)
        cases = [
            ("m 1 0 1", "m 1 0 1", "directional_constraint_requires_frame_mapping"),
            ("0 1 1", "0 1 1", "directional_constraint_requires_frame_mapping"),
            ("m 1 1 1", "m 0 0 0", "site_dependent_metadata_requires_mapping"),
            ("m 1 1 1 mag 1", "1 1 1 magmom 2", "site_dependent_metadata_requires_mapping"),
        ]
        for label in ("v", "vel", "velocity"):
            # 即使是零速度也不暗中丢弃；mag 前后都须识别速度，不误报为三分量磁矩。
            for extra in (f"m 1 1 1 {label} 0 0 0 mag 1", f"m 1 1 1 mag 1 {label} 0 0 0"):
                cases.append((extra, extra, "unsupported_vector_site_metadata"))
        for label in ("angle1", "angle2"):
            extra = f"m 1 1 1 mag 1 {label} 0"
            cases.append((extra, extra, "unsupported_vector_site_metadata"))
        for label in ("mag", "magmom"):
            for vector in ("0 0 0", "1 2 3"):
                extra = f"m 1 1 1 {label} {vector}"
                cases.append((extra, extra, "unsupported_vector_magnetism"))
            for scalar in ("nan", "inf"):
                extra = f"m 1 1 1 {label} {scalar}"
                cases.append((extra, extra, "nonfinite_scalar_magnetism"))
        for first, second, reason in cases:
            with self.subTest(first=first, second=second), self.assertRaisesRegex(IntegrityError, reason):
                source = self.text.replace("Si\n0\n1\n0 0 0 1 1 1",
                                           f"Si\n0\n2\n0 0 0 {first}\n.5 .5 .5 {second}")
                symmetry.sanitized_stru_text(output, self.text, source, {})

    def test_no_readback_when_serializing_symmetry_result(self):
        """输出函数直接序列化，不调用 ASE 解析或重复原胞识别。"""
        output = Atoms("Si", scaled_positions=[[0, 0, 0]], cell=[4, 4, 4], pbc=True)
        with patch("ase.io.read", side_effect=AssertionError("no readback")), patch.object(symmetry, "symmetry_group", side_effect=AssertionError("no repeat")):
            self.assertIn("ATOMIC_POSITIONS", symmetry.sanitized_stru_text(output, self.text, self.text, {}))

    def test_custom_commands_are_not_given_extra_symmetry_arguments(self):
        """用户四命令原样展开，不补写 native 已默认的容差参数。"""
        config = {**self.config, "zstar_commands": {name: ["custom", name] for name in ("pre", "run", "post", "static")}}
        self.assertEqual(dict(zstar.zstar_command_set(config))["pre"], ["custom", "pre"])

    def test_standardized_output_has_no_second_group_identification(self):
        """只识别初始和 relaxed 五档，输出群字段明确只是正式目标声明。"""
        atoms = Atoms("Si", scaled_positions=[[0, 0, 0]], cell=[4, 4, 4], pbc=True)
        with patch.object(symmetry, "symmetry_group", wraps=symmetry.symmetry_group) as identify:
            _, summary = symmetry.sanitize_structure(atoms, atoms.copy(), self.config)
        self.assertEqual(identify.call_count, 6)
        self.assertEqual(summary["output_group_basis"], "declared_formal_target")

    def test_invalid_standardized_array_and_composition_rejected(self):
        """标准化返回坏形状或改变元素计量仍属硬错误，不靠重复空间群识别发现。"""
        atoms = Atoms("Si", scaled_positions=[[0, 0, 0]], cell=[4, 4, 4], pbc=True)
        for result in ((np.eye(3), [[0, 0]], [14]), (np.eye(3), [[0, 0, 0]], [14.5]),
                       (np.eye(3), [[0, 0, 0]], [8])):
            with self.subTest(result=result), patch("spglib.standardize_cell", return_value=result), self.assertRaises(IntegrityError):
                symmetry.sanitize_structure(atoms, atoms.copy(), self.config)

    def test_zstar_revision_tracks_selected_relax_revision(self):
        """选定 relax 目录产生另一 UUID 后，既有 zstar 关联明确失效。"""
        self.make_products()
        self.assertTrue(relax.revision_matches(self.task, self.config, "zstar", self.zstar_revision))
        changed = relax.new_stage_revision(self.task, self.config, "relax")
        atomic_write_json(join_local(self.temporary.name, ".hpc_runs", "relax", "zz-new", "plan.json"), {
            "schema_version": 2, "workflow_version": "0.0.2", "stage": "relax", "task_root": "../../..",
            "run_id": "zz-new", "execution": {}, "groups": [], "tasks": [{"path": "task", "status": "success", **changed}]})
        self.assertFalse(relax.revision_matches(self.task, self.config, "zstar", self.zstar_revision))

    def test_missing_stage_inputs_cannot_reuse_completed_products(self):
        """完整旧物性或日志不能掩盖阶段输入缺失，stat 检查不重读科学结构。"""
        self.make_products()
        os.remove(join_local(self.task, "zstar", "INPUT"))
        self.assertEqual(zstar.inspect_zstar(self.task, self.config, self.zstar_revision)["status"], "input_failed")
        os.remove(join_local(self.task, "cell-relax", "STRU"))
        self.assertEqual(relax.inspect_relax(self.task, self.config, self.relax_revision)["status"], "input_failed")

    def test_default_zstar_command_prefix_is_a_scientific_setting(self):
        """未使用四命令覆盖时更改默认前缀使版本不匹配，不能复用旧终态。"""
        self.make_products()
        self.assertFalse(relax.revision_matches(self.task, {**self.config, "zstar_command": ["other-zstar"]}, "zstar", self.zstar_revision))

    def test_science_and_qc_changes_require_new_directory(self):
        """命令任意参数及 QC 小设置均冻结，Slurm 动态资源保持可调。"""
        self.make_products()
        for changes in ({"abacus_command": ["wrapper", "--new-science"]},
                        {"pyatb_command": ["wrapper", "--new-science"]},
                        {"minimum_distance": 0.2}, {"max_symmetry_strain": 0.002},
                        {"symmetry_scan_tolerances": [1e-4, 1e-3]}):
            with self.subTest(changes=changes):
                self.assertFalse(relax.revision_matches(self.task, {**self.config, **changes}, "zstar", self.zstar_revision))
        self.assertTrue(relax.revision_matches(self.task, {**self.config, "ntasks": 128, "omp_threads": 4}, "zstar", self.zstar_revision))
        changed = {**self.config, "zstar_subdir": "zstar-new", "xc": "pbe", "_revision_entry": None}
        prepared = zstar.prepare_zstar(self.task, changed)
        self.assertEqual(prepared["status"], "pending", prepared)
        self.assertNotEqual(prepared["input_revision_id"], self.zstar_revision["input_revision_id"])
        self.assertTrue(os.path.isfile(join_local(self.task, "zstar", "STRU")))

    def test_formal_failure_still_finishes_all_diagnostic_tolerances(self):
        """正式档失败先完成五档诊断，再作为硬错误拒绝标准化。"""
        atoms = Atoms("Si", scaled_positions=[[0, 0, 0]], cell=[4, 4, 4], pbc=True)
        with patch.object(symmetry, "symmetry_group", side_effect=[221, 221, 221, IntegrityError("formal"), 221, 221]) as identify:
            with self.assertRaisesRegex(IntegrityError, "formal_spacegroup"):
                symmetry.sanitize_structure(atoms, atoms.copy(), self.config)
        self.assertEqual(identify.call_count, 6)

    def test_relax_revision_index_is_explicit_scoped_and_not_global(self):
        """同轮显式索引避免重复历史 I/O；缺索引/根不符时重新读取。"""
        self.make_relaxed()
        index = relax.build_relax_revision_index(self.temporary.name, "cell-relax")
        with patch.object(relax, "build_relax_revision_index", side_effect=AssertionError("repeated history read")):
            for _ in range(3):
                self.assertEqual(relax.selected_relax_revision_id(self.task, {**self.config, "_relax_revision_index": index}), self.relax_revision["input_revision_id"])
        with patch.object(relax, "build_relax_revision_index", wraps=relax.build_relax_revision_index) as build:
            relax.selected_relax_revision_id(self.task, self.config)
            relax.selected_relax_revision_id(self.task, {**self.config, "_relax_revision_index": {**index, "work_root": "other"}})
        self.assertEqual(build.call_count, 2)

    def test_invalid_slurm_resource_values_rejected(self):
        """Slurm 提供零或负资源值时不构造错误的真实 MPI 命令。"""
        for value in ("0", "-1"):
            with patch.dict(os.environ, {"SLURM_NTASKS": value}), self.assertRaises(ValueError):
                zstar.zstar_command_set(self.config)


if __name__ == "__main__":
    unittest.main()
