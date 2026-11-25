"""后处理入口、路径、46列契约及不覆盖发布的回归。"""

from contextlib import redirect_stdout
import csv
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import cli
from hpc_workflow.common.errors import ConfigError, ConflictError, IntegrityError
from hpc_workflow.common.paths import join_local, project_root
from hpc_workflow.postprocess.config import load_config, output_path
from hpc_workflow.postprocess.writer import build_csv_row, build_fieldnames, write_results_csv


class PostConfigWriterTests(unittest.TestCase):
    """只在新版.test-artifacts沙箱创建输出，不使用真实工作根或资源库。"""

    def setUp(self):
        """建立独立输出沙箱，测试结束只清理本次临时目录。"""
        base = join_local(project_root(), ".test-artifacts")
        os.makedirs(base, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.temporary.cleanup)
        self.root = self.temporary.name
        self.output = join_local(self.root, "results.csv")

    def row(self, identity: str, value: float | str, source: str = "MP") -> dict:
        """构造一行合法部分结果；排序与源身份不借助被测排序器生成。"""
        task = {"task_name": identity, "metadata": {"input_source": source,
                "dataset_namespace": "batch", "source_record_id": identity,
                "global_sample_id": 123, "dft_priority": 2,
                "nominal_k_available": False, "zstar_relative_path": identity + "/zstar"},
                "expected_input_revision_id": "47d03cec-e51d-465e-a7f3-1b4027dc7f12", "provenance_status": "revision_linked"}
        values = {} if value == "" else {"dielectric_total_trace_mean": value}
        return build_csv_row(task, values)

    def read_output(self) -> list[dict]:
        """按普通CSV消费者读取已发布结果，验证BOM和引用规则。"""
        with open(self.output, encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle, strict=True))

    def test_exact_46_columns_and_original_order(self):
        """原39列不变，追加六项追溯和最后备注，不插入排序编号。"""
        first = ("dft_priority", "global_sample_id", "formula", "elements", "task_name", "band_gap_ev",
                 "nominal_k_available", "nominal_k_value", "dielectric_ionic_trace_mean", "dielectric_total_trace_mean")
        components = ("xx", "xy", "xz", "yx", "yy", "yz", "zx", "zy", "zz")
        original = first + tuple(f"dielectric_{kind}_{part}" for kind in ("total", "ionic", "electronic") for part in components)
        original += ("bec_inequivalent_atom_count", "born_effective_charges_json")
        fields = build_fieldnames()
        self.assertEqual(len(fields), 46)
        self.assertEqual(fields[:39], original)
        self.assertEqual(fields[-7:], ("input_source", "dataset_namespace", "source_record_id", "zstar_relative_path", "zstar_input_revision_id", "provenance_status", "remarks"))

    def test_numeric_sort_zero_negative_and_missing_last(self):
        """数值100必须排在9前，零和负值有效，缺失始终置底。"""
        rows = [self.row("missing", ""), self.row("nine", 9.0), self.row("hundred", 100.0),
                self.row("zero", 0.0), self.row("negative", -1.0)]
        write_results_csv(self.output, rows)
        self.assertEqual([row["task_name"] for row in self.read_output()], ["hundred", "nine", "zero", "negative", "missing"])

    def test_equal_values_use_identity_and_do_not_renumber_priority(self):
        """同值排序确定，原priority字段不随新行序改变。"""
        rows = [self.row("b", 20, "MJM"), self.row("a", 20, "MJM")]
        rows[0]["dft_priority"], rows[1]["dft_priority"] = 1, 3
        write_results_csv(self.output, rows)
        result = self.read_output()
        self.assertEqual([row["task_name"] for row in result], ["a", "b"])
        self.assertEqual([row["dft_priority"] for row in result], ["3", "1"])

    def test_mp_global_id_always_empty_and_false_not_unknown(self):
        """即使调用者传入MP样本号也清空；明确False保留字符串False。"""
        row = self.row("mp-one", 1)
        self.assertEqual(row["global_sample_id"], "")
        self.assertEqual(row["nominal_k_available"], "False")
        self.assertEqual(row["nominal_k_value"], "")
        self.assertEqual(row["remarks"], "")

    def test_scientific_parser_cannot_overwrite_metadata(self):
        """物性读取器不能覆盖源身份、priority或nominal预测。"""
        task = {"task_name": "x", "metadata": {}}
        for key in ("global_sample_id", "task_name", "dft_priority", "input_source", "nominal_k_value"):
            with self.subTest(key=key), self.assertRaises(IntegrityError):
                build_csv_row(task, {key: 123})

    def test_nonfinite_or_nonnumeric_sort_values_do_not_create_file(self):
        """NaN、Inf、布尔或数值字符串都不能进入排序/最终CSV。"""
        for value in (float("nan"), float("inf"), True, "100"):
            row = self.row("bad", 1)
            row["dielectric_total_trace_mean"] = value
            with self.subTest(value=value), self.assertRaises(IntegrityError):
                write_results_csv(self.output, [row])
            self.assertFalse(os.path.exists(self.output))

    def test_bom_and_json_roundtrip(self):
        """中文备注与包含逗号引号的全部原子JSON应无损通过标准CSV引用。"""
        row = self.row("x", 1)
        text = json.dumps([{"species": "Si", "z": [[1, 2, 3], [4, 5, 6], [7, 8, 9]]}])
        row.update(born_effective_charges_json=text, remarks="技术失败：测试, 保留部分结果")
        write_results_csv(self.output, [row])
        with open(self.output, "rb") as handle:
            self.assertEqual(handle.read(3), b"\xef\xbb\xbf")
        self.assertEqual(self.read_output()[0]["born_effective_charges_json"], text)

    def test_repeated_export_preserves_existing_bytes(self):
        """重复收集不得覆盖已确认的本批最终结果。"""
        write_results_csv(self.output, [self.row("first", 1)])
        with open(self.output, "rb") as handle:
            before = handle.read()
        with self.assertRaises(ConflictError):
            write_results_csv(self.output, [self.row("second", 100)])
        with open(self.output, "rb") as handle:
            self.assertEqual(handle.read(), before)

    def test_write_failure_leaves_no_partial_final_file(self):
        """落盘失败只清理自己临时文件，最终目标始终不存在。"""
        with patch("hpc_workflow.postprocess.writer.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                write_results_csv(self.output, [self.row("x", 1)])
        self.assertEqual(os.listdir(self.root), [])

    def test_publication_race_never_overwrites_winner(self):
        """另一个进程抢先发布时，保留胜者结果且清理本次临时文件。"""
        def competitor(source, destination):
            """模拟在真正发布前出现的已存在目标，而非模拟成功路径。"""
            with open(destination, "x", encoding="utf-8") as handle:
                handle.write("winner")
            raise FileExistsError(destination)

        method = "rename" if os.name == "nt" else "link"
        with patch("hpc_workflow.postprocess.writer.os." + method, side_effect=competitor):
            with self.assertRaises(ConflictError):
                write_results_csv(self.output, [self.row("loser", 1)])
        with open(self.output, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "winner")
        self.assertEqual(os.listdir(self.root), ["results.csv"])

    def test_config_anchor_default_output_and_unknown_legacy_key(self):
        """配置相对自身定位，默认一批一表，旧散装导入选项不被静默接受。"""
        path = join_local(self.root, "post_custom.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("CONFIG = {'work_root': '.', 'output_csv': None}\n")
        config = load_config(path)
        self.assertEqual(config["work_root"], self.root)
        self.assertEqual(output_path(config), join_local(self.root, "post", "zstar_results.csv"))
        self.assertFalse(os.path.exists(join_local(self.root, "post")))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("CONFIG = {'legacy_manifest': 'old.csv'}\n")
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_invalid_subdir_and_relative_traversal_rejected(self):
        """错误路径在读任务前被拒绝，不把其他批次当成本批结果。"""
        path = join_local(self.root, "post_custom.py")
        for override in ({"zstar_subdir": "../other"}, {"band_relative_root": "../other"}, {"output_csv": "data.json"}):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("CONFIG = " + repr({"work_root": self.root, **override}))
            with self.subTest(override=override), self.assertRaises(ConfigError):
                load_config(path)

    def test_cli_positional_post_default_validate_and_help(self):
        """后处理默认collect，validate为位置动作，帮助不加载工作根。"""
        self.assertEqual(cli.build_parser().parse_args(["post"]).action, "collect")
        self.assertEqual(cli.build_parser().parse_args(["post", "validate"]).action, "validate")
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(["help", "post"]), 0)
        self.assertIn("--config", output.getvalue())


if __name__ == "__main__":
    unittest.main()
