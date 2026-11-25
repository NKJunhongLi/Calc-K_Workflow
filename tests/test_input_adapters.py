"""本地结构输入回归：单层发现、顺序读取、稳定身份与必要几何检查。"""

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ase import Atoms
from ase.io import write

from pre_config import CONFIG
from hpc_workflow.common.errors import ConfigError, IntegrityError
from hpc_workflow.common.paths import join_local, project_root
from hpc_workflow.inputs.models import InputRecord
from hpc_workflow.preprocess.planner import build_plans, discover_inputs, task_id_for
from hpc_workflow.preprocess.validation import read_and_validate_structure, read_structure_safely, validate_config


class InputAdapterTests(unittest.TestCase):
    """保留原测试文件名；现在仅验证 LOCAL 文件输入，不导入已移除的 CSV 适配器。"""

    def setUp(self):
        # 所有输入由当前测试独占，读取顺序测试不会访问真实科学资源或项目外样例。
        parent = join_local(project_root(), ".test-artifacts")
        os.makedirs(parent, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.directory.cleanup)
        self.root = self.directory.name
        self.structures = join_local(self.root, "structures")
        os.mkdir(self.structures)
        self.atoms = Atoms("MgO", scaled_positions=[[0, 0, 0], [.5, .5, .5]], cell=[4, 4, 4], pbc=True)
        self.config = {**CONFIG, "structures_root": self.structures, "work_root": join_local(self.root, "work")}

    def test_discovery_is_sorted_single_level_and_has_no_filename_filter(self):
        # CSV、点文件和未知后缀同样是候选；子目录内的 CIF 不应递归进入本批。
        names = ("z.cif", "POSCAR", "notes.csv", ".hidden", "中文 空格.STRU")
        for name in names:
            with open(join_local(self.structures, name), "w", encoding="utf-8") as handle:
                handle.write("fixture")
        nested = join_local(self.structures, "nested")
        os.mkdir(nested)
        write(join_local(nested, "not-selected.cif"), self.atoms, format="cif")
        records, skipped = discover_inputs(self.structures)
        self.assertEqual([record.source_file for record in records], sorted(names))
        self.assertTrue(all(record.input_source == "LOCAL" for record in records))
        self.assertTrue(all(record.source_record_id == record.source_file for record in records))
        self.assertTrue(any(item["name"] == "nested" for item in skipped))

    def test_real_cif_without_extension_uses_explicit_first_reader(self):
        # 不依赖扩展名的 CIF 是用户所选“无文件名过滤”契约的直接应用。
        path = join_local(self.structures, "结构样例")
        write(path, self.atoms, format="cif")
        actual = read_and_validate_structure(path, self.config)
        self.assertEqual(actual.get_chemical_symbols(), ["Mg", "O"])
        self.assertEqual(len(actual), 2)

    def test_reader_order_stops_after_first_success(self):
        # 第一、二、三次成功分别只产生相应次数的 read 调用，自动识别永远排最后。
        for failures in range(3):
            attempts = [ValueError("not this format")] * failures + [self.atoms]
            with self.subTest(failures=failures), patch("ase.io.read", side_effect=attempts) as reader:
                actual = read_structure_safely(join_local(self.structures, "any-file"))
            self.assertIs(actual, self.atoms)
            self.assertEqual([call.kwargs.get("format") for call in reader.call_args_list],
                             ["cif", "abacus", None][:failures + 1])

    def test_reader_reports_every_parser_exception(self):
        # 诊断必须保留每个解析器自己的异常类型，不能把 CIF 错误类型写成自动识别错误。
        failures = [ValueError("cif detail"), KeyError("abacus detail"), RuntimeError("auto detail")]
        with patch("ase.io.read", side_effect=failures) as reader, self.assertRaises(RuntimeError) as raised:
            read_structure_safely(join_local(self.structures, "broken.csv"))
        message = str(raised.exception)
        for detail in ("ValueError", "cif detail", "KeyError", "abacus detail", "RuntimeError", "auto detail"):
            self.assertIn(detail, message)
        self.assertEqual(reader.call_count, 3)

    def test_bad_geometry_does_not_trigger_another_parser(self):
        # 解析成功后的体积错误属于科学输入失败，不能通过其他格式绕过检查。
        bad = Atoms("Si", positions=[[0, 0, 0]], cell=[0, 0, 0], pbc=True)
        with patch("ase.io.read", return_value=bad) as reader, self.assertRaises(IntegrityError):
            read_and_validate_structure(join_local(self.structures, "bad.cif"), self.config)
        self.assertEqual(reader.call_count, 1)

    def test_nonfinite_occupancy_and_overlap_are_rejected(self):
        # 失效占位和重叠仍属于必要物理检查；新输入来源不能放松这两类旧门槛。
        for occupancy in (.5, float("nan"), float("inf")):
            atoms = self.atoms.copy()
            atoms.info["occupancy"] = {"0": {"Mg": occupancy}}
            with self.subTest(occupancy=occupancy), patch("ase.io.read", return_value=atoms), self.assertRaises(IntegrityError):
                read_and_validate_structure(join_local(self.structures, "occupancy.cif"), self.config)
        overlap = Atoms("HH", positions=[[0, 0, 0], [.01, 0, 0]], cell=[4, 4, 4], pbc=True)
        with patch("ase.io.read", return_value=overlap), self.assertRaises(IntegrityError):
            read_and_validate_structure(join_local(self.structures, "overlap.cif"), self.config)

    def test_source_constraints_and_magnetism_are_normalized(self):
        # 用户明确选择统一自由移动和非磁性，不建立位点映射或传递 Selective Dynamics。
        from ase.constraints import FixAtoms
        source = self.atoms.copy()
        source.set_constraint(FixAtoms(indices=[0]))
        source.set_initial_magnetic_moments([2, -1])
        with patch("ase.io.read", return_value=source):
            normalized = read_and_validate_structure(join_local(self.structures, "POSCAR"), self.config)
        self.assertFalse(normalized.constraints)
        self.assertTrue(all(value == 0 for value in normalized.get_initial_magnetic_moments()))

    def test_identity_keeps_extension_and_is_not_based_on_formula_or_index(self):
        # 一个目录中的完整文件名就是相对身份；同 stem 不同格式不能覆盖同一个任务。
        first = InputRecord("LOCAL", "sample.cif", "sample.cif")
        second = InputRecord("LOCAL", "sample.STRU", "sample.STRU")
        self.assertNotEqual(task_id_for(first, "local"), task_id_for(second, "local"))
        self.assertNotEqual(task_id_for(first, "local"), task_id_for(first, "another"))
        plans = build_plans([first, second], self.config)
        self.assertEqual(plans[0]["source_file"], join_local(self.structures, "sample.cif"))
        self.assertEqual(os.path.dirname(plans[0]["task_directory"]), self.config["work_root"])
        self.assertFalse(os.path.exists(self.config["work_root"]))
        # 异常路径组成也只属于单条输入；后续正常文件仍须保留在可执行计划中。
        invalid = InputRecord("LOCAL", "a:b.cif", "a:b.cif")
        mixed = build_plans([invalid, first], self.config)
        self.assertTrue(mixed[0].get("planning_error"))
        self.assertEqual(mixed[1]["source_file"], join_local(self.structures, "sample.cif"))

    def test_casefold_identity_collision_is_rejected_before_writing(self):
        # 使用内存记录模拟 Linux 可见但 Windows 冲突的文件，避免依赖本机是否大小写敏感。
        records = [InputRecord("LOCAL", name, name) for name in ("Sample.cif", "sample.cif")]
        with self.assertRaises(ConfigError):
            build_plans(records, self.config)
        self.assertFalse(os.path.exists(self.config["work_root"]))

    def test_stable_directory_encoding_has_no_separator_collision(self):
        # 保留既有可逆编码的碰撞断言；新 LOCAL 来源同样不能与 staging 保留名称混淆。
        first = SimpleNamespace(input_source="LOCAL", source_record_id="b__c")
        second = SimpleNamespace(input_source="LOCAL", source_record_id="c")
        self.assertNotEqual(task_id_for(first, "a"), task_id_for(second, "a__b"))
        self.assertNotIn(".staging-", task_id_for(second, "batch.staging-trial"))

    def test_input_and_output_trees_are_isolated(self):
        """相同根及双向祖先关系都拒绝；Windows 大小写变体不能绕过重叠判断。"""
        # 这些检查只改变内存配置，不需要创建任何输出目录来证明目录包含关系。
        roots = [self.structures, join_local(self.structures, "work"), self.root]
        if os.name == "nt":
            roots.append(join_local(self.structures.upper(), "work"))
        for work_root in roots:
            with self.subTest(work_root=work_root), self.assertRaises(ConfigError):
                validate_config({**self.config, "work_root": work_root})


if __name__ == "__main__":
    unittest.main()
