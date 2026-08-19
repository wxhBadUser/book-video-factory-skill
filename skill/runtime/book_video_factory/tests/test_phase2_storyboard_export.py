from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from phase1_fixture_factory import build_phase1_inputs
from phase2_fixture_factory import build_bridge_input
from book_video_factory.hbg_bridge.project_spec import build_project_spec
from book_video_factory.hbg_bridge.script_export import build_script_exports
from book_video_factory.hbg_bridge.storyboard_export import (
    HbgStoryboardExportError,
    export_storyboard_base,
)


class DummyHandoff:
    release_id = "r1"
    package_digest = "a" * 64
    release_text_sha256 = build_bridge_input()["release_text_sha256"]
    hbg_commit = "63aa262d88f18c6058b205c2dd582cf909b219a4"


class Phase2StoryboardExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = build_phase1_inputs()["script"]
        self.bridge = build_bridge_input()
        self.exports = build_script_exports("老人与海", self.script, self.bridge["chapters"])

    def test_exports_hbg_base_with_semantic_extensions(self) -> None:
        storyboard = export_storyboard_base(self.bridge, self.exports)
        self.assertEqual(len(storyboard), 10)
        first = storyboard[0]
        self.assertEqual(first["id"], "s001")
        self.assertEqual(first["beatId"], "B001")
        self.assertEqual(first["chapter"], 1)
        self.assertEqual(first["generationMode"], "2x2")
        self.assertEqual(first["participants"], {"count": 1, "allowed": ["C001"]})
        self.assertEqual(first["motion"], "hold")

    def test_high_risk_flag_requires_single_even_if_contract_bypassed(self) -> None:
        bridge = deepcopy(self.bridge)
        bridge["storyboard_beats"][0]["risk_flags"] = ["hands"]
        bridge["storyboard_beats"][0]["generation_mode"] = "2x2"
        with self.assertRaisesRegex(HbgStoryboardExportError, "single"):
            export_storyboard_base(bridge, self.exports)

    def test_unknown_anchor_fails(self) -> None:
        bridge = deepcopy(self.bridge)
        bridge["storyboard_beats"][0]["anchor_refs"] = ["C999"]
        with self.assertRaisesRegex(HbgStoryboardExportError, "anchor"):
            export_storyboard_base(bridge, self.exports)

    def test_vendored_hbg_storyboard_validator_accepts_output(self) -> None:
        storyboard = export_storyboard_base(self.bridge, self.exports)
        spec = build_project_spec(
            {"project_id": "old-man-and-the-sea", "book": {"title": "老人与海", "author": "欧内斯特·海明威"}},
            DummyHandoff(), self.bridge, self.exports,
        )
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            (project / "PROJECT_SPEC.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            (project / "SCRIPT.md").write_text(self.exports.script_markdown, encoding="utf-8")
            (project / "STORYBOARD_BASE.json").write_text(json.dumps(storyboard, ensure_ascii=False), encoding="utf-8")
            script = ROOT.parent / "vendor/hbg-life-simulation/scripts/build_storyboard_base.mjs"
            result = subprocess.run(["node", str(script)], cwd=project, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            normalized = json.loads((project / "STORYBOARD_BASE.json").read_text(encoding="utf-8"))
            self.assertEqual(normalized[0]["beatId"], "B001")
            self.assertEqual(normalized[0]["chapterTitle"], "失败与再次出海")
            self.assertTrue(all(beat["motion"] == "hold" for beat in normalized))


if __name__ == "__main__":
    unittest.main()
