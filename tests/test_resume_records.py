"""原子记录、文件锁、秘密遮蔽和快照的回归。"""

import os
import tempfile
import unittest

from hpc_workflow.common.atomic import (
    FileLock, atomic_write_bytes, atomic_write_json, read_json,
)
from hpc_workflow.common.errors import ConflictError, IntegrityError
from hpc_workflow.common.paths import join_local, project_root
from hpc_workflow.records.manifest import new_revision_id, is_revision_id
from hpc_workflow.records.runs import create_run, finish_run


class RecordTests(unittest.TestCase):
    """确保失败、中断和重复执行不会隐藏已发布记录。"""

    def setUp(self):
        root = join_local(project_root(), ".test-artifacts")
        os.makedirs(root, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.root = self.temporary.name

    def test_lock_excludes_and_releases_after_exception(self):
        path = join_local(self.root, ".lock")
        with self.assertRaisesRegex(RuntimeError, "模拟中断"):
            with FileLock(path):
                with self.assertRaises(ConflictError):
                    with FileLock(path):
                        self.fail("重复进程进入锁")
                raise RuntimeError("模拟中断")
        self.assertFalse(os.path.exists(path))
        with FileLock(path):
            pass

    def test_snapshots_are_unique_and_secrets_hidden(self):
        first = create_run(self.root, "pre", {"api_key": "DO_NOT_PERSIST", "x": 1})
        second = create_run(self.root, "pre", {"api_key": "DO_NOT_PERSIST", "x": 1})
        self.assertNotEqual(first["directory"], second["directory"])
        finish_run(first, "failed", error="模拟失败")
        record_path = join_local(first["directory"], "run_manifest.json")
        with open(record_path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn("DO_NOT_PERSIST", text)
        self.assertEqual(read_json(record_path)["status"], "failed")

    def test_legal_configuration_sets_are_serializable(self):
        context = create_run(self.root, "pre", {"audit_labels": frozenset({"H2O", "CO2"})})
        payload = read_json(join_local(context["directory"], "run_manifest.json"))
        self.assertEqual(payload["config"]["audit_labels"], ["CO2", "H2O"])

    def test_revision_is_explicit_uuid_not_file_content(self):
        first, second = new_revision_id(), new_revision_id()
        self.assertNotEqual(first, second)
        self.assertTrue(is_revision_id(first))
        for value in (None, "a" * 64, "not-a-version", first.upper()):
            self.assertFalse(is_revision_id(value))
        context = create_run(self.root, "pre", {})
        record = read_json(join_local(context["directory"], "run_manifest.json"))
        self.assertEqual(record["workflow_version"], "0.0.2")
        self.assertEqual(record["schema_version"], 2)
        self.assertNotIn("config_fingerprint", record)

    def test_json_refuses_non_finite_before_publication(self):
        path = join_local(self.root, "record.json")
        atomic_write_json(path, {"ok": 1})
        with self.assertRaises(ValueError):
            atomic_write_json(path, {"bad": float("nan")})
        self.assertEqual(read_json(path), {"ok": 1})


if __name__ == "__main__":
    unittest.main()
