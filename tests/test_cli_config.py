"""命令入口、配置覆盖和双语法路径的无网络回归。"""

from contextlib import redirect_stderr, redirect_stdout
import io
import os
import tempfile
import unittest

import cli
from hpc_workflow.common.config_loader import load_config
from hpc_workflow.common.errors import ConfigError
from hpc_workflow.common.paths import (
    from_relative, join_local, path_style, project_root, resolve_local, to_relative,
)


class CliConfigTests(unittest.TestCase):
    """测试只在新项目 .test-artifacts 中建立临时文件。"""

    def setUp(self):
        self.root = join_local(project_root(), ".test-artifacts")
        os.makedirs(self.root, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.temporary.cleanup)

    def test_help_and_positional_commands(self):
        # 帮助无需依赖配置输入或科学计算库，也不创建任何运行输出。
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["help", "pre"]), 0)
        self.assertIn("--config", output.getvalue())
        self.assertEqual(cli.build_parser().parse_args(["pre"]).command, "pre")
        self.assertEqual(cli.build_parser().parse_args(["validate", "pre"]).workflow, "pre")

    def test_config_overlay_and_anchor(self):
        path = join_local(self.temporary.name, "custom.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("CONFIG = {'work_root': 'work_custom'}\n")
        config = load_config("pre", path)
        self.assertEqual(config["work_root"], join_local(self.temporary.name, "work_custom"))
        self.assertEqual(config["dataset_namespace"], "local")
        self.assertNotIn("metadata_csv", config)
        self.assertNotIn("input_source", config)

    def test_old_screening_and_handoff_commands_are_not_available(self):
        # 拆分后的入口不再暴露旧数据获取链；错误必须在参数解析阶段明确返回。
        invalid = (["screening"], ["pipeline"], ["validate", "screening"],
                   ["pre", "--from-screening", "old-handoff.json"])
        for args in invalid:
            with self.subTest(args=args), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                cli.build_parser().parse_args(args)
            self.assertEqual(error.exception.code, 2)

    def test_structure_root_requires_an_original_absolute_path(self):
        # structures_root 是用户明确提交的绝对目录，不能因配置文件位置而隐式合法化。
        path = join_local(self.temporary.name, "relative.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("CONFIG = {'structures_root': 'structures'}\n")
        with self.assertRaises(ConfigError):
            load_config("pre", path)

    def test_old_input_configuration_keys_are_rejected(self):
        # 已取消的 CSV、handoff 和 MP 模式不能被悄悄忽略或当作另一种输入模式。
        for key in ("metadata_csv", "mp_input_mode", "screening_handoff", "input_source", "structure_variant"):
            path = join_local(self.temporary.name, key + ".py")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("CONFIG = " + repr({key: None}) + "\n")
            with self.subTest(key=key), self.assertRaises(ConfigError):
                load_config("pre", path)

    def test_unknown_config_is_rejected(self):
        path = join_local(self.temporary.name, "bad.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("CONFIG = {'work_rooot': '/incorrect'}\n")
        with self.assertRaises(ConfigError):
            load_config("pre", path)

    def test_custom_syntax_error_has_configuration_context(self):
        path = join_local(self.temporary.name, "syntax.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("CONFIG = {\n")
        with self.assertRaisesRegex(ConfigError, "第 1 行"):
            load_config("pre", path)

    def test_windows_and_posix_remain_independent(self):
        # 纯字符串操作允许在 Windows CI 中检验 Linux 拼接，反向也相同。
        self.assertEqual(join_local(r"D:\tasks", "sample", "STRU"), r"D:\tasks\sample\STRU")
        self.assertEqual(join_local("/public/tasks", "sample", "STRU"), "/public/tasks/sample/STRU")
        self.assertEqual(from_relative(r"D:\tasks", "a/INPUT"), r"D:\tasks\a\INPUT")
        self.assertEqual(from_relative("/public/tasks", "a/INPUT"), "/public/tasks/a/INPUT")
        self.assertEqual(to_relative(r"D:\tasks\a\INPUT", r"D:\tasks"), "a/INPUT")
        self.assertEqual(resolve_local("a", "/root/config"), "/root/config/a")

    def test_unsafe_or_ambiguous_references(self):
        for bad in ("../outside", "/absolute", "D:/absolute", r"a\b", "a//b"):
            with self.subTest(path=bad), self.assertRaises(ConfigError):
                from_relative("/tasks", bad)
        for bad in (r"C:relative", r"\rooted", "/root\\mixed", r"\\server", "//tmp/data"):
            with self.subTest(path=bad), self.assertRaises(ConfigError):
                path_style(bad)


if __name__ == "__main__":
    unittest.main()
