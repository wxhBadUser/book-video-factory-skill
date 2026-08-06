from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from phase2_fixture_factory import build_bridge_input
from book_video_factory.hbg_bridge.character_export import (
    HbgCharacterExportError,
    export_characters_markdown,
)


class Phase2CharacterExportTests(unittest.TestCase):
    def test_exports_all_identity_fields_deterministically(self) -> None:
        chars = build_bridge_input()["characters"]
        one = export_characters_markdown(chars)
        two = export_characters_markdown(chars)
        self.assertEqual(one, two)
        self.assertIn("# 人物身份与连续性", one)
        self.assertIn("## C001｜圣地亚哥", one)
        self.assertIn("瘦削脸型", one)
        self.assertIn("与少年马诺林关系亲密", one)
        self.assertIn("pending", one)

    def test_duplicate_character_id_fails(self) -> None:
        chars = build_bridge_input()["characters"]
        chars.append(deepcopy(chars[0]))
        with self.assertRaisesRegex(HbgCharacterExportError, "duplicate"):
            export_characters_markdown(chars)

    def test_missing_immutable_traits_fails(self) -> None:
        chars = build_bridge_input()["characters"]
        chars[0]["immutable_traits"] = []
        with self.assertRaisesRegex(HbgCharacterExportError, "immutable"):
            export_characters_markdown(chars)

    def test_placeholder_text_fails(self) -> None:
        chars = build_bridge_input()["characters"]
        chars[0]["wardrobe"] = ["待定"]
        with self.assertRaisesRegex(HbgCharacterExportError, "placeholder"):
            export_characters_markdown(chars)


if __name__ == "__main__":
    unittest.main()
