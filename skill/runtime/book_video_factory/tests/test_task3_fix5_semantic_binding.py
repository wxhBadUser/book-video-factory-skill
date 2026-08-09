from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from book_video_factory.director_stage.compiler import _scene_for_caption_group
from book_video_factory.production_task_validation import (
    ProductionTaskValidationError,
    validate_production_image_task,
)
from book_video_factory.production_visuals.registry import SceneAssetError, _tasks
from book_video_factory.semantic_alignment.classifier import classify_proposition
from book_video_factory.semantic_alignment.contract_bindings import (
    ContractBindingError,
    aggregate_contract_bindings_sha256,
    normalize_contract_bindings,
)
from book_video_factory.semantic_alignment.models import EntityVisibility, VisualProposition
from book_video_factory.semantic_alignment.prompting import (
    PromptBindingError,
    build_aligned_prompt_blocks,
    compute_prompt_binding,
    verify_prompt_binding,
)


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schemas" / "production_image_task.v1.schema.json"
HEX_A = "a" * 64
HEX_B = "b" * 64
HEX_C = "c" * 64


def _literal() -> VisualProposition:
    return VisualProposition(
        mode="Literal",
        subject="福贵",
        action="福贵牵着老牛走过田埂",
        environment="黄昏田埂",
        mood="克制",
        lighting="soft",
        palette="earth",
        rationale_text="字幕点名福贵与动作，因此直接呈现。",
        entity_visibility=(EntityVisibility("C002", True, "福贵"),),
        source_terms=("福贵",),
    )


def _binding() -> tuple[dict, VisualProposition]:
    proposition = _literal()
    binding = compute_prompt_binding(
        caption_ids=("caption-0001", "caption-0002"),
        caption_text="福贵牵着老牛走。 / 天色暗了。",
        proposition=proposition,
        prompt="prompt",
        scene_id="caption-group-cg-0001",
        shot_id="SHOT_CAPTION_GROUP_CG_0001",
        beat_ids=("B001",),
        caption_visual_contract_sha256=aggregate_contract_bindings_sha256(
            [
                {"caption_id": "caption-0001", "content_sha256": HEX_B},
                {"caption_id": "caption-0002", "content_sha256": HEX_A},
            ]
        ),
        group_id="CG-0001",
        caption_contract_bindings=(
            {"caption_id": "caption-0001", "content_sha256": HEX_B},
            {"caption_id": "caption-0002", "content_sha256": HEX_A},
        ),
        caption_group_sha256=HEX_C,
    )
    return binding, proposition


def _verify_current(binding: dict, proposition: VisualProposition) -> None:
    verify_prompt_binding(
        binding,
        caption_text="福贵牵着老牛走。 / 天色暗了。",
        proposition=proposition,
        prompt="prompt",
        caption_visual_contract_sha256=aggregate_contract_bindings_sha256(
            [
                {"caption_id": "caption-0001", "content_sha256": HEX_B},
                {"caption_id": "caption-0002", "content_sha256": HEX_A},
            ]
        ),
        group_id="CG-0001",
        caption_contract_bindings=(
            {"caption_id": "caption-0001", "content_sha256": HEX_B},
            {"caption_id": "caption-0002", "content_sha256": HEX_A},
        ),
        caption_group_sha256=HEX_C,
        caption_ids=("caption-0001", "caption-0002"),
        scene_id="caption-group-cg-0001",
        shot_id="SHOT_CAPTION_GROUP_CG_0001",
        beat_ids=("B001",),
    )


def _current_expectations() -> dict[str, Any]:
    return {
        "caption_visual_contract_sha256": aggregate_contract_bindings_sha256(
            [
                {"caption_id": "caption-0001", "content_sha256": HEX_B},
                {"caption_id": "caption-0002", "content_sha256": HEX_A},
            ]
        ),
        "group_id": "CG-0001",
        "caption_contract_bindings": (
            {"caption_id": "caption-0001", "content_sha256": HEX_B},
            {"caption_id": "caption-0002", "content_sha256": HEX_A},
        ),
        "caption_group_sha256": HEX_C,
        "caption_ids": ("caption-0001", "caption-0002"),
        "scene_id": "caption-group-cg-0001",
        "shot_id": "SHOT_CAPTION_GROUP_CG_0001",
        "beat_ids": ("B001",),
    }


