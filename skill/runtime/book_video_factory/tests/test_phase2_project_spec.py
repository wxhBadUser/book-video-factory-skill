from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from phase1_fixture_factory import build_phase1_inputs
from phase2_fixture_factory import build_bridge_input
from book_video_factory.hbg_bridge.project_spec import HbgProjectSpecError, build_project_spec
from book_video_factory.hbg_bridge.script_export import build_script_exports


class DummyHandoff:
    release_id = "r1"
    package_digest = "a" * 64
    release_text_sha256 = build_bridge_input()["release_text_sha256"]
    hbg_commit = "63aa262d88f18c6058b205c2dd582cf909b219a4"


class Phase2ProjectSpecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = build_phase1_inputs()["script"]
        self.bridge = build_bridge_input()
        self.exports = build_script_exports("老人与海", self.script, self.bridge["chapters"])
        self.project_contract = {
            "project_id": "old-man-and-the-sea",
            "book": {"title": "老人与海", "author": "欧内斯特·海明威"},
        }

    def test_builds_hbg_compatible_book_spec(self) -> None:
        spec = build_project_spec(self.project_contract, DummyHandoff(), self.bridge, self.exports)
        self.assertEqual(spec["projectType"], "classic-book-narration")
        self.assertEqual(spec["narration"]["provider"], "edge-tts")
        self.assertEqual(spec["narration"]["voice"], "zh-CN-YunjianNeural")
        self.assertEqual(len(spec["source"]["chapters"]), 4)
        self.assertEqual(spec["workflow"]["contentPackageDigest"], "a" * 64)
        self.assertEqual(spec["workflow"]["hbgUpstreamCommit"], DummyHandoff.hbg_commit)
        self.assertEqual(spec["opening"]["flashMedia"], "assets/opening/flash.mp4")

    def test_spec_does_not_declare_an_unproduced_quote_ledger(self) -> None:
        spec = build_project_spec(self.project_contract, DummyHandoff(), self.bridge, self.exports)
        self.assertNotIn("quoteLedger", spec["book"])

    def test_project_book_identity_must_match_bridge_reveal_context(self) -> None:
        contract = json.loads(json.dumps(self.project_contract, ensure_ascii=False))
        contract["book"]["title"] = "错误书名"
        with self.assertRaisesRegex(HbgProjectSpecError, "book title"):
            build_project_spec(contract, DummyHandoff(), self.bridge, self.exports)

    def test_no_absolute_paths_are_emitted(self) -> None:
        spec = build_project_spec(self.project_contract, DummyHandoff(), self.bridge, self.exports)
        serialized = json.dumps(spec, ensure_ascii=False)
        self.assertNotRegex(serialized, r"[A-Za-z]:[\\/]")
        self.assertNotIn('"/', serialized)

    def test_spec_loads_through_vendored_hbg_project_config(self) -> None:
        spec = build_project_spec(self.project_contract, DummyHandoff(), self.bridge, self.exports)
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "PROJECT_SPEC.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            module_uri = (ROOT.parent / "vendor/hbg-life-simulation/scripts/project_config.mjs").resolve().as_uri()
            code = f"import {{loadProjectSpec}} from {module_uri!r}; console.log(loadProjectSpec(process.cwd()).title);"
            result = subprocess.run(["node", "--input-type=module", "-e", code], cwd=project, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "老人与海")


if __name__ == "__main__":
    unittest.main()
