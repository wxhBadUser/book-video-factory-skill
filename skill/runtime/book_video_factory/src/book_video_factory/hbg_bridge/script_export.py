from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


class HbgScriptExportError(ValueError):
    """Frozen script cannot be exported without changing or ambiguously cueing it."""


@dataclass(frozen=True)
class ExportedChapter:
    chapter_id: str
    title: str
    section_ids: tuple[str, ...]
    text: str
    cue: str


@dataclass(frozen=True)
class ScriptExports:
    source_bytes: bytes
    script_markdown: str
    script_json: dict[str, Any]
    chapters: tuple[ExportedChapter, ...]


def _cn(value: int) -> str:
    digits = "零一二三四五六七八九"
    if not 1 <= value <= 99:
        raise HbgScriptExportError(f"chapter number out of range: {value}")
    if value < 10:
        return digits[value]
    tens, ones = divmod(value, 10)
    return f"{'' if tens == 1 else digits[tens]}十{'' if ones == 0 else digits[ones]}"


def _unique_prefix(text: str, release_text: str) -> str:
    if not text:
        raise HbgScriptExportError("chapter text is empty")
    minimum = min(8, len(text))
    for length in range(minimum, len(text) + 1):
        cue = text[:length]
        if release_text.count(cue) == 1:
            return cue
    raise HbgScriptExportError("cannot derive a unique cue from the frozen chapter text")


def build_script_exports(
    title: str,
    script: Mapping[str, Any],
    chapters: Sequence[Mapping[str, Any]],
) -> ScriptExports:
    if not isinstance(title, str) or not title.strip():
        raise HbgScriptExportError("book title is required")
    release = script.get("release_version")
    if not isinstance(release, Mapping):
        raise HbgScriptExportError("release_version is required")
    release_text = release.get("text")
    sections = release.get("sections")
    if not isinstance(release_text, str) or not release_text:
        raise HbgScriptExportError("frozen release text is required")
    if not isinstance(sections, list) or not sections:
        raise HbgScriptExportError("frozen release sections are required")
    section_ids: list[str] = []
    section_text: dict[str, str] = {}
    for index, section in enumerate(sections):
        if not isinstance(section, Mapping):
            raise HbgScriptExportError(f"release section {index} must be an object")
        section_id = section.get("section_id")
        text = section.get("text")
        if not isinstance(section_id, str) or not section_id or section_id in section_text:
            raise HbgScriptExportError("release section IDs must be nonempty and unique")
        if not isinstance(text, str) or not text:
            raise HbgScriptExportError(f"release section {section_id} has no text")
        section_ids.append(section_id)
        section_text[section_id] = text
    if "".join(section_text[sid] for sid in section_ids) != release_text:
        raise HbgScriptExportError("release text is not the exact concatenation of frozen sections")

    flattened: list[str] = []
    exported: list[ExportedChapter] = []
    for index, chapter in enumerate(chapters, start=1):
        if not isinstance(chapter, Mapping):
            raise HbgScriptExportError(f"chapter {index} must be an object")
        chapter_id = chapter.get("chapter_id")
        chapter_title = chapter.get("title")
        covered = chapter.get("section_ids")
        if not isinstance(chapter_id, str) or not chapter_id:
            raise HbgScriptExportError(f"chapter {index} requires chapter_id")
        if not isinstance(chapter_title, str) or not chapter_title.strip():
            raise HbgScriptExportError(f"chapter {index} requires title")
        if not isinstance(covered, list) or not covered:
            raise HbgScriptExportError(f"chapter {index} requires section_ids")
        for section_id in covered:
            if section_id not in section_text or section_id in flattened:
                raise HbgScriptExportError("chapter section coverage is invalid")
            flattened.append(section_id)
        text = "".join(section_text[sid] for sid in covered)
        exported.append(ExportedChapter(
            chapter_id=chapter_id,
            title=chapter_title.strip(),
            section_ids=tuple(covered),
            text=text,
            cue=_unique_prefix(text, release_text),
        ))
    if flattened != section_ids:
        raise HbgScriptExportError("chapter section coverage must preserve every frozen section exactly once")

    lines = [f"# 旁白稿：{title.strip()}", ""]
    for index, chapter in enumerate(exported, start=1):
        lines.extend([f"## 第{_cn(index)}章｜{chapter.title}", "", chapter.text, ""])
    markdown = "\n".join(lines).rstrip() + "\n"
    return ScriptExports(
        source_bytes=release_text.encode("utf-8"),
        script_markdown=markdown,
        script_json=copy.deepcopy(dict(script)),
        chapters=tuple(exported),
    )
