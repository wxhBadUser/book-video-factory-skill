from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Mapping

from .script_export import ScriptExports


class HbgProjectSpecError(ValueError):
    """A book project cannot be represented by the HBG project contract."""


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HbgProjectSpecError(f"{label} is required")
    return value.strip()


def _relative(value: str, label: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise HbgProjectSpecError(f"{label} must be a safe project-relative path")
    return path.as_posix()


def build_project_spec(
    project_contract: Mapping[str, Any],
    handoff: Any,
    bridge_input: Mapping[str, Any],
    exports: ScriptExports,
) -> dict[str, Any]:
    book = project_contract.get("book")
    if not isinstance(book, Mapping):
        raise HbgProjectSpecError("project book identity is required")
    title = _require_text(book.get("title"), "book title")
    author = _require_text(book.get("author"), "book author")
    reveal = _require_text(bridge_input.get("brand", {}).get("reveal_text"), "brand reveal_text")
    if title not in reveal:
        raise HbgProjectSpecError("book title is inconsistent with the approved reveal text")
    if author not in reveal:
        raise HbgProjectSpecError("book author is inconsistent with the approved reveal text")
    narration = bridge_input.get("narration")
    if not isinstance(narration, Mapping) or narration.get("provider") != "edge-tts":
        raise HbgProjectSpecError("the active bridge requires edge-tts narration")
    brand = bridge_input.get("brand")
    if not isinstance(brand, Mapping):
        raise HbgProjectSpecError("brand configuration is required")

    chapters = [
        {"id": chapter.chapter_id, "title": chapter.title, "cue": chapter.cue}
        for chapter in exports.chapters
    ]
    if not chapters:
        raise HbgProjectSpecError("at least one chapter is required")

    return {
        "version": 2,
        "projectType": "classic-book-narration",
        "title": title,
        "titleLines": [title],
        "book": {
            "title": title,
            "author": author,
            "sourceManifest": _relative("01_research_资料搜集/SOURCE_MANIFEST.json", "source manifest"),
            "sourceLevel": "A",
            "factLedger": _relative("01_research_资料搜集/FACT_LEDGER.json", "fact ledger"),
        },
        "brand": {
            "seriesName": brand["series_name"],
            "episodeNumber": brand["episode_number"],
        },
        "source": {
            "openingSentence": "",
            "corrections": [],
            "chapters": chapters,
        },
        "narration": {
            "provider": "edge-tts",
            "voice": narration["voice"],
            "bodyRate": narration["body_rate"],
            "leadRate": narration["lead_rate"],
            "revealRate": narration["reveal_rate"],
            "pitch": narration["pitch"],
            "leadText": brand["lead_text"],
            "revealText": brand["reveal_text"],
            "flashDuration": 1.667,
            "captionMaxChars": 18 if bridge_input.get("orientation") == "landscape" else 14,
            "captionMinChars": 6,
            "captionMinDuration": 0.55,
        },
        "opening": {
            "mode": "classic-book-flash",
            "leadDisplayText": brand["lead_display_text"],
            "flashMedia": _relative("assets/opening/flash.mp4", "opening flash media"),
            "finalImage": _relative("assets/opening/final.jpg", "opening final image"),
            "flashLives": [],
        },
        "visual": {
            "profile": _relative("03_images_生成图片/BOOK_VISUAL_PROFILE.json", "visual profile"),
            "characters": _relative("CHARACTERS.md", "characters"),
            "anchorApproval": _relative("03_images_生成图片/ANCHOR_APPROVAL.json", "anchor approval"),
        },
        "workflow": {
            "releaseId": handoff.release_id,
            "scriptContract": "script.narrator-essay.v1",
            "scriptLock": _relative("02_story_script_故事脚本/SCRIPT_LOCK.json", "script lock"),
            "stateAuthority": "workflow-gates-manifests",
            "contentPackageDigest": handoff.package_digest,
            "releaseTextSha256": handoff.release_text_sha256,
            "hbgUpstreamCommit": handoff.hbg_commit,
        },
        "audio": {
            "narrationOutput": _relative("assets/audio/narration.m4a", "narration output"),
            "bgmSource": _relative("assets/audio/bgm/source.mp3", "BGM source"),
            "bgmLooped": _relative("assets/audio/bgm/looped.m4a", "looped BGM"),
        },
    }