def _write_task_queue(root: Path, task: dict[str, Any]) -> None:
    path = root / "05_director" / "IMAGE_TASKS.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(task, ensure_ascii=False) + "\n", encoding="utf-8")


def _schema_task(mode: str) -> dict:
    proposition = {
        "mode": mode,
        "subject": "纯气氛" if mode == "Abstract" else "主体",
        "action": "" if mode == "Abstract" else "静态动作",
        "environment": "" if mode == "Abstract" else "时代环境",
        "mood": "克制",
        "lighting": "soft",
        "palette": "earth",
        "rationale_text": "有来源的解释",
        "entity_visibility": (
            [{"entity_id": "C002", "must_be_visible": True, "natural_language": "福贵"}]
            if mode == "Literal"
            else []
        ),
        "surrogate_objects": ["空碗"] if mode == "Symbolic" else [],
        "source_terms": ["失去"] if mode == "Symbolic" else [],
    }
    task = {
        "schema_version": "production-image-task.v1",
        "task_id": "SCENE_CG_0001",
        "scene_id": "caption-group-cg-0001",
        "shot_id": "SHOT_CAPTION_GROUP_CG_0001",
        "generation_lane": "host-imagegen",
        "generation_mode": "single",
        "caption_ids": ["caption-0001"],
        "caption_text": "字幕",
        "caption_visual_contract_sha256": "",
        "narrative_function": "theory" if mode != "Literal" else "plot",
        "source_beat_ids": ["B001"],
        "prompt": "prompt",
        "prompt_sha256": "",
        "visual_proposition": proposition,
        "prompt_binding": {
            "shot_id": "SHOT_CAPTION_GROUP_CG_0001",
            "scene_id": "caption-group-cg-0001",
            "group_id": "CG-0001",
            "caption_ids": ["caption-0001"],
            "caption_contract_bindings": [
                {"caption_id": "caption-0001", "content_sha256": HEX_A}
            ],
            "caption_group_sha256": HEX_C,
            "caption_visual_contract_sha256": "",
            "beat_ids": ["B001"],
            "caption_text_sha256": hashlib.sha256("字幕".encode()).hexdigest(),
            "proposition_sha256": "",
            "prompt_sha256": "",
        },
        "output_target": "assets/generated/scenes/cg-0001.png",
    }
    aggregate = aggregate_contract_bindings_sha256(
        task["prompt_binding"]["caption_contract_bindings"],
        expected_caption_ids=task["caption_ids"],
    )
    prompt_sha = hashlib.sha256(task["prompt"].encode()).hexdigest()
    proposition_sha = VisualProposition.from_mapping(proposition).content_sha256()
    task["caption_visual_contract_sha256"] = aggregate
    task["prompt_sha256"] = prompt_sha
    task["prompt_binding"]["caption_visual_contract_sha256"] = aggregate
    task["prompt_binding"]["prompt_sha256"] = prompt_sha
    task["prompt_binding"]["proposition_sha256"] = proposition_sha
    return task


_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list, tuple),
    "string": (str,),
    "boolean": (bool,),
}


def _schema_errors(value: Any, schema: dict, path: str = "$") -> list[str]:
    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type is not None and not isinstance(value, _TYPE_MAP[expected_type]):
        return [f"{path}: expected {expected_type}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: wrong const")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: outside enum")
    if isinstance(value, str):
        if schema.get("pattern") and not re.search(schema["pattern"], value):
            errors.append(f"{path}: pattern mismatch")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength")
    if isinstance(value, (list, tuple)):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                errors.extend(_schema_errors(item, schema["items"], f"{path}[{index}]"))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key}")
        properties = schema.get("properties", {})
        for key, child_schema in properties.items():
            if key in value:
                errors.extend(_schema_errors(value[key], child_schema, f"{path}.{key}"))
        if schema.get("additionalProperties") is False:
            for key in set(value) - set(properties):
                errors.append(f"{path}: unexpected {key}")
        for branch in schema.get("allOf", []):
            condition = branch.get("if")
            if condition is None or not _schema_errors(value, condition, path):
                errors.extend(_schema_errors(value, branch.get("then", {}), path))
    return errors


def test_ordered_contract_aggregate_preserves_caption_order() -> None:
    forward = [
        {"caption_id": "caption-0001", "content_sha256": HEX_B},
        {"caption_id": "caption-0002", "content_sha256": HEX_A},
    ]
    reverse = list(reversed(forward))
    assert aggregate_contract_bindings_sha256(forward) != aggregate_contract_bindings_sha256(reverse)
    with pytest.raises(ContractBindingError, match="ordered caption ids"):
        aggregate_contract_bindings_sha256(reverse, expected_caption_ids=("caption-0001", "caption-0002"))


