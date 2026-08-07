from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .style_profiles import (
    DEFAULT_STYLE_PROFILE_ID,
    load_style_profile,
)


class ProviderPolicyError(RuntimeError):
    """A generation provider is not permitted for new work."""


# The only sanctioned scene-image generation provider for NEW work. Host
# ImageGen is the default and the sole vetted lane; the flagged web providers
# (huozhe-r1 shipped 173 assets on ``gemini-web`` / ``flow-web``) must fail
# closed so no new artifact can be produced by an unvetted provider. Legacy
# artifacts are not rehashed (see design §12.1); this gate governs new work.
SANCTIONED_IMAGE_PROVIDER = "host-imagegen"
FORBIDDEN_IMAGE_PROVIDERS = frozenset({"gemini-web", "flow-web", "imagegen"})


def validate_provider(provider: str) -> str:
    """Fail closed unless ``provider`` is the sanctioned generation provider.

    Returns the normalized provider name on success so callers can bind the
    exact string they validated.
    """

    name = (provider or "").strip()
    if not name:
        raise ProviderPolicyError("generation provider is required")
    if name in FORBIDDEN_IMAGE_PROVIDERS:
        raise ProviderPolicyError(
            f"generation provider {name!r} is forbidden for new work; "
            f"use {SANCTIONED_IMAGE_PROVIDER!r}"
        )
    if name != SANCTIONED_IMAGE_PROVIDER:
        raise ProviderPolicyError(
            f"generation provider {name!r} is not on the sanctioned allowlist; "
            f"use {SANCTIONED_IMAGE_PROVIDER!r}"
        )
    return name


PROJECT_DIRECTORIES = (
    "00_topic_选题",
    "01_research_资料搜集/raw",
    "01_research_资料搜集/normalized",
    "01_research_资料搜集/sources/cover",
    "02_story_script_故事脚本",
    "03_images_生成图片/prompts",
    "03_images_生成图片/generated",
    "03_images_生成图片/approved",
    "04_audio",
    "05_director",
    "06_visual_production",
    "07_render/workspaces",
    "05_voice_人声",
    "06_music_音乐",
    "07_timeline_时间线",
    "08_render_合成/preview",
    "08_render_合成/final",
    "09_qc_质检",
    "10_delivery_交付",
    "assets/generated/anchors",
    "assets/generated/sheets",
    "assets/generated/scenes",
    "assets/audio/opening",
    "assets/audio/bgm",
    "qa",
    "renders",
    "manifests/stages",
    "logs/approval_events",
    "logs",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, payload: Any, *, overwrite: bool = True) -> bool:
    if path.exists() and not overwrite:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)
    return True


def write_text(path: Path, content: str, *, overwrite: bool = True) -> bool:
    if path.exists() and not overwrite:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)
    return True


def probe_media(path: Path) -> dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
        "-of",
        "json",
        str(path),
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    return json.loads(completed.stdout)


def build_initial_project_spec(book_title: str, author: str) -> dict[str, Any]:
    """Return the exact untouched Phase 2 project-spec placeholder."""
    return {
        "version": 2,
        "projectType": "classic-book-narration",
        "title": book_title,
        "titleLines": [book_title],
        "book": {
            "title": book_title,
            "author": author,
            "sourceManifest": "01_research_资料搜集/SOURCE_MANIFEST.json",
            "sourceLevel": "pending",
            "factLedger": "01_research_资料搜集/FACT_LEDGER.json",
        },
        "source": {"corrections": [], "chapters": []},
        "narration": {
            "provider": "edge-tts",
            "voice": "",
            "bodyRate": "+0%",
            "leadRate": "+0%",
            "revealRate": "+0%",
            "pitch": "+0Hz",
            "captionMaxChars": 18,
            "captionMinChars": 6,
            "captionMinDuration": 0.55,
        },
        "opening": {
            "mode": "classic-book-flash",
            "leadText": "",
            "revealText": "",
            "flashDuration": 1.667,
            "flashLives": [],
        },
        "visual": {
            "profile": "03_images_生成图片/BOOK_VISUAL_PROFILE.json",
            "characters": "CHARACTERS.md",
            "anchorApproval": "03_images_生成图片/ANCHOR_APPROVAL.json",
        },
        "workflow": {
            "releaseId": "r1",
            "scriptContract": "script.narrator-essay.v1",
            "scriptLock": "02_story_script_故事脚本/SCRIPT_LOCK.json",
            "stateAuthority": "workflow-gates-manifests",
        },
        "audio": {
            "narrationOutput": "assets/audio/narration.m4a",
            "bgmSource": "assets/audio/bgm/source.mp3",
            "bgmLooped": "assets/audio/bgm/looped.m4a",
        },
    }



