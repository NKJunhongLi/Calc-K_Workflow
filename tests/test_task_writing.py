"""真实 ASE 隔离写出、INPUT 分发、迁移复用和冲突保护的端到端小样本。"""

import csv
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from ase import Atoms
from ase.io import read, write
from ase.build import bulk

from pre_config import CONFIG
from hpc_workflow.common.atomic import atomic_write_json, read_json
from hpc_workflow.common.errors import ConfigError, ConflictError, IntegrityError
from hpc_workflow.common.paths import join_local, project_root
from hpc_workflow.preprocess.pipeline import run_pre
from hpc_workflow.preprocess.templates import DEFAULT_INPUT_BYTES
from hpc_workflow.preprocess.validation import validate_config
from hpc_workflow.preprocess.writer import inspect_existing, WriterSession, write_task
from hpc_workflow.resources.abacus import build_abacus_resources


def file_bytes(path):
    """只在测试中直接读取小型夹具字节，不使用摘要。"""
    with open(path, "rb") as handle:
        return handle.read()


class TaskWritingTests(unittest.TestCase):
    """全部可变内容位于新项目，测试不会写原 workflow 或真实集群。"""

    def setUp(self):
        root = join_local(project_root(), ".test-artifacts")
        os.makedirs(root, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.directory.cleanup)
        self.root = self.directory.name
        self.structures = join_local(self.root, "structures")
        os.makedirs(self.structures)
        self.cif = join_local(self.structures, "sample.cif")
        self.atoms = Atoms("MgO", scaled_positions=[[0, 0, 0], [0.5, 0.5, 0.5]], cell=[4, 4, 4], pbc=True)
        write(self.cif, self.atoms, format="cif")
        # 用户只提交结构父目录；元数据由真正读取的晶体产生，不建立输入 CSV。
        self.config = {**CONFIG, "structures_root": self.structures,
                       "work_root": join_local(self.root, "work")}

    def execute(self, name="run", config=None):
        return run_pre(config or self.config, join_local(self.root, name))

    def test_builtin_template_is_exact_copy(self):
        with open(join_local(project_root(), "INPUT-template"), "rb") as handle:
            self.assertEqual(DEFAULT_INPUT_BYTES, handle.read())

    def test_full_write_reuse_and_unchanged_main_cwd(self):
        cwd = os.getcwd()
        before = os.path.exists(join_local(project_root(), "ase_sort.dat"))
        result = self.execute("run1")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["tasks_created"], 1)
        second = self.execute("run2")
        self.assertEqual(second["tasks_reused"], 1)
        self.assertEqual(os.getcwd(), cwd)
        self.assertEqual(os.path.exists(join_local(project_root(), "ase_sort.dat")), before)
        directories = [name for name in os.listdir(self.config["work_root"]) if name.startswith("local__")]
        task = join_local(self.config["work_root"], directories[0])
        manifest = read_json(join_local(task, "task_manifest.json"))
        self.assertEqual(manifest["source_file"], "sample.cif")
        self.assertEqual(manifest["input_source"], "LOCAL")
        self.assertEqual(manifest["source_record_id"], "sample.cif")
        # 输入侧不写入不存在的编号、预测信息或原 CSV 空字段；post 自己补最终空列。
        for absent in ("mapping_file", "global_sample_id", "dft_priority", "source_metadata",
                       "nominal_k_available", "nominal_k_value", "energy_above_hull", "material_id"):
            self.assertNotIn(absent, manifest)
        self.assertEqual(set(os.listdir(task)), {"STRU", "INPUT", "primitive.cif", "task_manifest.json"})
        self.assertEqual(result["writer_process_starts"], 1)
        self.assertEqual(second["writer_process_starts"], 0)
        with open(join_local(task, "INPUT"), "rb") as handle:
            self.assertEqual(handle.read(), DEFAULT_INPUT_BYTES)
        with open(join_local(task, "STRU"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("/public/home/", text)
        self.assertNotIn("\\public\\", text)

    def test_moved_work_and_source_can_reuse(self):
        self.execute("first")
        moved = join_local(self.root, "moved_work")
        moved_source = join_local(self.root, "moved_structures")
        shutil.copytree(self.config["work_root"], moved)
        shutil.copytree(self.structures, moved_source)
        config = {**self.config, "work_root": moved, "structures_root": moved_source}
        with patch("hpc_workflow.preprocess.pipeline.software_versions", return_value={"python": "another_host"}):
            result = self.execute("moved_run", config)
        self.assertEqual(result["tasks_reused"], 1)

    def test_fcc_primitive_has_four_products_without_mapping(self):
        write(self.cif, bulk("NaCl", "rocksalt", a=5.64, cubic=True), format="cif")
        result = self.execute("fcc")
        self.assertEqual(result["tasks_created"], 1)
        task_name = next(name for name in os.listdir(self.config["work_root"]) if name.startswith("local__"))
        task = join_local(self.config["work_root"], task_name)
        manifest = read_json(join_local(task, "task_manifest.json"))
        self.assertEqual(manifest["primitive_nsites"], 2)
        self.assertFalse(os.path.exists(join_local(task, "atom_mapping.json")))

    def test_external_cif_edit_does_not_claim_content_verification(self):
        self.execute("first")
        task_name = next(name for name in os.listdir(self.config["work_root"]) if name.startswith("local__"))
        stru = join_local(self.config["work_root"], task_name, "STRU")
        digest = file_bytes(stru)
        self.atoms.positions[1, 0] += 0.1
        write(self.cif, self.atoms, format="cif")
        result = self.execute("changed")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["tasks_reused"], 1)
        self.assertEqual(file_bytes(stru), digest)
        with open(join_local(self.config["work_root"], "preprocess_manifest.csv"), encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(next(csv.DictReader(handle))["status"], "success")

    def test_complete_interrupted_staging_is_recovered(self):
        self.execute("first")
        name = next(name for name in os.listdir(self.config["work_root"]) if name.startswith("local__"))
        task = join_local(self.config["work_root"], name)
        staging = task + ".staging-" + "a" * 32
        # 此移动只针对本测试创建的目录，先核实绝对目标仍位于本测试范围。
        self.assertEqual(os.path.commonpath([task, staging, self.root]), self.root)
        os.rename(task, staging)
        result = self.execute("recover")
        self.assertEqual(result["tasks_recovered"], 1)
        self.assertTrue(os.path.isdir(task))
        self.assertFalse(os.path.exists(staging))

    def test_incomplete_staging_is_not_counted_as_published(self):
        self.execute("first")
        name = next(name for name in os.listdir(self.config["work_root"]) if name.startswith("local__"))
        staging = join_local(self.config["work_root"], name + ".staging-" + "b" * 32)
        os.mkdir(staging)
        with open(join_local(staging, "task_manifest.json"), "w", encoding="utf-8") as handle:
            handle.write("{\"status\":\"success\"}")
        result = self.execute("with_incomplete")
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_published_tasks"], 1)
        self.assertEqual(len(result["unpublished_staging"]), 1)

    def test_custom_input_failure_never_falls_back(self):
        with self.assertRaises(ConfigError):
            self.execute(config={**self.config, "input_file": join_local(self.root, "missing_INPUT")})
        self.assertFalse(os.path.exists(self.config["work_root"]))

    def test_resource_mapping_never_checks_resource_files(self):
        with patch("os.path.isfile", side_effect=AssertionError("不得探测资源")):
            resources = build_abacus_resources(self.config)
            resources.validate_elements(("Mg", "O", "Hf", "La"))

    def test_changed_resource_paths_conflict_without_overwriting_stru(self):
        # 先用默认资源发布真实任务，再模拟用户只修改字典中 O 的资源；旧任务不可复用。
        self.execute("original_resources")
        name = next(name for name in os.listdir(self.config["work_root"]) if name.startswith("local__"))
        task = join_local(self.config["work_root"], name)
        paths = [join_local(task, filename) for filename in os.listdir(task)]
        before = {path: file_bytes(path) for path in paths}
        from hpc_workflow.resources import abacus
        paired = abacus._resource_map(self.config["dojo_root"])
        paired["O"] = {"pp": "/another-library/O.upf", "basis": "/another-library/O.orb"}
        with patch.object(abacus, "_resource_map", return_value=paired):
            result = self.execute("changed_resources")
        self.assertEqual(result["status"], "partial_failed")
        self.assertEqual(result["tasks_failed"], 1)
        self.assertEqual(result["tasks_reused"], 0)
        self.assertEqual({path: file_bytes(path) for path in paths}, before)

    def test_mixed_official_and_private_resources_reach_real_stru(self):
        # 同一个任务混用 O 官方路径和 La/Hf 私人路径，验证稳定接口真实传入 ASE。
        atoms = Atoms("LaHfO2", scaled_positions=[[0, 0, 0], [0.5, 0.5, 0.5], [0.25, 0.25, 0.25], [0.75, 0.75, 0.75]], cell=[8, 8, 8], pbc=True)
        write(self.cif, atoms, format="cif")
        self.config["dojo_root"] = "/remote/test-dojo"
        resources = build_abacus_resources(self.config)
        result = self.execute("mixed_resources")
        self.assertEqual(result["tasks_created"], 1)
        name = next(name for name in os.listdir(self.config["work_root"]) if name.startswith("local__"))
        with open(join_local(self.config["work_root"], name, "STRU"), encoding="utf-8") as handle:
            stru = handle.read()
        self.assertEqual(resources.pp["O"], "/remote/test-dojo/Pseudopotential/O.upf")
        for symbol in ("O", "La", "Hf"):
            self.assertIn(resources.pp[symbol], stru)
            self.assertIn(resources.basis[symbol], stru)
        self.assertFalse(resources.pp["La"].startswith("/remote/test-dojo/"))
        self.assertFalse(resources.pp["Hf"].startswith("/remote/test-dojo/"))

    def test_modified_manifest_cannot_relabel_old_results(self):
        self.execute("first")
        name = next(name for name in os.listdir(self.config["work_root"]) if name.startswith("local__"))
        task = join_local(self.config["work_root"], name)
        path = join_local(task, "task_manifest.json")
        manifest = read_json(path)
        expected = manifest["input_description"]
        manifest["source_record_id"] = "other.cif"
        atomic_write_json(path, manifest)
        with self.assertRaises(ConflictError):
            inspect_existing(task, expected, name)

    def test_batch_reuses_one_writer_and_one_staging_index(self):
        from hpc_workflow.preprocess.writer import scan_staging
        # 两个不同材料共用一轮解释器，恢复索引只建一次。
        write(join_local(self.structures, "second.cif"), self.atoms, format="cif")
        with patch("hpc_workflow.preprocess.pipeline.scan_staging", wraps=scan_staging) as scanning:
            result = self.execute("batch")
        self.assertEqual(result["tasks_created"], 2)
        self.assertEqual(result["writer_process_starts"], 1)
        self.assertEqual(scanning.call_count, 1)

    def test_writer_recovers_after_process_exit_and_keeps_ordinary_errors_local(self):
        # 真实常驻进程：普通写出错误不重启，进程死亡仅令当前任务失败。
        resources = build_abacus_resources(self.config).mapping_for(("Mg", "O"))
        request = {"numbers": self.atoms.numbers.tolist(), "cell": self.atoms.cell.array.tolist(),
                   "scaled_positions": self.atoms.get_scaled_positions().tolist(),
                   "input_bytes": DEFAULT_INPUT_BYTES, "resources": resources}
        with WriterSession() as session:
            for name in ("first", "bad", "after_bad", "dead", "restarted"):
                os.mkdir(join_local(self.root, name))
            session.write({**request, "staging": join_local(self.root, "first")})
            pid = session.worker_pid
            with self.assertRaises(IntegrityError):
                session.write({**request, "resources": {}, "staging": join_local(self.root, "bad")})
            session.write({**request, "staging": join_local(self.root, "after_bad")})
            self.assertEqual(session.worker_pid, pid)
            self.assertEqual(session.starts, 1)
            session.process.terminate()
            session.process.join(timeout=5)
            with self.assertRaises(IntegrityError):
                session.write({**request, "staging": join_local(self.root, "dead")})
            self.assertIsNone(session.process)
            session.write({**request, "staging": join_local(self.root, "restarted")})
            self.assertEqual(session.starts, 2)

    def test_explicit_input_settings_change_requires_new_work_root(self):
        self.execute("first")
        custom = join_local(self.root, "custom_INPUT")
        with open(custom, "wb") as handle:
            handle.write(DEFAULT_INPUT_BYTES.replace(b"100", b"120", 1))
        result = self.execute("changed_input", {**self.config, "input_file": custom})
        self.assertEqual(result["tasks_failed"], 1)
        result = self.execute("new_input", {**self.config, "input_file": custom, "work_root": join_local(self.root, "new_work")})
        self.assertEqual(result["tasks_created"], 1)

    def test_legacy_manifest_is_not_reused(self):
        self.execute("first")
        name = next(name for name in os.listdir(self.config["work_root"]) if name.startswith("local__"))
        task = join_local(self.config["work_root"], name)
        manifest = read_json(join_local(task, "task_manifest.json"))
        manifest["workflow_version"] = "0.0.1"
        atomic_write_json(join_local(task, "task_manifest.json"), manifest)
        with self.assertRaises(ConflictError):
            inspect_existing(task, None, name)

    def test_bad_enum_is_configuration_error(self):
        with self.assertRaises(ConfigError):
            validate_config({**self.config, "primitive_method": []})


    def test_real_three_formats_prepare_from_geometry_without_touching_sources(self):
        """真实 CIF/STRU/POSCAR 共用 pre；约束和磁矩按明确政策归一化，源字节不变。"""
        from ase.constraints import FixAtoms
        # CIF 保留已有普通结构；另外两个格式含实际约束，ABACUS 再携带非零磁矩。
        constrained = self.atoms.copy()
        constrained.set_constraint(FixAtoms(indices=[0]))
        constrained.set_initial_magnetic_moments([2.0, -1.0])
        stru = join_local(self.structures, "STRU")
        resources = build_abacus_resources(self.config)
        # ASE-ABACUS 会在 CWD 建立排序副产物；夹具也必须限定在自己的临时目录。
        previous = os.getcwd()
        try:
            os.chdir(self.structures)
            write(stru, constrained, format="abacus", pp=resources.pp, basis=resources.basis)
        finally:
            os.chdir(previous)
        sort_file = join_local(self.structures, "ase_sort.dat")
        if os.path.isfile(sort_file):
            os.unlink(sort_file)
        poscar = join_local(self.structures, "POSCAR")
        write(poscar, constrained, format="vasp", direct=True)
        before = {name: file_bytes(join_local(self.structures, name)) for name in os.listdir(self.structures)}
        result = self.execute("three-formats")
        self.assertEqual(result["status"], "success", result)
        self.assertEqual(result["tasks_created"], 3)
        # 直接检查正式输出的实际属性，不能以在生产中也重建 Atoms 的假结果代替。
        tasks = [name for name in os.listdir(self.config["work_root"]) if name.startswith("local__")]
        self.assertEqual(len(tasks), 3)
        for name in tasks:
            directory = join_local(self.config["work_root"], name)
            atoms = read(join_local(directory, "STRU"), format="abacus")
            self.assertFalse(atoms.constraints)
            self.assertTrue(all(value == 0 for value in atoms.get_initial_magnetic_moments()))
            self.assertEqual(file_bytes(join_local(directory, "INPUT")), DEFAULT_INPUT_BYTES)
        self.assertEqual(before, {name: file_bytes(join_local(self.structures, name)) for name in before})

    def test_bad_file_is_recorded_while_direct_structures_continue(self):
        """不过滤普通文件名：混入 CSV 明确失败；子目录内的合法 CIF 不参与处理。"""
        # 非结构文件必须进入逐文件错误表，不能无声过滤；嵌套结构则按非递归契约跳过。
        with open(join_local(self.structures, "notes.csv"), "w", encoding="utf-8") as handle:
            handle.write("a,b\nnot,a structure\n")
        nested = join_local(self.structures, "nested")
        os.mkdir(nested)
        write(join_local(nested, "hidden.cif"), self.atoms, format="cif")
        result = self.execute("mixed")
        self.assertEqual(result["status"], "partial_failed", result)
        self.assertEqual(result["tasks_created"], 1)
        self.assertEqual(result["tasks_failed"], 1)
        with open(join_local(self.config["work_root"], "preprocess_failed.csv"), encoding="utf-8-sig", newline="") as handle:
            failures = list(csv.DictReader(handle))
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0]["source_record_id"], "notes.csv")
        self.assertNotIn("global_sample_id", failures[0])
        self.assertNotIn("csv_row_number", failures[0])

    def test_empty_directory_is_not_successful_preparation(self):
        """空输入根不能被报告为成功准备了任务，也不能启动无用 writer。"""
        empty = join_local(self.root, "empty")
        os.mkdir(empty)
        result = self.execute("empty-run", {**self.config, "structures_root": empty})
        self.assertNotEqual(result["status"], "success")
        self.assertEqual(result["tasks_created"], 0)
        self.assertEqual(result["writer_process_starts"], 0)

    def test_long_encoded_identity_fails_locally_and_keeps_other_file(self):
        """源文件名能落盘但安全编码超限时，只拒绝该文件，不能使正常文件失去执行机会。"""
        # 中文每字编码后明显变长；构造有效 CIF，避免用解析失败掩盖真正的命名边界。
        filename = "材料" * 20 + ".cif"
        write(join_local(self.structures, filename), self.atoms, format="cif")
        result = self.execute("long-name")
        self.assertEqual(result["status"], "partial_failed", result)
        self.assertEqual(result["tasks_created"], 1)
        self.assertEqual(result["tasks_failed"], 1)
        with open(join_local(self.config["work_root"], "preprocess_failed.csv"), encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source_record_id"], filename)
        self.assertIn("过长", rows[0]["reason"])


if __name__ == "__main__":
    unittest.main()