def test_current_contract_binding_rejects_legacy_child_hash_key() -> None:
    with pytest.raises(ContractBindingError, match="legacy"):
        normalize_contract_bindings([
            {"caption_id": "caption-0001", "caption_visual_contract_sha256": HEX_A}
        ])


def test_literal_contract_question_stays_literal() -> None:
    proposition = classify_proposition(
        shot_id="S1",
        caption_texts=("福贵为什么牵着老牛走？",),
        description="",
        seed_rationale="",
        source_entities=("福贵",),
        narrative_function="plot",
        visual_mode="literal",
    )
    assert proposition.mode == "Literal"
    assert [item.natural_language for item in proposition.entity_visibility if item.must_be_visible] == ["福贵"]


def test_opening_meta_question_may_be_abstract_only_when_contract_says_abstract() -> None:
    proposition = classify_proposition(
        shot_id="S1",
        caption_texts=("一个人连续失败八十四天，还会不会再出海？",),
        description="",
        seed_rationale="",
        source_entities=(),
        narrative_function="opening",
        visual_mode="abstract",
    )
    assert proposition.mode == "Abstract"
    assert proposition.entity_visibility == ()


def test_current_group_binding_cannot_be_verified_without_identity_expectations() -> None:
    binding, proposition = _binding()
    with pytest.raises(PromptBindingError, match="current caption group verification requires"):
        verify_prompt_binding(
            binding,
            caption_text="福贵牵着老牛走。 / 天色暗了。",
            proposition=proposition,
            prompt="prompt",
            group_id="CG-0001",
            caption_contract_bindings=binding["caption_contract_bindings"],
            caption_group_sha256=HEX_C,
        )


@pytest.mark.parametrize("omitted", tuple(_current_expectations()))
def test_current_group_binding_requires_every_current_expectation(omitted: str) -> None:
    binding, proposition = _binding()
    expectations = _current_expectations()
    expectations.pop(omitted)
    with pytest.raises(PromptBindingError):
        verify_prompt_binding(
            binding,
            caption_text="福贵牵着老牛走。 / 天色暗了。",
            proposition=proposition,
            prompt="prompt",
            **expectations,
        )


def test_current_group_binding_rejects_legacy_child_key_even_when_expected_matches() -> None:
    binding, proposition = _binding()
    child = binding["caption_contract_bindings"][0]
    child["caption_visual_contract_sha256"] = child.pop("content_sha256")
    expectations = _current_expectations()
    expectations["caption_contract_bindings"] = binding["caption_contract_bindings"]
    with pytest.raises(PromptBindingError, match="legacy"):
        verify_prompt_binding(
            binding,
            caption_text="福贵牵着老牛走。 / 天色暗了。",
            proposition=proposition,
            prompt="prompt",
            **expectations,
        )


@pytest.mark.parametrize("field", ["caption_ids", "scene_id", "shot_id", "beat_ids"])
def test_current_group_binding_rejects_each_mutated_identity_field(field: str) -> None:
    binding, proposition = _binding()
    mutated = copy.deepcopy(binding)
    mutated[field] = ["EVIL"] if isinstance(mutated[field], list) else "EVIL"
    with pytest.raises(PromptBindingError, match=field):
        _verify_current(mutated, proposition)


def test_prompt_explicitly_forbids_title() -> None:
    blocks = build_aligned_prompt_blocks(
        caption_text="福贵牵着老牛走。",
        proposition=_literal(),
        narrative_function="plot",
        camera={
            "shot_size": "medium",
            "lens": "50mm",
            "camera_angle": "eye level",
            "composition": "clear subject",
            "depth": "readable background",
        },
        style={"visual_world": "realism", "palette": "earth", "texture": "film grain"},
        must_show=("福贵",),
    )
    assert "title" in blocks[5]


