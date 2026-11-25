"""原胞转换回归：缩胞、旋转框架、轻量几何与显式失败策略。"""

import unittest
from unittest.mock import patch

import numpy as np
from ase import Atoms
from ase.build import bulk

from pre_config import CONFIG
from hpc_workflow.common.errors import IntegrityError
from hpc_workflow.preprocess.primitive import make_primitive


class PrimitiveMappingTests(unittest.TestCase):
    """测试真实 spglib 几何结果，不把其内部 primitive 编号当作输出顺序。"""

    def config(self, **changes):
        return {**CONFIG, **changes}

    def assert_primitive(self, source, result):
        self.assertEqual(result.source_nsites, len(source))
        self.assertGreater(len(result.atoms), 0)
        self.assertEqual(len(source) % len(result.atoms), 0)
        self.assertTrue(np.isfinite(result.atoms.positions).all())
        self.assertFalse(hasattr(result, "mapping"))

    def test_find_primitive_supercell(self):
        source = bulk("NaCl", "rocksalt", a=5.64, cubic=True).repeat((2, 1, 1))
        result = make_primitive(source, self.config())
        self.assertEqual(len(result.atoms), 2)
        self.assert_primitive(source, result)

    def test_rotated_nonorthogonal_structure_all_methods(self):
        source = Atoms("MgO", scaled_positions=[[0.12, 0.23, 0.34], [0.47, 0.64, 0.81]],
                       cell=[[4.1, 0.1, 0.2], [0.2, 4.5, 0.3], [0.4, 0.2, 4.9]], pbc=True)
        source.rotate(31, "x", rotate_cell=True)
        for method, ideal in (("find_primitive", True), ("standardize_cell", True), ("standardize_cell", False)):
            with self.subTest(method=method, no_idealize=ideal):
                result = make_primitive(source, self.config(primitive_method=method, standardize_no_idealize=ideal))
                self.assert_primitive(source, result)

    def test_primitive_failure_policy_is_explicit(self):
        source = bulk("NaCl", "rocksalt", a=5.64, cubic=True)
        with patch("spglib.find_primitive", return_value=None):
            with self.assertRaises(IntegrityError):
                make_primitive(source, self.config())
            result = make_primitive(source, self.config(primitive_failure_policy="use_input_cell"))
        self.assertTrue(result.used_input_cell_fallback)
        self.assertEqual(len(result.atoms), len(source))
        self.assert_primitive(source, result)

    def test_reidentified_canonical_axes_do_not_break_mapping(self):
        # 从真实ZrTl2CrO6问题样本提取的小型离线回归：两次dataset可能选择不同等价轴。
        source = Atoms(numbers=[40, 81, 81, 24, 8, 8, 8, 8, 8, 8],
                       cell=[[5.6433155, 0, 0], [2.8216568, 4.8872557, 0], [-0.0000018, 3.2581702, 4.758294]],
                       scaled_positions=[[.7827981, .0383587, .5641314], [.0278514, .5482515, .299292],
                                         [.5352379, .5334788, .821451], [.2832894, .0373762, .065605],
                                         [.4244659, .5474982, .3113839], [.6319909, .9327745, .3113834],
                                         [.9342262, .1391805, .8228745], [.0391907, .1402983, .3113843],
                                         [.1423631, .5273658, .8228751], [.5305496, .9310423, .8228755]], pbc=True)
        self.assert_primitive(source, make_primitive(source, self.config()))
    def test_conversion_never_reidentifies_symmetry(self):
        import spglib
        source = bulk("NaCl", "rocksalt", a=5.64, cubic=True)
        original = spglib.find_primitive
        with patch("spglib.find_primitive", wraps=original) as conversion, patch("spglib.get_symmetry_dataset", side_effect=AssertionError("禁止原胞复验")):
            result = make_primitive(source, self.config())
        self.assertEqual(conversion.call_count, 1)
        self.assertEqual(len(result.atoms), 2)

    def test_invalid_spglib_arrays_and_composition_rejected(self):
        source = bulk("NaCl", "rocksalt", a=5.64, cubic=True)
        invalid = [(np.eye(3), [[0, 0, 0]], [0]), (np.eye(3), [[0, 0, 0]], [11]),
                   (np.eye(3), [[float("nan"), 0, 0], [.5, .5, .5]], [11, 17])]
        for result in invalid:
            with self.subTest(result=result), patch("spglib.find_primitive", return_value=result), self.assertRaises((IntegrityError, ValueError)):
                make_primitive(source, self.config())

    def test_short_range_overlap_and_periodic_self_images(self):
        from hpc_workflow.preprocess.validation import validate_geometry
        for source in (Atoms("HH", positions=[[0, 0, 0], [.01, 0, 0]], cell=[4, 4, 4], pbc=True),
                       Atoms("H", positions=[[0, 0, 0]], cell=[.01, 4, 4], pbc=True)):
            with self.assertRaises(IntegrityError):
                validate_geometry(source, .1)

    def test_nonfinite_occupancy_is_not_full_occupancy(self):
        from types import SimpleNamespace
        from hpc_workflow.preprocess.validation import read_and_validate_structure
        from hpc_workflow.common.paths import join_local, project_root
        source = bulk("NaCl", "rocksalt", a=5.64)
        for value in (float("nan"), float("inf"), .5):
            source.info["occupancy"] = {"0": {"Na": value}}
            with self.subTest(value=value), patch("ase.io.read", return_value=source), self.assertRaises(IntegrityError):
                read_and_validate_structure(join_local(project_root(), "unused.cif"), self.config())


if __name__ == "__main__":
    unittest.main()
