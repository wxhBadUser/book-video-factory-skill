"""Part 4 wiring - the production image task must carry the caption-first prompt.

Before this change ``_task_for_scene`` fed the scene's template
``semanticRationale`` straight into the prompt as the *action*, put art
direction first and shipped nothing that could later prove the prompt still
matched its caption. This file pins the wiring:

* the prompt starts with the caption block, not with art direction;
* the task carries a ``visual_proposition`` with a stable content hash;
* the task carries a ``prompt_binding`` that covers caption ids, caption text
  hash, proposition hash, prompt hash, scene id and beat ids;
* the binding verifies against the emitted prompt and fails closed after any
  drift.
"""

from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path
from typing import Any

from book_video_factory.director_stage.compiler import _task_for_scene
from book_video_factory.semantic_alignment.caption_contract import CaptionVisualContract
from book_video_factory.semantic_alignment.models import VisualProposition
from book_video_factory.semantic_alignment.prompting import (
    PromptBindingError,
    verify_prompt_binding,
)

PROFILE = {
    "release_id": "v1-r1",
    "book_look": {
        "visual_world": "literary cinematic realism",
        "period": "1940s-1980s rural China",
        "geography": "Jiangnan farmland",
        "render_balance": "restrained painterly",
        "emotional_temperature": "warm dusk",
    },
    "palette_profiles": [{"palette_id": "P_EARTH", "colors": ["umber", "ivory", "ochre"]}],
    "lighting_profiles": [
        {
            "lighting_id": "L_DUSK",
            "key": "low sun from camera-left",
            "fill": "sky bounce",
            "shadow": "deep but readable",
        }
    ],
    "material_profiles": [{"materials": ["coarse cotton", "wet clay", "worn timber"]}],
    "style_reference_ids": ["STYLE_A"],
    "forbidden_traits": ["modern clothing"],
    "character_anchors": [
        {
            "character_id": "C001",
            "anchor_id": "CHAR_C001",
            "prompt_subject": "福贵，瘦削老年农民",
            "invariants": ["灰白短发"],
            "wardrobe": ["粗布短褂"],
            "forbidden_changes": ["西装"],
        }
    ],
}
VISUAL_ASSETS = {"assets": [{"task_id": "ANCHOR_C001_FRONT"}]}
CANVAS = {"orientation": "landscape", "width": 1920, "height": 1080}


def scene(**overrides: object) -> dict:
    base = {
        "id": "scene-001",
        "captionIds": ["c1"],
        "description": "福贵牵着老牛走过田埂",
        "semanticRationale": "字幕与画面共享当前场景：福贵",
        "requiredEntities": ["福贵", "老牛"],
        "forbiddenEntities": ["拖拉机"],
        "anchorRefs": ["C001"],
        "riskFlags": [],
        "sourceBeatIds": ["B-001"],
        "captionIntent": "疲惫而平静",
        "narrativeFunction": "plot",
        "participants": {"count": 1, "allowed": ["C001"]},
    }
    base.update(overrides)
    return base


CAPTIONS = {"c1": {"text": "福贵牵着老牛走过田埂。"}}


