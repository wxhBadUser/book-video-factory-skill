from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT.parent / "skill/runtime/book_video_factory"


class PhaseThreeCliTests(unittest.TestCase):
    def _run(self, runtime: Path, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(runtime / "scripts" / script), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def test_all_four_clis_expose_expected_arguments_in_source_and_runtime(self) -> None:
        expected = {
            "build_visual_stage.py": ("--project", "--visual-input"),
            "register_visual_asset.py": ("--project", "--task-id", "--source", "--tool-call-id", "--prompt-sha256", "--style-reference-id", "--identity-reference-task-id"),
            "build_visual_review.py": ("--project",),
            "approve_visual_stage.py": ("--project", "--release-id", "--reviewer", "--decision-file"),
        }
        for runtime in (ROOT, RUNTIME):
            for script, markers in expected.items():
                with self.subTest(runtime=runtime, script=script):
                    completed = self._run(runtime, script, "--help")
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    for marker in markers:
                        self.assertIn(marker, completed.stdout)

    def test_clis_fail_closed_with_json_error_when_evidence_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            calls = (
                ("build_visual_stage.py", "--project", str(project), "--visual-input", str(root / "missing.json")),
                ("register_visual_asset.py", "--project", str(project), "--task-id", "A1", "--source", str(root / "missing.png"), "--tool-call-id", "call-real-required", "--prompt-sha256", "0" * 64),
                ("build_visual_review.py", "--project", str(project)),
                ("approve_visual_stage.py", "--project", str(project), "--release-id", "r1", "--reviewer", "human", "--decision-file", str(root / "missing-decision.json")),
            )
            for script, *args in calls:
                with self.subTest(script=script):
                    completed = self._run(ROOT, script, *args)
                    self.assertEqual(completed.returncode, 2, completed.stderr)
                    payload = json.loads(completed.stdout)
                    self.assertEqual(payload["status"], "failed")
                    self.assertTrue(payload["error"])


if __name__ == "__main__":
    unittest.main()