def initialize_project(
    warehouse: Path,
    slug: str,
    book_title: str,
    author: str,
    reference_video: Path | None = None,
    mode: str = "single-book",
    release_profile_id: str | None = None,
    style_profile_id: str = DEFAULT_STYLE_PROFILE_ID,
    generation_lane: str | None = None,
    orientation: str = "landscape",
    qualification_scope: str = "production",
) -> Path:
    if mode != "single-book":
        raise ValueError("the active pipeline supports only single-book projects")
    if qualification_scope not in {"production", "hbg-parity-pilot"}:
        raise ValueError("qualification_scope must be production or hbg-parity-pilot")
    style_profile = load_style_profile(style_profile_id)
    if mode not in style_profile.supported_workflow_modes:
        raise ValueError(
            f"style {style_profile_id} does not support workflow mode {mode}"
        )
    resolved_generation_lane = style_profile.resolve_generation_lane(generation_lane)
    expected_release_profile_id = style_profile.release_profile_for_orientation(orientation)
    resolved_release_profile_id = release_profile_id or expected_release_profile_id
    if resolved_release_profile_id != expected_release_profile_id:
        raise ValueError(
            f"style {style_profile_id} requires release profile "
            f"{expected_release_profile_id} for {orientation}; refusing incompatible override "
            f"{resolved_release_profile_id}"
        )
    project = warehouse.resolve() / "projects" / slug
    contract_path = project / "project.json"
    if contract_path.is_file():
        try:
            existing = json.loads(contract_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(
                f"existing project contract is unreadable: {contract_path}"
            ) from error
        workflow = existing.get("workflow") if isinstance(existing, dict) else None
        existing_workflow = workflow if isinstance(workflow, dict) else {}
        existing_style = existing_workflow.get(
            "style_profile_id", DEFAULT_STYLE_PROFILE_ID
        )
        existing_mode = existing_workflow.get("mode", "single-book")
        existing_lane = existing_workflow.get("generation_lane")
        existing_orientation = existing_workflow.get("orientation", "landscape")
        existing_release_profile = existing_workflow.get(
            "release_profile_id",
            load_style_profile(existing_style).release_profile_for_orientation(existing_orientation),
        )
        existing_qualification_scope = existing_workflow.get(
            "qualification_scope", "production"
        )
        if existing_lane is None and existing_style == DEFAULT_STYLE_PROFILE_ID:
            existing_lane = load_style_profile(existing_style).resolve_generation_lane(None)
        requested = (
            mode,
            style_profile_id,
            resolved_release_profile_id,
            resolved_generation_lane,
            orientation,
            qualification_scope,
        )
        recorded = (
            existing_mode,
            existing_style,
            existing_release_profile,
            existing_lane,
            existing_orientation,
            existing_qualification_scope,
        )
        if requested != recorded:
            raise ValueError(
                "project already exists with workflow "
                f"mode={existing_mode}, style_profile_id={existing_style}, "
                f"release_profile_id={existing_release_profile}, "
                f"generation_lane={existing_lane}, qualification_scope={existing_qualification_scope}; "
                f"refusing requested "
                f"mode={mode}, style_profile_id={style_profile_id}, "
                f"release_profile_id={resolved_release_profile_id}, "
                f"generation_lane={resolved_generation_lane}, orientation={orientation}, "
                f"qualification_scope={qualification_scope}"
            )
    for relative in PROJECT_DIRECTORIES:
        directory = project / relative
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ".gitkeep").touch(exist_ok=True)

    manifest = {
        "schema_version": "1.0",
        "project_id": slug,
        "book": {"title": book_title, "author": author},
        "status": "initialized",
        "current_stage": "00_topic",
        "created_at": utc_now(),
        "reference_video": str(reference_video.resolve()) if reference_video else None,
        "workflow": {
            "mode": mode,
            "style_profile_id": style_profile.style_id,
            "style_display_name": style_profile.display_name_zh,
            "release_profile_id": resolved_release_profile_id,
            "orientation": orientation,
            "generation_lane": resolved_generation_lane,
            "execution_mode": style_profile.execution_mode,
            "state_source": "derived_gate_evaluator",
            "status_field_role": "compatibility_cache_only",
            "qualification_scope": qualification_scope,
        },
    }
    write_json(contract_path, manifest, overwrite=False)

    project_spec = build_initial_project_spec(book_title, author)
    write_json(project / "PROJECT_SPEC.json", project_spec, overwrite=False)
    write_text(
        project / "SCRIPT_SOURCE.md",
        "# 冻结口播原文\n\n> 脚本批准后由适配器写入；不得在生产阶段静默改写。\n",
        overwrite=False,
    )
    write_text(
        project / "SCRIPT.md",
        "# HBG 旁白执行稿\n\n> 由冻结的 SCRIPT_RELEASE.md 编译生成。\n",
        overwrite=False,
    )
    write_text(
        project / "CHARACTERS.md",
        "# 人物身份与连续性\n\n> 视觉锚点批准后由 BOOK_VISUAL_PROFILE.json 导出。\n",
        overwrite=False,
    )
    write_json(project / "STORYBOARD_BASE.json", [], overwrite=False)
    write_json(
        project / "02_story_script_故事脚本" / "HBG_BRIDGE_INPUT.example.json",
        {
            "schema_version": "hbg-bridge-input.v1",
            "content_package_digest": "",
            "release_text_sha256": "",
            "release_id": "r1",
            "orientation": orientation,
            "brand": {
                "series_name": "一生值得读的世界名著",
                "episode_number": 1,
                "lead_text": "名著值得读，但很多人读不进去。",
                "lead_display_text": "一生值得读的世界名著",
                "reveal_text": f"《{book_title}》，{author}。",
            },
            "narration": {
                "provider": "edge-tts",
                "voice": "zh-CN-YunjianNeural",
                "body_rate": "+0%",
                "lead_rate": "+0%",
                "reveal_rate": "+0%",
                "pitch": "+0Hz",
            },
            "chapters": [],
            "characters": [],
            "storyboard_beats": [],
        },
        overwrite=False,
    )
    write_text(
        project / "PROMPTS.md",
        "# ImageGen 提示词与调用记录\n\n> 每项任务必须绑定 beat、shot、锚点和输出 Hash。\n",
        overwrite=False,
    )

    write_json(
        project / "04_audio" / "AUDIO_STAGE_INPUT.example.json",
        {
            "schema_version": "audio-stage-input.v1",
            "release_id": "r1",
            "provider": "edge-tts",
            "voice": "zh-CN-YunjianNeural",
            "body_rate": "+0%",
            "lead_rate": "+0%",
            "reveal_rate": "+0%",
            "pitch": "+0Hz",
            "lead_text": "名著值得读，但很多人读不进去。",
            "reveal_text": f"《{book_title}》，{author}。",
            "caption_min_chars": 6,
            "caption_max_chars": 18,
            "caption_min_duration": 0.55,
            "lead_start": 0.0,
            "flash_gap_after_lead": 0.05,
            "flash_duration": 1.667,
            "reveal_hold": 0.15,
            "body_gap": 0.1,
            "body_mode": "continuous",
            "bindings": {
                "script_md_sha256": "0" * 64,
                "project_spec_sha256": "0" * 64,
                "hbg_style_sha256": "0" * 64,
                "storyboard_base_sha256": "0" * 64,
                "visual_approval_sha256": "0" * 64,
                "pronunciation_lexicon_sha256": "0" * 64,
            },
        },
        overwrite=False,
    )
    write_json(
        project / "04_audio" / "PRONUNCIATION_LEXICON.example.json",
        {"schema_version": "pronunciation-lexicon.v1", "release_id": "r1", "entries": []},
        overwrite=False,
    )
    write_json(
        project / "04_audio" / "STORYBOARD_AUDIO_PLAN.example.json",
        {
            "schema_version": "storyboard-audio-plan.v1",
            "release_id": "r1",
            "preliminary_manifest_sha256": "0" * 64,
            "beat_dispositions": [
                {"beat_id": "B001", "mode": "retain", "shot_ids": ["audio-b001-01"]}
            ],
            "shots": [
                {
                    "id": "audio-b001-01",
                    "source_beat_ids": ["B001"],
                    "chapter": 1,
                    "cue": "使用真实 display caption 的完整文本",
                    "caption_ids": ["caption-0001"],
                    "description": "根据真实语音窗口编写的具体画面",
                    "required_entities": ["主角"],
                    "forbidden_entities": [],
                    "risk_flags": [],
                    "generation_mode": "2x2",
                    "anchor_refs": ["C001"],
                    "participants": {"count": 1, "allowed": ["C001"]},
                    "motion": "zoom-in",
                    "visual_load": "ordinary",
                    "intentional_hold": False,
                    "hold_reason": "",
                    "semantic_rationale": "字幕与画面共享主角和当前动作",
                    "nonverbal_window": None,
                }
            ],
        },
        overwrite=False,
    )


    write_json(
        project / "06_visual_production" / "SCENE_REVIEW_DECISION.example.json",
        {
            "schema_version": "scene-review-decision.v1",
            "release_id": "r1",
            "director_stage_manifest_sha256": "0" * 64,
            "scene_asset_manifest_sha256": "0" * 64,
            "reviewer": "",
            "decisions": [],
        },
        overwrite=False,
    )
    write_json(
        project / "qa" / "vision_review_provider.example.json",
        {
            "schema_version": "vision-review-provider.v1",
            "active_provider": "claude-sonnet-4.5",
        },
        overwrite=False,
    )
    write_json(
        project / "07_render" / "RENDER_INPUT.example.json",
        {
            "schema_version": "render-stage-input.v1",
            "release_id": "r1",
            "renderer": "streaming_ffmpeg",
            "output_name": f"{slug}-v1.mp4",
            "bgm_source": "assets/audio/bgm/source.mp3",
            "opening": {
                "preview_video": "assets/opening/preview.mp4",
                "final_image_task_id": "SCENE_REPLACE_WITH_APPROVED_TASK",
                "flash_task_ids": ["SCENE_REPLACE_WITH_APPROVED_TASK"],
            },
            "quality": "high",
            "minimum_free_gib": 12,
            "hyperframes_version": "1.0.0",
        },
        overwrite=False,
    )

    if reference_video:
        reference_video = reference_video.expanduser().resolve()
        if not reference_video.is_file():
            raise FileNotFoundError(f"Reference video not found: {reference_video}")
        reference = {
            "source_path": str(reference_video),
            "role": "style_and_timing_reference_only",
            "publishable_asset": False,
            "probed_at": utc_now(),
            "ffprobe": probe_media(reference_video),
        }
        write_json(project / "00_topic_选题" / "reference.json", reference)

    return project
