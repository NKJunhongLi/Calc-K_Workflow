"""后处理科学解析回归：全部临时产物位于新版目录，外部旧样例仅只读。"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from hpc_workflow.common.paths import join_local, project_root
from hpc_workflow.postprocess import readers
from hpc_workflow.postprocess.models import DataValidationError


class PostReaderTests(unittest.TestCase):
    """验证原始方向、部分结果、严格物理元数据及错误隔离。"""

    def setUp(self):
        """在授权项目内建立独立临时目录；测试后删除自身的临时产物。"""
        root = join_local(project_root(), ".test-artifacts")
        os.makedirs(root, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.directory = self.temporary.name
        self.tensor = [[12.0, -2.0, 3.0], [4.0, 15.0, -6.0], [7.0, 8.0, 18.0]]

    def write(self, name, content):
        """只在本测试临时根中写入指定相对名称的 UTF-8 夹具。"""
        path = join_local(self.directory, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
        return path

    def static(self, **updates):
        """写出带完整原生物理元数据的总静态响应，支持逐项错误注入。"""
        payload = {"tensor": self.tensor, "frequency_cm-1": 0,
                   "response_kind": "total relative dielectric tensor", "response_unit": "1",
                   "response_convention": "relative permittivity", "electronic_response_included": True}
        payload.update(updates)
        return self.write("dielectric_response/static_response.json", json.dumps(payload))

    def products(self):
        """生成一套最小完整成功产物，张量刻意不对称以检查是否误转置。"""
        self.write("0.no-move/pyatb-band/Out/Band_Structure/band_info.dat", "Band gap (eV): 1.5D+0\n")
        self.write("0.no-move/pyatb-band/Out/Band_Structure/band.dat", "0 1 2\n")
        self.write("0.no-move/zstar_insulation.json", '{"insulating":true,"gap_eV":1.5,"threshold_eV":0.01}')
        self.write("BORN", "# epsilon\n2 0 0 0 3 0 0 0 4\n1 0 0 0 1 0 0 0 1\n")
        for name in ("BEC.dat", "BEC.rep.dat"):
            self.write(name, "# Z[displacement,polarization]\n4 O 1 2 3 4 5 6 7 8 9\n")
        self.static()

    def test_inline_examples_preserve_values_and_bec_order(self):
        """文件内夹具核对方向、全部不等价原子及原子顺序，不访问项目外历史样例。"""
        gap = self.write("band_info.dat", "Band gap (eV): 0.3294\n")
        self.assertAlmostEqual(readers.parse_band_gap(gap), 0.3294)
        total = readers.parse_total_tensor(self.static(tensor=[[20, -2, 0], [4, 21, 0], [0, 0, 24.35167505599555]]))
        electronic = readers.parse_electronic_tensor(self.write("BORN", "2 0 0 0 3 0 0 0 4\n"))
        result = readers.derive_dielectric_values(total, electronic)
        self.assertAlmostEqual(result.total_trace_mean, 21.78389168533185)
        records = "".join(f"{index} O 1 -1.36167530 0 -1.16527747 2 0 0 0 3\n" for index in (1, 2, 4, 5, 6, 7, 10))
        charges = readers.parse_born_charges(self.write("BEC.rep.dat", records))
        self.assertEqual([record.atom_index for record in charges], [1, 2, 4, 5, 6, 7, 10])
        self.assertEqual(charges[0].z_displacement_polarization[0, 1], -1.36167530)
        self.assertEqual(charges[0].z_displacement_polarization[1, 0], -1.16527747)
        self.assertFalse(result.ionic.flags.writeable)

    def test_fortran_numbers_and_invalid_number_strings(self):
        """接受 E/D 科学记数并拒绝非有限值、布尔、下划线和非 ASCII 文本。"""
        self.assertEqual(readers.parse_finite_float(" -1.25d+2 ", "test"), -125)
        for invalid in ("NaN", "Inf", "1e999", "True", "1_000", "", "１２", True):
            with self.subTest(invalid=invalid), self.assertRaises(DataValidationError):
                readers.parse_finite_float(invalid, "test")

    def test_band_label_missing_duplicate_or_malformed(self):
        """带隙字段必须唯一且严格匹配，不能静默选择某一个。"""
        for text in ("no gap", "Band gap (eV): 1\nBand gap (eV): 2\n",
                     "Band gap (eV): NaN", "Band gap (eV): 1 suffix"):
            with self.subTest(text=text), self.assertRaises(DataValidationError):
                readers.parse_band_gap(self.write("band_info.dat", text))

    def test_born_nine_components_and_optional_factor_and_matrix(self):
        """BORN 支持标准九分量行以及单位因子+三行矩阵，因子不乘电子张量。"""
        for text in ("# header\n1 2 3 4 5 6 7 8 9\n",
                     "14.3996\n1 2 3 4 5 6 7 8 9\n",
                     "14.3996\n1 2 3\n4 5 6\n7 8 9\n"):
            with self.subTest(text=text):
                value = readers.parse_electronic_tensor(self.write("BORN", text))
                self.assertEqual(value[1, 0], 4)
                self.assertEqual(value[0, 1], 2)

    def test_born_malformed_first_tensor_never_falls_back_to_later_atom(self):
        """错误首张量不能回退到后续原子 BEC，将其他物理量冒充电子介电。"""
        for text in ("1 2\n1 0 0 0 1 0 0 0 1", "1 2 3\n4 5\n7 8 9", "14.3996"):
            with self.subTest(text=text), self.assertRaises(DataValidationError):
                readers.parse_electronic_tensor(self.write("BORN", text))

    def test_static_preserves_asymmetry_negative_values_and_no_double_electronic(self):
        """直接读取总张量，不对称化、不转置、不取绝对值、不重复增加电子项。"""
        matrix = readers.parse_total_tensor(self.static())
        self.assertEqual(matrix[0, 1], -2)
        self.assertEqual(matrix[1, 0], 4)
        result = readers.derive_dielectric_values(matrix, np.eye(3) * 2)
        self.assertEqual(result.total_trace_mean, 15)
        self.assertEqual(result.ionic_trace_mean, 13)
        self.assertEqual(result.ionic[0, 1], -2)

    def test_static_requires_exact_physical_metadata(self):
        """有限频率、离子项、二维响应、错误单位及字符串/布尔频率都不进入总介电列。"""
        updates = ({"frequency_cm-1": 1}, {"frequency_cm-1": "0"}, {"frequency_cm-1": False},
                   {"response_kind": "ionic relative dielectric tensor"}, {"response_unit": "angstrom"},
                   {"response_convention": "polarizability"}, {"electronic_response_included": False},
                   {"electronic_response_included": "True"}, {"dimensionality": 2}, {"dimensionality": 3.0})
        for update in updates:
            with self.subTest(update=update), self.assertRaises(DataValidationError):
                readers.parse_total_tensor(self.static(**update))

    def test_static_rejects_duplicate_keys_nonfinite_and_wrong_component_types(self):
        """JSON 的重复键、非有限数值、字符串、布尔和错误形状均给出受控数据错误。"""
        path = self.write("response.json", '{"tensor": [], "tensor": []}')
        with self.assertRaises(DataValidationError):
            readers.parse_total_tensor(path)
        for tensor in ([[True, 0, 0], [0, 1, 0], [0, 0, 1]], [["1", 0, 0], [0, 1, 0], [0, 0, 1]],
                       [[float("nan"), 0, 0], [0, 1, 0], [0, 0, 1]], [1, 2, 3], [[10 ** 1000, 0, 0], [0, 1, 0], [0, 0, 1]]):
            with self.subTest(tensor=str(tensor)[:30]), self.assertRaises(DataValidationError):
                readers.parse_total_tensor(self.static(tensor=tensor))

    def test_bec_legacy_star_markers_and_uncommented_header(self):
        """兼容旧版未注释表头、紧贴或分离的星标，并保留原不连续编号。"""
        path = self.write("BEC.rep.dat", "No. Atom xx xy xz yx yy yz zx zy zz\n"
                          "*4 O 1 2 3 4 5 6 7 8 9\n*   1 Si 9 8 7 6 5 4 3 2 1\n")
        records = readers.parse_born_charges(path)
        self.assertEqual([item.atom_index for item in records], [4, 1])
        payload = json.loads(readers.serialize_born_charges(records))
        self.assertEqual(payload[0]["z_displacement_polarization"][1][0], 4)

    def test_bec_rejects_duplicate_zero_index_unknown_lines_and_bad_tensor(self):
        """BEC 不允许重复编号、零号、错误数据行、任意说明行或不完整张量。"""
        row = "1 O 1 0 0 0 1 0 0 0 1\n"
        for content in (row + row, "0 O 1 0 0 0 1 0 0 0 1", "unsupported header\n" + row,
                        "1 O 1 2 3", row + "No. Atom xx xy xz yx yy yz zx zy zz\n", "# only header",
                        "1 Xx 1 0 0 0 1 0 0 0 1"):
            with self.subTest(content=content), self.assertRaises(DataValidationError):
                readers.parse_born_charges(self.write("BEC.rep.dat", content))

    def test_full_reader_returns_32_physical_fields_without_errors(self):
        """完整输入得到带隙、27 分量、2 均值、BEC 数量和 JSON 共 32 个物性字段。"""
        self.products()
        result = readers.read_task_data(self.directory, {})
        self.assertEqual(result["errors"], [])
        self.assertEqual(len(result["values"]), 32)
        self.assertEqual(result["values"]["dielectric_total_trace_mean"], 15)
        self.assertEqual(result["values"]["dielectric_ionic_trace_mean"], 12)
        self.assertEqual(result["values"]["dielectric_total_yx"], 4)
        self.assertEqual(len(result["evidence"]), 7)
        self.assertIs(result["insulation"], True)

    def test_native_insulation_false_preserves_band_gap_without_another_error(self):
        """明确绝缘拒绝是有效原生结论，不丢弃带隙，也不当作 JSON 解析失败。"""
        self.products()
        self.write("0.no-move/zstar_insulation.json", '{"insulating":false,"gap_eV":0,"threshold_eV":0.01}')
        result = readers.read_task_data(self.directory, {})
        self.assertIs(result["insulation"], False)
        self.assertEqual(result["values"]["band_gap_ev"], 1.5)
        self.assertEqual(result["errors"], [])

    def test_native_insulation_requires_strict_boolean_and_finite_optional_values(self):
        """绝缘结论不能从字符串、整数或非有限元数据猜测。"""
        for payload in ({"insulating": "False"}, {"insulating": 1}, {},
                        {"insulating": True, "gap_eV": float("inf")},
                        {"insulating": True, "gap_eV": 10 ** 1000}):
            path = self.write("zstar_insulation.json", json.dumps(payload))
            with self.subTest(payload=str(payload)[:80]), self.assertRaises(DataValidationError):
                readers.parse_insulation(path)

    def test_partial_total_independent_of_electronic_bec_and_band(self):
        """只存在静态总张量时仍可提取总分量与排序值，其他字段不伪造零。"""
        self.static()
        result = readers.read_task_data(self.directory, {})
        self.assertEqual(result["values"]["dielectric_total_trace_mean"], 15)
        self.assertNotIn("dielectric_ionic_trace_mean", result["values"])
        self.assertNotIn("bec_inequivalent_atom_count", result["values"])
        self.assertNotIn("band_gap_ev", result["values"])
        self.assertTrue(result["errors"])

    def test_partial_band_gap_retained_when_band_data_missing(self):
        """物理拒绝后的单独 band_info 仍能保留带隙，同时报告 band.dat 缺失。"""
        self.write("0.no-move/pyatb-band/Out/Band_Structure/band_info.dat", "Band gap (eV): 0\n")
        result = readers.read_task_data(self.directory, {})
        self.assertEqual(result["values"]["band_gap_ev"], 0)
        self.assertTrue(any("能带文件不完整" in error for error in result["errors"]))

    def test_ambiguous_band_pairs_are_not_selected_arbitrarily(self):
        """两套完整能带结果即使包含默认 Band_Structure 也必须报歧义。"""
        self.products()
        self.write("0.no-move/pyatb-band/Out/other/band_info.dat", "Band gap (eV): 99")
        self.write("0.no-move/pyatb-band/Out/other/band.dat", "0 1")
        result = readers.read_task_data(self.directory, {})
        self.assertNotIn("band_gap_ev", result["values"])
        self.assertTrue(any("无法唯一定位" in error for error in result["errors"]))
        self.assertEqual(result["values"]["dielectric_total_trace_mean"], 15)

    def test_configurable_band_root(self):
        """自定义相对 Out 根只改变本任务内部寻址，不自动改写为别的平台绝对地址。"""
        self.write("custom/Out/band_info.dat", "Band gap (eV): 3.25")
        self.write("custom/Out/band.dat", "0 1")
        result = readers.read_task_data(self.directory, {"band_relative_root": "custom/Out"})
        self.assertEqual(result["values"]["band_gap_ev"], 3.25)

    def test_nonempty_bec_dat_is_required_but_rep_partial_is_retained(self):
        """缺少 BEC.dat 时仍保留有效不等价 BEC，但完整性错误不会丢失。"""
        self.write("BEC.rep.dat", "5 Si 1 0 0 0 1 0 0 0 1")
        result = readers.read_task_data(self.directory, {})
        self.assertEqual(result["values"]["bec_inequivalent_atom_count"], 1)
        self.assertTrue(any("BEC.dat 读取失败" in error for error in result["errors"]))

    def test_ionic_overflow_retains_valid_total_and_electronic_fields(self):
        """相减真实溢出只丢失离子字段，已验证的总/电子原始数据继续保留。"""
        self.static(tensor=[[1e308, 0, 0], [0, 1e308, 0], [0, 0, 1e308]])
        self.write("BORN", "-1e308 0 0 0 -1e308 0 0 0 -1e308")
        result = readers.read_task_data(self.directory, {})
        self.assertAlmostEqual(result["values"]["dielectric_total_trace_mean"], 1e308)
        self.assertEqual(result["values"]["dielectric_electronic_xx"], -1e308)
        self.assertNotIn("dielectric_ionic_xx", result["values"])
        self.assertTrue(any("溢出" in error for error in result["errors"]))

    def test_source_file_changes_are_rejected(self):
        """读取前后签名变化必须拒绝本文件，不能生成混合时刻的科学数据。"""
        path = self.write("BORN", "1 0 0 0 1 0 0 0 1")
        actual = readers._signature
        calls = []
        def changed(info):
            """仅在最后一次核验伪造不同长度，模拟计算进程并发改写。"""
            result = actual(info)
            calls.append(result)
            return result if len(calls) != 3 else (result[0], result[1], result[2] + 1, result[3])
        with patch.object(readers, "_signature", side_effect=changed), self.assertRaises(DataValidationError):
            readers.parse_electronic_tensor(path)

    def test_symlink_parent_not_used_as_results(self):
        """目录链接不能将另一个任务结果借入当前结构；Windows 无权限时用等价检测注入。"""
        self.products()
        actual = os.path.islink
        response = join_local(self.directory, "dielectric_response")
        with patch.object(readers.os.path, "islink", side_effect=lambda path: path == response or actual(path)):
            result = readers.read_task_data(self.directory, {})
        self.assertNotIn("dielectric_total_trace_mean", result["values"])
        self.assertEqual(result["values"]["band_gap_ev"], 1.5)
        self.assertTrue(any("符号链接" in error for error in result["errors"]))

    def test_bec_rep_partial_survives_static_metadata_failure(self):
        """静态物理约定错误不应丢失已经有效的带隙和不等价原子电荷。"""
        self.products()
        self.static(response_unit="angstrom")
        result = readers.read_task_data(self.directory, {})
        self.assertEqual(result["values"]["band_gap_ev"], 1.5)
        self.assertEqual(result["values"]["bec_inequivalent_atom_count"], 1)
        self.assertNotIn("dielectric_total_trace_mean", result["values"])


if __name__ == "__main__":
    unittest.main()