def test_group_scene_participant_count_and_mode_come_from_contracts() -> None:
    captions = {
        "c1": {"text": "福贵与家珍站在门口。", "start": 0.0, "end": 2.0},
    }
    upstream = {
        "c1": {
            "id": "old-scene",
            "chapter": 1,
            "sourceBeatIds": ["B001"],
            "requiredEntities": ["错误旧实体"],
            "forbiddenEntities": [],
            "riskFlags": [],
            "anchorRefs": ["C002", "C003"],
            "participants": {"count": 0, "allowed": []},
            "motion": "hold",
            "generationMode": "single",
        }
    }
    contracts = {
        "c1": SimpleNamespace(scene_state={"visible_character_ids": ["C002", "C003"]})
    }
    scene = _scene_for_caption_group(
        {"group_id": "CG-1", "caption_ids": ["c1"], "start": 0.0, "end": 2.0, "narrative_function": "plot"},
        captions,
        upstream,
        contracts,
    )
    assert scene["participants"] == {
        "count": 2,
        "mode": "multi_character",
        "allowed": ["C002", "C003"],
        "forbidden": [],
    }


def test_schema_requires_authoritative_semantic_sources_and_public_binding_key() -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    valid = _schema_task("Literal")
    assert _schema_errors(valid, schema) == []
    for field in ("narrative_function", "source_beat_ids"):
        invalid = copy.deepcopy(valid)
        invalid.pop(field)
        assert _schema_errors(invalid, schema)
    invalid = copy.deepcopy(valid)
    child = invalid["prompt_binding"]["caption_contract_bindings"][0]
    child["caption_visual_contract_sha256"] = child.pop("content_sha256")
    assert _schema_errors(invalid, schema)


@pytest.mark.parametrize("mode", ["Literal", "Symbolic", "Abstract"])
def test_schema_and_production_loader_accept_each_legal_proposition_mode(mode: str) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    task = _schema_task(mode)
    assert _schema_errors(task, schema) == []
    validate_production_image_task(task)


def test_production_loader_rejects_missing_semantic_source_or_legacy_binding_key() -> None:
    for field in ("narrative_function", "source_beat_ids"):
        invalid = _schema_task("Literal")
        invalid.pop(field)
        with pytest.raises(ProductionTaskValidationError, match=field):
            validate_production_image_task(invalid)
    invalid = _schema_task("Literal")
    child = invalid["prompt_binding"]["caption_contract_bindings"][0]
    child["caption_visual_contract_sha256"] = child.pop("content_sha256")
    with pytest.raises(ProductionTaskValidationError, match="legacy"):
        validate_production_image_task(invalid)


@pytest.mark.parametrize(
    ("label", "mutate"),
    (
        ("narrative_function", lambda task: task.pop("narrative_function")),
        ("source_beat_ids", lambda task: task.pop("source_beat_ids")),
        ("source_beat_ids", lambda task: task.__setitem__("source_beat_ids", [])),
        (
            "Literal",
            lambda task: task["visual_proposition"].__setitem__("entity_visibility", []),
        ),
        (
            "Symbolic",
            lambda task: task["visual_proposition"].__setitem__("surrogate_objects", []),
        ),
        (
            "Abstract",
            lambda task: task["visual_proposition"].__setitem__(
                "entity_visibility",
                [{"entity_id": "C002", "must_be_visible": True, "natural_language": "福贵"}],
            ),
        ),
        (
            "legacy",
            lambda task: task["prompt_binding"]["caption_contract_bindings"][0].update(
                {
                    "caption_visual_contract_sha256": task["prompt_binding"]
                    ["caption_contract_bindings"][0].pop("content_sha256")
                }
            ),
        ),
        ("prompt_sha256", lambda task: task.__setitem__("prompt_sha256", HEX_B)),
        (
            "aggregate SHA",
            lambda task: task.__setitem__("caption_visual_contract_sha256", HEX_B),
        ),
        (
            "scene_id",
            lambda task: task["prompt_binding"].__setitem__("scene_id", "EVIL"),
        ),
    ),
)
def test_persisted_task_loader_rejects_schema_and_semantic_attacks(
    tmp_path: Path, label: str, mutate: Any
) -> None:
    mode = label if label in {"Literal", "Symbolic", "Abstract"} else "Literal"
    task = _schema_task(mode)
    mutate(task)
    _write_task_queue(tmp_path, task)
    with pytest.raises(SceneAssetError, match=label):
        _tasks(tmp_path)


@pytest.mark.parametrize("mode", ["Literal", "Symbolic", "Abstract"])
def test_persisted_task_loader_accepts_each_legal_proposition_mode(
    tmp_path: Path, mode: str
) -> None:
    task = _schema_task(mode)
    _write_task_queue(tmp_path, task)
    assert _tasks(tmp_path) == {task["task_id"]: task}