class DirectorTaskSemanticBindingTests(unittest.TestCase):
    def build(self, **overrides: object) -> dict:
        return _task_for_scene(scene(**overrides), PROFILE, VISUAL_ASSETS, CAPTIONS, CANVAS)

    def test_prompt_opens_with_the_caption_block(self) -> None:
        task = self.build()
        self.assertTrue(
            task["prompt"].startswith("[1/9 CAPTION]"),
            msg=f"prompt started with: {task['prompt'][:80]!r}",
        )
        self.assertIn("福贵牵着老牛走过田埂。", task["prompt"].splitlines()[0])

    def test_caption_precedes_style_in_the_prompt(self) -> None:
        prompt = self.build()["prompt"]
        self.assertLess(
            prompt.index("福贵牵着老牛走过田埂。"),
            prompt.index("literary cinematic realism"),
        )

    def test_task_carries_a_visual_proposition(self) -> None:
        task = self.build()
        proposition = task["visual_proposition"]
        self.assertIn(proposition["mode"], {"Literal", "Symbolic", "Abstract"})
        self.assertTrue(proposition["rationale_text"].strip())
        self.assertNotEqual(
            proposition["rationale_text"], scene()["semanticRationale"],
            msg="the template rationale must not be reused as the proposition rationale",
        )

    def test_template_rationale_is_never_used_as_the_prompt_action(self) -> None:
        prompt = self.build()["prompt"]
        self.assertNotIn("字幕与画面共享当前场景", prompt)

    def test_task_carries_a_complete_prompt_binding(self) -> None:
        task = self.build()
        binding = task["prompt_binding"]
        self.assertEqual(binding["caption_ids"], ["c1"])
        self.assertEqual(binding["scene_id"], "scene-001")
        self.assertEqual(binding["beat_ids"], ["B-001"])
        self.assertEqual(binding["prompt_sha256"], task["prompt_sha256"])
        self.assertEqual(
            binding["proposition_sha256"],
            VisualProposition.from_mapping(task["visual_proposition"]).content_sha256(),
        )
        self.assertEqual(len(binding["caption_text_sha256"]), 64)

    def test_binding_verifies_against_the_emitted_prompt(self) -> None:
        task = self.build()
        verify_prompt_binding(
            task["prompt_binding"],
            caption_text=task["caption_text"],
            proposition=VisualProposition.from_mapping(task["visual_proposition"]),
            prompt=task["prompt"],
        )

    def test_binding_fails_closed_when_the_caption_changes(self) -> None:
        task = self.build()
        with self.assertRaises(PromptBindingError):
            verify_prompt_binding(
                task["prompt_binding"],
                caption_text="福贵坐在门槛上。",
                proposition=VisualProposition.from_mapping(task["visual_proposition"]),
                prompt=task["prompt"],
            )

    def test_binding_fails_closed_when_the_prompt_changes(self) -> None:
        task = self.build()
        with self.assertRaises(PromptBindingError):
            verify_prompt_binding(
                task["prompt_binding"],
                caption_text=task["caption_text"],
                proposition=VisualProposition.from_mapping(task["visual_proposition"]),
                prompt=task["prompt"] + "\nextra instruction",
            )

    def test_forbidden_entities_reach_the_prompt(self) -> None:
        prompt = self.build()["prompt"]
        self.assertIn("拖拉机", prompt)
        self.assertIn("modern clothing", prompt)

    def test_author_background_scene_never_claims_story_characters(self) -> None:
        task = self.build(
            id="scene-002",
            narrativeFunction="author_background",
            description="余华写作时期的书桌",
            requiredEntities=["余华"],
            anchorRefs=[],
            captionIds=["c2"],
        )
        self.assertIn("author_background", task["prompt"])

    def test_theory_contract_drops_beat_person_from_task_subject_inputs(self) -> None:
        """A Beat-only person cannot bypass an abstract Caption Visual Contract."""
        text = "这本书真正可怕的地方，是命运对好人反复的碾压。"
        contract = CaptionVisualContract(
            caption_id="c3",
            caption_text=text,
            caption_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            section_id="S1",
            source_beat_ids=("B-001",),
            narrative_function="theory",
            scene_state={
                "visible_character_ids": [],
                "location_id": "",
                "time_context": "",
                "action_state": text,
                "continuity_state": {"pronoun_resolutions": []},
            },
            visual_mode="symbolic_or_abstract",
        )
        task = _task_for_scene(
            scene(
                captionIds=["c3"],
                description="阴天的田野",
                requiredEntities=["福贵"],
                narrativeFunction="theory",
            ),
            PROFILE,
            VISUAL_ASSETS,
            {"c3": {"text": text}},
            CANVAS,
            caption_contracts={"c3": contract},
        )
        self.assertEqual(task["required_entities"], [])
        self.assertEqual(task["visual_proposition"]["mode"], "Abstract")
        self.assertNotIn("福贵", task["prompt"])

    def test_two_identical_scenes_produce_identical_tasks(self) -> None:
        first = self.build()
        second = self.build()
        self.assertEqual(first["prompt_sha256"], second["prompt_sha256"])
        self.assertEqual(first["prompt_binding"], second["prompt_binding"])

    def test_missing_source_beats_is_a_hard_error(self) -> None:
        with self.assertRaises(Exception):
            self.build(sourceBeatIds=[])


