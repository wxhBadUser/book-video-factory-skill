"""P0-4: character registry resolves every contract-visible character."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from book_video_factory.visual_foundation.character_registry import (
    CharacterRegistry, CharacterRegistryError, parse_character_registry,
    resolve_visible_characters, verify_registry_covers_characters,
)

SANTIAGO = {
    "character_id": "C001", "name": "圣地亚哥", "narrative_role": "protagonist",
    "life_stage": "elder",
    "immutable_traits": ["瘦削脸型", "深陷眼窝", "晒黑且布满皱纹的皮肤"],
    "allowed_changes": ["表情", "姿势", "轻微衣物磨损"],
    "wardrobe_states": ["褪色浅色衬衫", "旧长裤"],
    "identity_anchor_task_ids": ["ANCHOR_C001_FRONT", "ANCHOR_C001_THREE_QUARTER"],
    "identity_board_task_id": "BOARD_C001",
}
MANOLIN = {
    "character_id": "C002", "name": "马诺林", "narrative_role": "supporting",
    "life_stage": "boy",
    "immutable_traits": ["少年体格", "晒黑皮肤"],
    "allowed_changes": ["表情", "姿态"],
    "wardrobe_states": ["旧衬衫"],
    "identity_anchor_task_ids": ["ANCHOR_C002_FRONT"],
    "identity_board_task_id": "BOARD_C002",
}
REGISTRY = [SANTIAGO, MANOLIN]


def test_parse_builds_typed_registry():
    registry = parse_character_registry(REGISTRY)
    assert isinstance(registry, CharacterRegistry)
    assert registry.get("C001").name == "圣地亚哥"
    assert registry.get("C002").name == "马诺林"


def test_duplicate_character_id_rejected():
    with pytest.raises(CharacterRegistryError):
        parse_character_registry([SANTIAGO, SANTIAGO])


def test_resolve_visible_characters_fail_closed_on_unknown():
    registry = parse_character_registry(REGISTRY)
    resolved = resolve_visible_characters(registry, ["C001", "C002"])
    assert resolved["C001"]["name"] == "圣地亚哥"
    with pytest.raises(CharacterRegistryError):
        resolve_visible_characters(registry, ["C001", "C999"])


def test_verify_identity_board_required_per_character():
    registry = parse_character_registry(REGISTRY)
    with pytest.raises(CharacterRegistryError):
        verify_registry_covers_characters(registry, ["C001", "C002"],
                                          board_task_ids={"C001": "BOARD_C001"})  # C002 缺板


def test_registry_document_validates_against_schema():
    import json
    import jsonschema
    registry = parse_character_registry(REGISTRY)
    doc = registry.to_document(release_id="omats-v25")
    schema = json.loads(
        (Path(__file__).parents[1] / "schemas" / "character_registry.v1.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.validate(doc, schema)
