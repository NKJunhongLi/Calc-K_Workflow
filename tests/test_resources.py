"""显式配对资源表的完整迁移、整表替换、无 I/O 和配置边界回归。"""

import ast
from contextlib import ExitStack
import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from pre_config import CONFIG
from hpc_workflow.common.config_loader import load_config
from hpc_workflow.common.errors import ConfigError
from hpc_workflow.common.paths import join_local, project_root
from hpc_workflow.resources import abacus


class ResourceMappingTests(unittest.TestCase):
    """旧完整路径为独立基线，所有测试写入仅发生在新版测试沙箱。"""

    def setUp(self):
        # 基线来自重构前提交的实际映射，不用新实现反向生成期望值。
        path = join_local(project_root(), "tests", "resource_mapping_baseline.json")
        with open(path, encoding="utf-8-sig") as handle:
            self.baseline = json.load(handle)
        root = join_local(project_root(), ".test-artifacts")
        os.makedirs(root, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.directory.cleanup)

    def test_all_default_paths_exactly_match_previous_commit(self):
        resources = abacus.build_abacus_resources(CONFIG)
        self.assertEqual(len(resources.pp), 83)
        for field in ("pp", "basis"):
            self.assertEqual({key: os.path.basename(value) for key, value in getattr(resources, field).items()},
                             {key: os.path.basename(value) for key, value in self.baseline[field].items()})

    def test_new_dojo_root_changes_only_67_official_elements(self):
        # 官方条目迁移根目录；私人资源必须逐字保持原来的绝对路径。
        current = abacus.build_abacus_resources({"dojo_root": "/cluster/official-library"})
        prefix = self.baseline["dojo_root"] + "/"
        reference = abacus.build_abacus_resources(CONFIG)
        official, private = [], []
        for symbol, old_pp in self.baseline["pp"].items():
            if old_pp.startswith(prefix):
                official.append(symbol)
                for field, mapping in (("pp", current.pp), ("basis", current.basis)):
                    self.assertTrue(self.baseline[field][symbol].startswith(prefix))
                    self.assertEqual(mapping[symbol], "/cluster/official-library/" + self.baseline[field][symbol][len(prefix):])
            else:
                private.append(symbol)
                self.assertEqual(current.pp[symbol], reference.pp[symbol])
                self.assertEqual(current.basis[symbol], reference.basis[symbol])
        self.assertEqual(len(official), 67)
        self.assertEqual(set(private), set("La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf".split()))

    def test_complete_absolute_replacement_needs_no_dojo_root(self):
        replacement = {"O": {"pp": "/SG15/O.upf", "basis": "/custom/O.orb"}}
        # 三种未设置形式都不能触发官方根的校验或拼接，也不能补回原83元素。
        for config in ({}, {"dojo_root": None}, {"dojo_root": ""}):
            with self.subTest(config=config), patch.object(abacus, "_resource_map", return_value=replacement), patch.object(abacus, "_dojo_path", side_effect=AssertionError("整表绝对路径不应访问官方库")):
                resources = abacus.build_abacus_resources(config)
                self.assertEqual(resources.pp, {"O": "/SG15/O.upf"})
                self.assertEqual(resources.basis, {"O": "/custom/O.orb"})
                with self.assertRaises(ConfigError):
                    resources.mapping_for(("Mg",))

    def test_editing_only_literal_dictionary_can_replace_whole_library(self):
        # 在内存中模拟用户只覆盖 return 字典，不修改构造器、验证器和下游接口。
        with open(abacus.__file__, encoding="utf-8-sig") as handle:
            tree = ast.parse(handle.read(), filename=abacus.__file__)
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_resource_map")
        statement = next(node for node in function.body if isinstance(node, ast.Return))
        replacement = {"U": {"pp": "/another-library/U.upf", "basis": "/another-library/U.orb"}}
        statement.value = ast.parse(repr(replacement), mode="eval").body
        ast.fix_missing_locations(tree)
        name = "hpc_workflow.resources._literal_replacement_fixture"
        module = types.ModuleType(name)
        module.__package__ = "hpc_workflow.resources"
        module.__file__ = abacus.__file__
        # dataclass 需要能在 sys.modules 找到声明模块；该临时注册在测试结束时恢复。
        with patch.dict(sys.modules, {name: module}):
            exec(compile(tree, abacus.__file__, "exec"), module.__dict__)
            for config in ({}, {"dojo_root": None}, {"dojo_root": ""}):
                resources = module.build_abacus_resources(config)
                self.assertEqual(resources.mapping_for(("U",)), {"pp": {"U": replacement["U"]["pp"]}, "basis": {"U": replacement["U"]["basis"]}})

    def test_new_element_can_be_added_without_83_element_limit(self):
        paired = abacus._resource_map(self.baseline["dojo_root"])
        paired["Og"] = {"pp": "/custom/Og.upf", "basis": "/custom/Og.orb"}
        with patch.object(abacus, "_resource_map", return_value=paired):
            resources = abacus.build_abacus_resources({})
        self.assertEqual(len(resources.pp), 84)
        self.assertEqual(resources.mapping_for(("Og",)), {"pp": {"Og": "/custom/Og.upf"}, "basis": {"Og": "/custom/Og.orb"}})

    def test_malformed_top_level_maps_are_configuration_errors(self):
        for value in (None, [], (), "not a resource map", {}):
            with self.subTest(value=value), patch.object(abacus, "_resource_map", return_value=value), self.assertRaises(ConfigError):
                abacus.build_abacus_resources({})

    def test_malformed_element_entries_are_configuration_errors(self):
        invalid = [
            {"Xx": {"pp": "/Xx.upf", "basis": "/Xx.orb"}},
            {1: {"pp": "/one.upf", "basis": "/one.orb"}},
            {"O": None}, {"O": ["/O.upf", "/O.orb"]},
            {"O": {"pp": "/O.upf"}}, {"O": {"basis": "/O.orb"}},
            {"O": {"pp": "/O.upf", "basis": "/O.orb", "extra": "/other"}},
        ]
        for paired in invalid:
            with self.subTest(paired=paired), patch.object(abacus, "_resource_map", return_value=paired), self.assertRaises(ConfigError):
                abacus.build_abacus_resources({})

    def test_malformed_paths_are_configuration_errors(self):
        for path in (None, 1, [], "", "relative/O.upf", r"D:\PP\O.upf", "/with space/O.upf", "/bad\x00/O.upf", "/root/../O.upf", "/root/./O.upf", "//ambiguous/O.upf", "/root\\O.upf"):
            for field in ("pp", "basis"):
                paired = {"O": {"pp": "/valid/O.upf", "basis": "/valid/O.orb"}}
                paired["O"][field] = path
                with self.subTest(path=path, field=field), patch.object(abacus, "_resource_map", return_value=paired), self.assertRaises(ConfigError):
                    abacus.build_abacus_resources({})

    def test_default_official_entries_require_explicit_linux_root(self):
        for root in (None, "", r"D:\dojo", "relative/dojo", "/bad root"):
            with self.subTest(root=root), self.assertRaises(ConfigError):
                abacus.build_abacus_resources({"dojo_root": root})

    def test_dojo_join_keeps_posix_root_and_rejects_escape(self):
        self.assertEqual(abacus._dojo_path("/cluster/dojo/", "Pseudopotential", "O.upf"), "/cluster/dojo/Pseudopotential/O.upf")
        for parts in ((), ("",), ("/outside/O.upf",), ("../O.upf",), ("Pseudopotential/./O.upf",), (r"Pseudopotential\O.upf",), (None,)):
            with self.subTest(parts=parts), self.assertRaises(ConfigError):
                abacus._dojo_path("/cluster/dojo", *parts)

    def test_unused_element_changes_do_not_change_selected_mapping(self):
        paired = abacus._resource_map(CONFIG["dojo_root"])
        original = abacus.build_abacus_resources(CONFIG).mapping_for(("Mg", "O"))
        # 同一任务没有使用的资源仍可维护，不应改变它已经绑定的实际元素映射。
        paired["Hf"] = {"pp": "/updated/Hf.upf", "basis": "/updated/Hf.orb"}
        with patch.object(abacus, "_resource_map", return_value=paired):
            self.assertEqual(abacus.build_abacus_resources({}).mapping_for(("Mg", "O")), original)

    def test_config_type_and_removed_resource_settings_are_rejected(self):
        for config in (None, [], "invalid"):
            with self.subTest(config=config), self.assertRaises(ConfigError):
                abacus.build_abacus_resources(config)
        for key in ("lanthanide_root", "hfo_root", "pp_overrides", "basis_overrides"):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                abacus.build_abacus_resources({"dojo_root": self.baseline["dojo_root"], key: None})

    def test_removed_settings_are_rejected_by_custom_config_loader(self):
        # CLI 自定义配置也必须拒绝旧入口，避免用户以为旧覆盖项仍然生效。
        for key in ("lanthanide_root", "hfo_root", "pp_overrides", "basis_overrides"):
            path = join_local(self.directory.name, key + ".py")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("CONFIG = " + repr({key: None}) + "\n")
            with self.subTest(key=key), self.assertRaises(ConfigError):
                load_config("pre", path)

    def test_resource_construction_and_selection_perform_no_file_io(self):
        # 预先取得当前文本期望值；资源函数期间连 stat/open 都禁止调用。
        expected = abacus.build_abacus_resources({"dojo_root": self.baseline["dojo_root"]}).mapping_for(("O", "La", "Hf"))
        with ExitStack() as stack:
            for target in ("builtins.open", "os.stat", "os.path.isfile", "os.path.isdir", "os.path.exists", "os.access"):
                stack.enter_context(patch(target, side_effect=AssertionError("资源映射不得探测文件：" + target)))
            resources = abacus.build_abacus_resources({"dojo_root": self.baseline["dojo_root"]})
            actual = resources.mapping_for(("O", "La", "Hf"))
        self.assertEqual(actual, expected)

    def test_literal_resource_table_has_no_duplicate_elements_or_fields(self):
        # Python 会静默覆盖重复字面键；静态检查使默认资源表的这类抄写错误可见。
        with open(abacus.__file__, encoding="utf-8-sig") as handle:
            tree = ast.parse(handle.read())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_resource_map")
        returns = [node for node in function.body if isinstance(node, ast.Return)]
        self.assertEqual(len(returns), 1)
        literal = returns[0].value
        self.assertIsInstance(literal, ast.Dict)
        symbols = [ast.literal_eval(key) for key in literal.keys]
        self.assertEqual(len(symbols), len(set(symbols)))
        self.assertEqual(set(symbols), set(self.baseline["pp"]))
        for symbol, entry in zip(symbols, literal.values):
            with self.subTest(symbol=symbol):
                self.assertIsInstance(entry, ast.Dict)
                fields = [ast.literal_eval(key) for key in entry.keys]
                self.assertEqual(len(fields), 2)
                self.assertEqual(set(fields), {"pp", "basis"})


if __name__ == "__main__":
    unittest.main()