CAPTIONS["c2"] = {"text": "余华写这本书的时候只有三十岁。"}

SCHEMA_PATH = (
    Path(__file__).resolve().parents[1]
    / "schemas"
    / "production_image_task.v1.schema.json"
)

_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list, tuple),
    "string": (str,),
    "boolean": (bool,),
    "number": (int, float),
    "integer": (int,),
}


def validate_against_schema(value: Any, schema: dict, path: str = "$") -> list[str]:
    """Minimal JSON Schema subset validator.

    The project does not vendor ``jsonschema`` and installing it here would
    change the runtime environment, so this covers exactly the keywords the
    production task schema uses: type, const, enum, pattern, minLength,
    required, properties, items, minItems and additionalProperties.
    """

    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = _TYPE_MAP[expected_type]
        ok = isinstance(value, allowed)
        if expected_type in {"number", "integer"} and isinstance(value, bool):
            ok = False
        if not ok:
            errors.append(f"{path}: expected {expected_type}, got {type(value).__name__}")
            return errors
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in enum {schema['enum']}")
    if isinstance(value, str):
        pattern = schema.get("pattern")
        if pattern and not re.search(pattern, value):
            errors.append(f"{path}: {value!r} does not match {pattern}")
        min_length = schema.get("minLength")
        if min_length is not None and len(value) < min_length:
            errors.append(f"{path}: shorter than minLength {min_length}")
    if isinstance(value, (list, tuple)):
        min_items = schema.get("minItems")
        if min_items is not None and len(value) < min_items:
            errors.append(f"{path}: fewer than minItems {min_items}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(validate_against_schema(item, item_schema, f"{path}[{index}]"))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required property {key!r}")
        properties = schema.get("properties", {})
        for key, sub_schema in properties.items():
            if key in value:
                errors.extend(validate_against_schema(value[key], sub_schema, f"{path}.{key}"))
        if schema.get("additionalProperties") is False:
            unexpected = sorted(set(value) - set(properties))
            if unexpected:
                errors.append(f"{path}: unexpected properties {unexpected}")
    return errors


class ProductionImageTaskSchemaTests(unittest.TestCase):
    """The emitted task must satisfy the published production task contract."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_schema_declares_the_new_semantic_fields(self) -> None:
        properties = self.schema["properties"]
        for field in ("narrative_function", "source_beat_ids", "visual_proposition", "prompt_binding"):
            with self.subTest(field=field):
                self.assertIn(
                    field,
                    properties,
                    msg="the production task schema must document the semantic alignment fields",
                )

    def test_emitted_task_validates_against_the_schema(self) -> None:
        task = _task_for_scene(scene(), PROFILE, VISUAL_ASSETS, CAPTIONS, CANVAS)
        errors = validate_against_schema(task, self.schema)
        self.assertEqual(errors, [], msg="\n".join(errors))

    def test_schema_rejects_an_unknown_proposition_mode(self) -> None:
        task = _task_for_scene(scene(), PROFILE, VISUAL_ASSETS, CAPTIONS, CANVAS)
        task["visual_proposition"]["mode"] = "Interpretive"
        errors = validate_against_schema(task, self.schema)
        self.assertTrue(errors, msg="an unknown proposition mode must be rejected")

    def test_schema_rejects_a_binding_without_caption_ids(self) -> None:
        task = _task_for_scene(scene(), PROFILE, VISUAL_ASSETS, CAPTIONS, CANVAS)
        task["prompt_binding"]["caption_ids"] = []
        errors = validate_against_schema(task, self.schema)
        self.assertTrue(errors, msg="an empty caption id list must be rejected")

    def test_schema_rejects_a_truncated_prompt_hash(self) -> None:
        task = _task_for_scene(scene(), PROFILE, VISUAL_ASSETS, CAPTIONS, CANVAS)
        task["prompt_binding"]["prompt_sha256"] = "abc123"
        errors = validate_against_schema(task, self.schema)
        self.assertTrue(errors, msg="a malformed prompt hash must be rejected")


if __name__ == "__main__":
    unittest.main()
