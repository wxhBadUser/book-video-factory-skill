from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


class HbgCharacterExportError(ValueError):
    """Character identity input is incomplete or contains placeholders."""


_PLACEHOLDER = re.compile(r"(?:待定|后补|占位|placeholder|todo|tbd)", re.IGNORECASE)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HbgCharacterExportError(f"{label} is required")
    text = value.strip()
    if _PLACEHOLDER.search(text):
        raise HbgCharacterExportError(f"{label} contains placeholder text")
    return text


def _items(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise HbgCharacterExportError(f"{label} must be a nonempty array")
    result = [_text(item, f"{label}[{index}]") for index, item in enumerate(value)]
    if len(result) != len(set(result)):
        raise HbgCharacterExportError(f"{label} contains duplicates")
    return result


def export_characters_markdown(characters: Sequence[Mapping[str, Any]]) -> str:
    if not isinstance(characters, Sequence) or isinstance(characters, (str, bytes)) or not characters:
        raise HbgCharacterExportError("characters must be a nonempty array")
    lines = ["# 人物身份与连续性", "", "本文件由已批准脚本绑定的 HBG Bridge Input 确定性导出。不得只凭人物名字维持身份。", ""]
    seen: set[str] = set()
    for index, character in enumerate(characters):
        if not isinstance(character, Mapping):
            raise HbgCharacterExportError(f"character {index} must be an object")
        cid = _text(character.get("character_id"), f"characters[{index}].character_id")
        if cid in seen:
            raise HbgCharacterExportError(f"duplicate character_id: {cid}")
        seen.add(cid)
        name = _text(character.get("name"), f"characters[{index}].name")
        role = _text(character.get("role"), f"characters[{index}].role")
        stage = _text(character.get("life_stage"), f"characters[{index}].life_stage")
        status = _text(character.get("anchor_status"), f"characters[{index}].anchor_status")
        groups = [
            ("不可变身份特征", _items(character.get("immutable_traits"), "immutable_traits")),
            ("允许变化", _items(character.get("changeable_traits"), "changeable_traits")),
            ("服装", _items(character.get("wardrobe"), "wardrobe")),
            ("人物关系", _items(character.get("relationships"), "relationships")),
        ]
        lines.extend([
            f"## {cid}｜{name}", "", f"- 角色：{role}", f"- 人生阶段：{stage}",
            f"- 锚点状态：`{status}`", "",
        ])
        for heading, values in groups:
            lines.extend([f"### {heading}", ""])
            lines.extend(f"- {value}" for value in values)
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
