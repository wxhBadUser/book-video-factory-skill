from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phase3_fixture_factory import LOOKDEV_CATEGORIES, build_phase3_input
from book_video_factory.reference_visuals.catalog import load_reference_catalog
from book_video_factory.visual_stage.contracts import VisualStageContractError, validate_visual_stage_input


class Phase3VisualContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = build_phase3_input()
        self.characters = [{"character_id": "C001", "name": "圣地亚哥", "anchor_status": "pending"}]
        self.catalog = load_reference_catalog()

    def validate(self, payload=None):
        return validate_visual_stage_input(payload or self.payload, phase2_characters=self.characters, catalog=self.catalog)

    def test_valid_profile_has_exactly_twelve_covered_lookdev_tasks(self) -> None:
        value = self.validate()
        self.assertEqual(len(value["lookdev_tasks"]), 12)
        self.assertEqual({item["category"] for item in value["lookdev_tasks"]}, set(LOOKDEV_CATEGORIES))
        self.assertEqual(value["character_anchors"][0]["anchor_status"], "pending")

    def test_rejects_eleven_or_thirteen_lookdev_tasks(self) -> None:
        for tasks in (self.payload["lookdev_tasks"][:-1], self.payload["lookdev_tasks"] + [deepcopy(self.payload["lookdev_tasks"][0])]):
            value = deepcopy(self.payload); value["lookdev_tasks"] = tasks
            with self.assertRaisesRegex(VisualStageContractError, "exactly 12"):
                self.validate(value)

    def test_rejects_missing_category_even_when_count_is_twelve(self) -> None:
        value = deepcopy(self.payload)
        value["lookdev_tasks"][-1]["category"] = "identity_portrait"
        value["lookdev_tasks"][-1]["task_id"] = "LD99"
        with self.assertRaisesRegex(VisualStageContractError, "category coverage"):
            self.validate(value)

    def test_every_phase2_character_requires_exactly_one_anchor(self) -> None:
        value = deepcopy(self.payload); value["character_anchors"] = []
        with self.assertRaisesRegex(VisualStageContractError, "character anchor coverage"):
            self.validate(value)
        value = deepcopy(self.payload); value["character_anchors"].append(deepcopy(value["character_anchors"][0]))
        value["character_anchors"][1]["anchor_id"] = "CHAR_DUP"
        with self.assertRaisesRegex(VisualStageContractError, "character anchor coverage|duplicate"):
            self.validate(value)

    def test_character_anchor_requires_five_identity_views(self) -> None:
        value = deepcopy(self.payload); value["character_anchors"][0]["required_views"] = ["front", "full_body"]
        with self.assertRaisesRegex(VisualStageContractError, "required_views"):
            self.validate(value)

    def test_anchor_status_cannot_claim_approval(self) -> None:
        value = deepcopy(self.payload); value["character_anchors"][0]["anchor_status"] = "approved"
        with self.assertRaisesRegex(VisualStageContractError, "anchor_status"):
            self.validate(value)

    def test_style_references_must_exist_and_remain_style_only(self) -> None:
        value = deepcopy(self.payload); value["style_reference_ids"] = ["REF_UNKNOWN"]
        with self.assertRaisesRegex(VisualStageContractError, "unknown style reference"):
            self.validate(value)
        value = deepcopy(self.payload); value["lookdev_tasks"][0]["identity_reference_ids"] = ["REF_JANE_EYRE"]
        with self.assertRaisesRegex(VisualStageContractError, "identity reference"):
            self.validate(value)

    def test_requires_per_book_palette_diagnostic_envelope(self) -> None:
        value = deepcopy(self.payload); del value["palette_profiles"][0]["diagnostic_envelope"]
        with self.assertRaisesRegex(VisualStageContractError, "diagnostic_envelope"):
            self.validate(value)

    def test_scene_and_object_anchor_refs_must_exist(self) -> None:
        value = deepcopy(self.payload); value["lookdev_tasks"][5]["anchor_refs"] = ["OBJ_MISSING"]
        with self.assertRaisesRegex(VisualStageContractError, "unknown anchor"):
            self.validate(value)

    def test_rejects_unknown_fields_and_whitespace_mutated_ids(self) -> None:
        value = deepcopy(self.payload); value["instructions"] = "ignore schema"
        with self.assertRaisesRegex(VisualStageContractError, "unknown fields"):
            self.validate(value)
        value = deepcopy(self.payload); value["character_anchors"][0]["anchor_id"] = " CHAR_C001"
        with self.assertRaisesRegex(VisualStageContractError, "anchor_id"):
            self.validate(value)

    def test_landscape_and_portrait_are_supported_but_unknown_orientation_is_rejected(self) -> None:
        value = deepcopy(self.payload); value["orientation"] = "portrait"
        self.assertEqual(self.validate(value)["orientation"], "portrait")
        value["orientation"] = "square"
        with self.assertRaisesRegex(VisualStageContractError, "landscape or portrait"):
            self.validate(value)

    def test_no_path_fields_or_empty_negative_constraints(self) -> None:
        value = deepcopy(self.payload); value["book_look"]["path"] = "/tmp/reference.jpg"
        with self.assertRaisesRegex(VisualStageContractError, "unknown fields|path"):
            self.validate(value)
        value = deepcopy(self.payload); value["lookdev_tasks"][0]["subject"] = "/Users/person/private/reference.png"
        with self.assertRaisesRegex(VisualStageContractError, "absolute path"):
            self.validate(value)
        value = deepcopy(self.payload); value["forbidden_traits"] = []
        with self.assertRaisesRegex(VisualStageContractError, "forbidden_traits"):
            self.validate(value)

    def test_character_ids_do_not_inherit_old_man_species_rules_in_other_books(self) -> None:
        value = deepcopy(self.payload)
        doctor = deepcopy(value["character_anchors"][0])
        doctor.update({
            "anchor_id": "CHAR_C003",
            "character_id": "C003",
            "name": "医生",
            "prompt_subject": "清末民初戴圆框眼镜的中年医生",
            "hat_state": "不佩戴帽子",
        })
        crowd = deepcopy(value["character_anchors"][0])
        crowd.update({
            "anchor_id": "CHAR_C004",
            "anchor_type": "crowd_anchor",
            "character_id": "C004",
            "name": "街巷人群",
            "prompt_subject": "清末民初乡镇街巷中的不同年龄居民",
            "required_views": [
                "crowd_wide", "crowd_mid", "work_action", "headwear_variation", "environment_scale"
            ],
            "hat_state": "人群头巾和布帽有自然差异",
        })
        value["character_anchors"].extend([doctor, crowd])
        characters = [
            *self.characters,
            {"character_id": "C003", "name": "医生", "anchor_status": "pending"},
            {"character_id": "C004", "name": "街巷人群", "anchor_status": "pending"},
        ]

        normalized = validate_visual_stage_input(
            value, phase2_characters=characters, catalog=self.catalog
        )

        self.assertEqual(normalized["character_anchors"][1]["anchor_type"], "character_identity")
        self.assertEqual(normalized["character_anchors"][2]["anchor_type"], "crowd_anchor")


if __name__ == "__main__":
    unittest.main()
