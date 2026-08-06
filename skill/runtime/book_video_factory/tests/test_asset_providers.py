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

from book_video_factory.visual_assets import (  # noqa: E402
    VisualContractError,
    build_imagegen_prompt,
    ingest_imagegen_asset,
    select_shot_characters,
    validate_painting_light_cadence,
)
from book_video_factory.longform_contracts import validate_longform_manifest  # noqa: E402


def painting_light(role: str) -> dict[str, str]:
    return {
        "key": "motivated side light",
        "fill": "cool ambient fill",
        "shadow_readability": "named subject retains colored mid-tones",
        "temperature_role": role,
    }


class ImageGenPromptTests(unittest.TestCase):
    def test_literary_oil_prompt_binds_narration_entities_and_paint_handling(self) -> None:
        art_direction = {
            "visual_world": "poetic literary narrative oil painting",
            "palette": "burnt umber, moss green, parchment ivory, one vermilion accent",
            "texture": "layered oil pigment on linen canvas",
            "painting_identity": "Twilight Garden Literary Oil",
            "paint_handling": {
                "focal": "fine transparent glazing on faces, hands and narrative objects",
                "accent": "restrained localized impasto on flowers and brightest edges",
                "background": "visible directional brushwork with lost edges in shadow",
            },
            "reference_roles": {
                "image_1": "figure rendering and paint handling only",
                "image_2": "palette, flower material and linen-canvas texture only",
                "image_3": "motivated garden light and colored-shadow structure only",
            },
            "lighting": {
                "key": "one motivated warm side light",
                "fill": "cool grey-blue ambient fill",
                "shadow": "colored shadows with readable mid-tones",
            },
        }
        shot = {
            "shot_id": "SH105",
            "asset_tier": "cinematic_still",
            "narration_text": "狐狸请小王子驯养自己。",
            "semantic_entities": ["little_prince", "fox"],
            "scene_mode": "dialogue",
            "lighting_intent": {
                "key": "soft antique-gold sunset from camera-left on both faces",
                "fill": "cool mist-blue sky fill keeps the fox muzzle and child's eyes readable",
                "shadow_readability": "wheat, scarf and both face planes retain colored mid-tones",
                "temperature_role": "balanced",
            },
            "subject": "the little prince and the fox face one another in wheat",
            "action": "the fox speaks while the child listens without touching it",
            "shot_size": "medium two-shot",
            "lens": "50mm",
            "camera_angle": "eye level",
            "composition": "both faces visible across a deliberate gap",
            "depth": "foreground wheat, sharp faces, soft distant field",
            "emotion": "cautious tenderness",
            "edit_target": "generated-v2/V2S05.png",
            "reference_instructions": [
                "Image 1 is the edit target; preserve both subjects and composition.",
                "Image 2 is the Little Prince continuity anchor; preserve identity only.",
            ],
            "references": [
                {"role": "character", "asset_id": "CHAR_LITTLE_PRINCE_V1"},
                {"role": "character", "asset_id": "CHAR_FOX_V1"},
            ],
        }

        prompt = build_imagegen_prompt(art_direction, shot, [])

        for expected in (
            "Narration this frame must illustrate: 狐狸请小王子驯养自己。",
            "Visible semantic entities: little_prince, fox",
            "Dialogue staging",
            "fine transparent glazing",
            "restrained localized impasto",
            "visible directional brushwork",
            "Image 1 controls figure rendering and paint handling only",
            "not a photograph",
            "Every listed semantic entity must be visibly recognizable",
            "Shot-specific light: key soft antique-gold sunset",
            "temperature role balanced",
            "Edit target: generated-v2/V2S05.png",
            "Image 1 is the edit target; preserve both subjects and composition.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, prompt)

    def test_literary_oil_prompt_rejects_missing_semantic_entities(self) -> None:
        art_direction = {
            "visual_world": "literary oil painting",
            "palette": "umber and ivory",
            "texture": "linen canvas",
            "painting_identity": "Twilight Garden Literary Oil",
            "paint_handling": {
                "focal": "fine glazing",
                "accent": "localized impasto",
                "background": "directional brushwork",
            },
            "lighting": {
                "key": "side light",
                "fill": "cool fill",
                "shadow": "colored shadow",
            },
        }
        shot = {
            "shot_id": "SH106",
            "asset_tier": "cinematic_still",
            "narration_text": "玫瑰在玻璃罩里沉默。",
            "semantic_entities": [],
            "scene_mode": "object",
            "lighting_intent": {
                "key": "warm side light",
                "fill": "cool sky fill",
                "shadow_readability": "petals and glass edges remain readable",
                "temperature_role": "balanced",
            },
            "subject": "a rose under glass",
            "action": "the petals turn toward light",
            "shot_size": "close-up",
            "lens": "85mm",
            "camera_angle": "eye level",
            "composition": "rose centered in glass",
            "depth": "rose sharp, stars soft",
            "emotion": "quiet tenderness",
            "references": [],
        }

        with self.assertRaisesRegex(VisualContractError, "semantic_entities"):
            build_imagegen_prompt(art_direction, shot, [])

    def test_literary_oil_prompt_rejects_missing_shot_specific_light(self) -> None:
        art_direction = {
            "visual_world": "literary oil painting",
            "palette": "umber and ivory",
            "texture": "linen canvas",
            "painting_identity": "Twilight Garden Literary Oil",
            "paint_handling": {
                "focal": "fine glazing",
                "accent": "localized impasto",
                "background": "directional brushwork",
            },
            "lighting": {
                "key": "side light",
                "fill": "cool fill",
                "shadow": "colored shadow",
            },
        }
        shot = {
            "shot_id": "SH107",
            "asset_tier": "cinematic_still",
            "narration_text": "点灯人点亮了铁灯。",
            "semantic_entities": ["lamplighter", "iron_lamp"],
            "scene_mode": "scene",
            "subject": "a lamplighter and iron lamp",
            "action": "he shields the flame",
            "shot_size": "wide shot",
            "lens": "50mm",
            "camera_angle": "eye level",
            "composition": "lamp and person both legible",
            "depth": "figure sharp, sky soft",
            "emotion": "quiet duty",
            "references": [],
        }

        with self.assertRaisesRegex(VisualContractError, "lighting_intent"):
            build_imagegen_prompt(art_direction, shot, [])

    def test_literary_oil_batch_rejects_four_consecutive_warm_frames(self) -> None:
        with self.assertRaisesRegex(VisualContractError, "four consecutive"):
            validate_painting_light_cadence(
                [
                    {"shot_id": "SH001", "lighting_intent": painting_light("warm-dominant")},
                    {"shot_id": "SH002", "lighting_intent": painting_light("warm-dominant")},
                    {"shot_id": "SH003", "lighting_intent": painting_light("warm-dominant")},
                    {"shot_id": "SH004", "lighting_intent": painting_light("warm-dominant")},
                ]
            )

    def test_literary_oil_batch_accepts_cool_counterpoint_within_four_frames(self) -> None:
        validate_painting_light_cadence(
            [
                {"shot_id": "SH001", "lighting_intent": painting_light("warm-dominant")},
                {"shot_id": "SH002", "lighting_intent": painting_light("balanced")},
                {"shot_id": "SH003", "lighting_intent": painting_light("warm-dominant")},
                {"shot_id": "SH004", "lighting_intent": painting_light("cool-counterpoint")},
                {"shot_id": "SH005", "lighting_intent": painting_light("warm-dominant")},
            ]
        )

    def test_prompt_encodes_camera_light_texture_continuity_and_clean_frame(self) -> None:
        art_direction = {
            "visual_world": "late-19th-century European railway realism",
            "palette": "deep burgundy, oxidized gold, smoky teal, warm skin",
            "texture": "fine 35mm grain, restrained halation, tactile oil-painted detail",
            "lighting": {
                "key": "warm window light from camera-left",
                "fill": "very low cool ambient fill",
                "shadow": "deep shaped shadows with readable faces",
            },
        }
        character = {
            "character_id": "anna",
            "continuity_anchor": "oval face, dark brown center-parted hair, burgundy wool dress",
        }
        shot = {
            "shot_id": "SH023",
            "asset_tier": "cinematic_still",
            "subject": "Anna pauses beside a rain-streaked train window",
            "action": "her gloved hand loosens from the brass handle",
            "shot_size": "medium close-up",
            "lens": "50mm",
            "camera_angle": "eye level, three-quarter profile",
            "composition": "Anna on the right third, negative space toward the moving landscape",
            "depth": "shallow depth of field, eyes and glove sharp",
            "emotion": "contained dread beneath social composure",
            "references": [{"role": "character", "asset_id": "CHAR_ANNA_V1"}],
        }

        prompt = build_imagegen_prompt(art_direction, shot, [character])
        validate_longform_manifest(
            {
                "schema_version": "1.0",
                "schema_id": "art-direction.v1",
                "release_id": "v1-r1",
                "book": {"title": "安娜·卡列尼娜", "author": "列夫·托尔斯泰"},
                "status": "locked",
                **art_direction,
            }
        )

        for expected in (
            "SH023",
            "50mm",
            "camera-left",
            "deep shaped shadows",
            "fine 35mm grain",
            "burgundy wool dress",
            "16:9",
            "no text",
            "no watermark",
            "no official book cover",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, prompt)

    def test_prompt_fails_closed_when_required_art_direction_is_missing(self) -> None:
        with self.assertRaises(VisualContractError):
            build_imagegen_prompt(
                {"visual_world": "cinematic"},
                {"shot_id": "S1", "subject": "a room"},
                [],
            )

    def test_ingest_copies_generated_asset_and_records_real_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            project.mkdir()
            generated = root / "generated.png"
            generated.write_bytes(b"real-image-bytes")

            entry = ingest_imagegen_asset(
                project,
                shot_id="SH001",
                source=generated,
                prompt="cinematic prompt",
                tool_call_id="imagegen-call-1",
            )

            target = project / "03_images_生成图片/generated/SH001.png"
            self.assertTrue(target.is_file())
            self.assertEqual(target.read_bytes(), b"real-image-bytes")
            self.assertEqual(entry["path"], "generated/SH001.png")
            self.assertEqual(len(entry["sha256"]), 64)
            self.assertEqual(len(entry["prompt_sha256"]), 64)

    def test_character_anchors_are_limited_to_shot_references(self) -> None:
        shot = {
            "references": [
                {"role": "character", "asset_id": "CHAR_LITTLE_PRINCE_V1"}
            ]
        }
        characters = [
            {"character_id": "little_prince", "continuity_anchor": "blond child"},
            {"character_id": "fox", "continuity_anchor": "russet fox"},
        ]

        selected = select_shot_characters(shot, characters)

        self.assertEqual([item["character_id"] for item in selected], ["little_prince"])


if __name__ == "__main__":
    unittest.main()
