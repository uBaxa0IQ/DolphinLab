"""Crash recovery of immutable run inputs; no model or database connection."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_research_storage  # noqa: F401 - establishes an isolated import environment

from dolphinlab.agents.session import ResearchSession
from dolphinlab.common.files import freeze_bytes


class MaterializationRecoveryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dolphinlab-materialize-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name, value in (
            ("DATA_ROOT", self.root / "uploads"),
            ("RUNS_ROOT", self.root / "runs"),
        ):
            self.enterContext(patch("dolphinlab.agents.session." + name, value))
        self.raw = b"month,amount\n2026-08,160000\n2026-09,170000\n"
        self.upload = self.root / "uploads/projects/project/inputs/sales"
        self.upload.parent.mkdir(parents=True)
        self.upload.write_bytes(self.raw)
        self.prior = self.root / "prior.json"
        self.prior.write_text('{"value":6.25}', encoding="utf-8")
        self.run = SimpleNamespace(
            id="run",
            project_id="project",
            task_description="Compare monthly revenue without causal claims",
            inputs=[
                {
                    "id": "input-id",
                    "name": "sales.csv",
                    "stored_name": "sales",
                    "sha256": hashlib.sha256(self.raw).hexdigest(),
                }
            ],
            project_context={
                "selected_results": [
                    {
                        "id": "previous-result",
                        "artifact_path": str(self.prior),
                        "sha256": hashlib.sha256(self.prior.read_bytes()).hexdigest(),
                    }
                ]
            },
        )
        self.task_dir = self.root / "runs/run/task"

    def materialize(self):
        return ResearchSession(None, None).materialize(self.run, None)

    def test_interruption_retries_without_rewriting_readonly_evidence(self):
        def interrupted(path, content):
            if path.name == "project-context.json":
                raise OSError("Simulated worker interruption")
            freeze_bytes(path, content)

        with patch("dolphinlab.agents.session.freeze_bytes", side_effect=interrupted):
            with self.assertRaises(OSError):
                self.materialize()
        self.assertFalse((self.task_dir / "task.md").exists())
        frozen = self.task_dir / "prior-result-1.json"
        modified = frozen.stat().st_mtime_ns
        task = self.materialize()
        self.assertTrue(task.is_file())
        self.assertEqual(frozen.stat().st_mtime_ns, modified)
        context = json.loads((self.task_dir / "project-context.json").read_text(encoding="utf-8"))
        self.assertEqual(context["selected_results"][0]["artifact_path"], str(frozen))

    def test_restart_uses_frozen_files_after_original_sources_are_removed(self):
        task = self.materialize()
        original = task.read_bytes()
        self.upload.unlink()
        self.prior.unlink()
        self.assertEqual(self.materialize().read_bytes(), original)

    def test_restart_rejects_tampered_local_evidence(self):
        self.materialize()
        for name in ("prior-result-1.json", "input-id_sales.csv"):
            with self.subTest(file=name):
                path = self.task_dir / name
                original = path.read_bytes()
                path.chmod(0o644)
                path.write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "Frozen input or historical evidence"):
                    self.materialize()
                path.write_bytes(original)
                path.chmod(0o444)

    def test_damaged_source_does_not_commit_a_partial_task(self):
        self.upload.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "Input file changed"):
            self.materialize()
        self.assertFalse((self.task_dir / "task.md").exists())
        self.upload.write_bytes(self.raw)
        self.assertTrue(self.materialize().is_file())

    def test_failed_atomic_commit_is_retried_without_partial_content(self):
        target = self.root / "immutable.json"
        with patch("dolphinlab.common.files.os.replace", side_effect=OSError("Interrupted")):
            with self.assertRaises(OSError):
                freeze_bytes(target, b'{"complete":true}')
        self.assertFalse(target.exists())
        freeze_bytes(target, b'{"complete":true}')
        self.assertEqual(json.loads(target.read_bytes()), {"complete": True})
