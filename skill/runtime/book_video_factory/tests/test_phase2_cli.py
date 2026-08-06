from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/build_hbg_bridge.py"

from phase2_fixture_factory import build_phase2_project, write_json


class Phase2CliTests(unittest.TestCase):
    def test_cli_builds_bridge_and_prints_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, _, payload = build_phase2_project(base, approve=True)
            input_path = base / "bridge.json"
            write_json(input_path, payload)
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--project", str(project), "--bridge-input", str(input_path)],
                capture_output=True, text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "created")
            self.assertEqual(len(result["bridge_digest"]), 64)

    def test_cli_failure_is_nonzero_and_does_not_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, _, _, payload = build_phase2_project(base, approve=False)
            input_path = base / "bridge.json"
            write_json(input_path, payload)
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--project", str(project), "--bridge-input", str(input_path)],
                capture_output=True, text=True,
            )
            self.assertNotEqual(completed.returncode, 0)
            error = json.loads(completed.stdout)
            self.assertIn("error", error)
            self.assertFalse((project / "02_story_script_故事脚本/HBG_BRIDGE_MANIFEST.json").exists())


if __name__ == "__main__":
    unittest.main()
