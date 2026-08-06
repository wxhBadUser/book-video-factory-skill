from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from phase1_fixture_factory import build_phase1_inputs
from phase2_fixture_factory import build_bridge_input, clone
from book_video_factory.hbg_bridge.contracts import (
    HbgBridgeContractError,
    validate_bridge_input,
)


class Phase2BridgeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = build_phase1_inputs()["script"]
        self.package_digest = "a" * 64
        self.release_hash = build_bridge_input(package_digest=self.package_digest)["release_text_sha256"]

    def validate(self, payload):
        return validate_bridge_input(
            payload,
            script=self.script,
            package_digest=self.package_digest,
            release_text_sha256=self.release_hash,
        )

    def test_valid_bridge_input_passes_and_is_normalized(self) -> None:
        result = self.validate(build_bridge_input(package_digest=self.package_digest))
        self.assertEqual(result["narration"]["provider"], "edge-tts")
        self.assertEqual(len(result["chapters"]), 4)
        self.assertEqual(len(result["storyboard_beats"]), 10)

    def test_package_digest_mismatch_fails(self) -> None:
        payload = build_bridge_input(package_digest="b" * 64)
        with self.assertRaisesRegex(HbgBridgeContractError, "content package digest"):
            self.validate(payload)

    def test_release_text_hash_mismatch_fails(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["release_text_sha256"] = "b" * 64
        with self.assertRaisesRegex(HbgBridgeContractError, "release text"):
            self.validate(payload)

    def test_non_edge_provider_fails(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["narration"]["provider"] = "other-tts"
        with self.assertRaisesRegex(HbgBridgeContractError, "edge-tts"):
            self.validate(payload)

    def test_duplicate_chapter_id_fails(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["chapters"][1]["chapter_id"] = payload["chapters"][0]["chapter_id"]
        with self.assertRaisesRegex(HbgBridgeContractError, "chapter_id"):
            self.validate(payload)

    def test_missing_section_coverage_fails(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["chapters"][-1]["section_ids"].remove("S10")
        with self.assertRaisesRegex(HbgBridgeContractError, "section coverage"):
            self.validate(payload)

    def test_duplicate_section_coverage_fails(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["chapters"][1]["section_ids"].append("S01")
        with self.assertRaisesRegex(HbgBridgeContractError, "section coverage"):
            self.validate(payload)

    def test_reversed_chapter_section_order_fails(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["chapters"][0]["section_ids"] = ["S02", "S01", "S03"]
        with self.assertRaisesRegex(HbgBridgeContractError, "section order"):
            self.validate(payload)

    def test_placeholder_character_identity_fails(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["characters"][0]["immutable_traits"] = ["待定"]
        with self.assertRaisesRegex(HbgBridgeContractError, "placeholder"):
            self.validate(payload)

    def test_unknown_anchor_reference_fails(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["storyboard_beats"][0]["anchor_refs"] = ["C999"]
        with self.assertRaisesRegex(HbgBridgeContractError, "anchor"):
            self.validate(payload)

    def test_high_risk_beat_must_be_single(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["storyboard_beats"][0]["risk_flags"] = ["hands"]
        payload["storyboard_beats"][0]["generation_mode"] = "2x2"
        with self.assertRaisesRegex(HbgBridgeContractError, "single"):
            self.validate(payload)

    def test_missing_agent_authored_description_fails(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["storyboard_beats"][0]["description"] = ""
        with self.assertRaisesRegex(HbgBridgeContractError, "description"):
            self.validate(payload)

    def test_required_and_forbidden_entities_must_be_disjoint(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["storyboard_beats"][0]["forbidden_entities"] = ["圣地亚哥"]
        with self.assertRaisesRegex(HbgBridgeContractError, "required.*forbidden"):
            self.validate(payload)

    def test_participants_must_match_allowed_characters(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["storyboard_beats"][0]["participants"] = {"count": 1, "allowed": ["C999"]}
        with self.assertRaisesRegex(HbgBridgeContractError, "participants"):
            self.validate(payload)

    def test_absolute_or_traversal_paths_are_not_allowed_anywhere(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["brand"]["series_name"] = "../../escape"
        with self.assertRaisesRegex(HbgBridgeContractError, "path traversal"):
            self.validate(payload)

    def test_storyboard_cue_must_belong_to_declared_section(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        first = payload["storyboard_beats"][0]["section_id"]
        payload["storyboard_beats"][0]["section_id"] = payload["storyboard_beats"][1]["section_id"]
        payload["storyboard_beats"][1]["section_id"] = first
        with self.assertRaisesRegex(HbgBridgeContractError, "cue.*section"):
            self.validate(payload)

    def test_phase_two_cannot_claim_character_anchor_is_already_approved(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["characters"][0]["anchor_status"] = "approved"
        with self.assertRaisesRegex(HbgBridgeContractError, "anchor_status.*pending"):
            self.validate(payload)

    def test_environment_beat_may_have_zero_participants(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["storyboard_beats"][0]["participants"] = {"count": 0, "allowed": []}
        result = self.validate(payload)
        self.assertEqual(result["storyboard_beats"][0]["participants"], {"count": 0, "allowed": []})


    def test_unknown_risk_flag_cannot_bypass_single_image_routing(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["storyboard_beats"][0]["risk_flags"] = ["hands_typo"]
        payload["storyboard_beats"][0]["generation_mode"] = "2x2"
        with self.assertRaisesRegex(HbgBridgeContractError, "risk_flags.*unknown"):
            self.validate(payload)

    def test_identifiers_with_surrounding_whitespace_fail_instead_of_normalizing_ambiguously(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["chapters"][0]["chapter_id"] = " CH01"
        with self.assertRaisesRegex(HbgBridgeContractError, "surrounding whitespace"):
            self.validate(payload)

    def test_environment_beat_may_have_no_character_anchor(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        payload["storyboard_beats"][0]["participants"] = {"count": 0, "allowed": []}
        payload["storyboard_beats"][0]["anchor_refs"] = []
        result = self.validate(payload)
        self.assertEqual(result["storyboard_beats"][0]["anchor_refs"], [])

    def test_every_visible_participant_requires_an_identity_anchor_reference(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        second = dict(payload["characters"][0])
        second["character_id"] = "C002"
        second["name"] = "马诺林"
        payload["characters"].append(second)
        payload["storyboard_beats"][0]["anchor_refs"] = ["C002"]
        payload["storyboard_beats"][0]["participants"] = {"count": 1, "allowed": ["C001"]}
        with self.assertRaisesRegex(HbgBridgeContractError, "participant.*anchor"):
            self.validate(payload)

    def test_edge_tts_rate_and_pitch_must_use_supported_syntax(self) -> None:
        for key, value in (("body_rate", "fast"), ("pitch", "high")):
            with self.subTest(key=key):
                payload = build_bridge_input(package_digest=self.package_digest)
                payload["narration"][key] = value
                with self.assertRaisesRegex(HbgBridgeContractError, key):
                    self.validate(payload)

    def test_validator_does_not_mutate_input(self) -> None:
        payload = build_bridge_input(package_digest=self.package_digest)
        original = clone(payload)
        self.validate(payload)
        self.assertEqual(payload, original)


if __name__ == "__main__":
    unittest.main()
