from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from book_video_factory.audio_stage.status import audio_stage_status
from book_video_factory.host_orchestration import derive_next_action
from book_video_factory.locked_script import verify_locked_script
from book_video_factory.manifests import sha256_file
from book_video_factory.delivery_stage import FinalMasterApprovalError, verify_final_master_approval
from book_video_factory.gates import current_approvals
from book_video_factory.style_profiles import project_workflow
from book_video_factory.repository_integrity import repository_integrity_block
from book_video_factory.source_ingestion import source_rights_state
from book_video_factory.visual_stage.approval import visual_stage_next_status


def _json(path: Path) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value=json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError):
        return None
    return value if isinstance(value,dict) else None


def _release_id(root: Path) -> str:
    for relative in (
        "08_render_合成/final/FINAL_RENDER_MANIFEST.json",
        "07_render/RENDER_MANIFEST.json",
        "06_visual_production/SCENE_ASSET_APPROVAL.json",
        "05_director/DIRECTOR_STAGE_MANIFEST.json",
        "04_audio/AUDIO_STAGE_MANIFEST.json",
        "03_images_生成图片/ANCHOR_APPROVAL.json",
        "02_story_script_故事脚本/SCRIPT_LOCK.json",
    ):
        value=_json(root/relative)
        if value and isinstance(value.get("release_id"),str): return value["release_id"]
    spec=_json(root/"PROJECT_SPEC.json") or {}
    workflow=spec.get("workflow") if isinstance(spec.get("workflow"),dict) else {}
    return str(workflow.get("releaseId","r1"))


def _v2_host_orchestration(root: Path, release_id: str) -> dict[str, Any] | None:
    lock_status = verify_locked_script(root)
    status = lock_status.get("status")
    if status in {"missing_locked_script", "invalid_locked_script"}:
        # No V2 locked contract yet; fall through to the legacy Phase 0-7 path.
        return None
    if status != "script_locked":
        next_action = {
            "blocked_rights": "clear and verify source rights before locking the V2 script",
            "blocked_by_script_integrity": "recompute and restore the locked script hash evidence",
        }.get(status, "resolve the V2 locked script contract blocking state")
        return {
            "release_id": release_id,
            "stage": "locked_script",
            "status": status,
            "next_action": next_action,
            "command": None,
        }
    action = derive_next_action(root)
    if action is not None:
        return {
            "release_id": release_id,
            "stage": "host_orchestration",
            "status": "host_action_pending",
            "human_review_required": False,
            "next_action": f"execute Host action {action['action_id']} and register its terminal event",
            "command": None,
            "host_action": action,
        }
    return {
        "release_id": release_id,
        "stage": "locked_script",
        "status": "script_locked",
        "next_action": "locked script verified; await the next legal Host action",
        "command": None,
    }


def _raw_pipeline_status(project: Path) -> dict[str, Any]:
    root=project.expanduser().resolve(); release_id=_release_id(root)
    integrity_block = repository_integrity_block()
    if integrity_block is not None:
        return {"release_id": release_id, **integrity_block}
    v2 = _v2_host_orchestration(root, release_id)
    if v2 is not None:
        return v2
    final=_json(root/"08_render_合成/final/FINAL_RENDER_MANIFEST.json")
    if final:
        video=root/str(final.get("video_path","")); qa=root/str(final.get("qa_report_path",""))
        if not (video.is_file() and qa.is_file() and sha256_file(video)==final.get("video_sha256") and sha256_file(qa)==final.get("qa_report_sha256")):
            return {"release_id":release_id,"stage":"render","status":"blocked_by_final_integrity","next_action":"restore or regenerate the final render","command":None}
        final_status=final.get("next_stage_status")
        if final_status=="awaiting_encoded_qa":
            return {
                "release_id":release_id,"stage":"encoded_visual_qa","status":final_status,
                "next_action":"run machine encoded QA (semantic, reality, identity frame checks)",
                "command":f"python book_video_factory/scripts/review_encoded_master.py --project '{root}' --decision '{root}/09_qc/ENCODED_REVIEW_DECISION.json'",
            }
        if final_status=="blocked_by_encoded_visual_qa":
            return {"release_id":release_id,"stage":"encoded_visual_qa","status":final_status,"next_action":"repair the rejected local scenes or render and regenerate encoded QA evidence","command":None}
        try:
            verify_final_master_approval(root)
        except FinalMasterApprovalError:
            return {
                "release_id":release_id,"stage":"final_master_review","status":"awaiting_final_master_approval",
                "next_action":"review the encoded MP4 and QA evidence, then approve the local master",
                "command":f"python book_video_factory/scripts/approve_final_master.py --project '{root}' --reviewer '<HUMAN>' --note '<APPROVAL_NOTE>'",
            }
        if source_rights_state(root) != "cleared":
            return {"release_id":release_id,"stage":"release_rights","status":"blocked_by_release_rights","next_action":"clear source rights before public release","command":None}
        return {"release_id":release_id,"stage":"complete","status":"complete","next_action":"publish or archive the approved master","command":None}
    render=_json(root/"07_render/RENDER_MANIFEST.json")
    if render:
        from book_video_factory.render_stage.preflight import RenderPreflightError, verify_render_preflight
        try:
            verify_render_preflight(root)
        except RenderPreflightError:
            preflight=_json(root/"07_render/RENDER_PREFLIGHT.json")
            blocked=bool(preflight and preflight.get("status")=="blocked")
            return {
                "release_id":release_id,"stage":"render_preflight",
                "status":"blocked_by_render_preflight" if blocked else "awaiting_render_preflight",
                "next_action":"resolve the exact recorded preflight blocker and rerun HBG preflight" if blocked else "run HBG style and long-render preflight",
                "command":f"python book_video_factory/scripts/preflight_render.py --project '{root}' --input '{root}/07_render/RENDER_INPUT.json'",
            }
        from book_video_factory.render_stage.encoded_visual_qa import EncodedVisualQaError, verify_encoded_frame_plan
        try:
            verify_encoded_frame_plan(root)
        except EncodedVisualQaError:
            return {
                "release_id":release_id,"stage":"encoded_frame_plan","status":"awaiting_encoded_frame_plan",
                "next_action":"build the deterministic semantic timestamp plan before HBG encoded verification",
                "command":f"python book_video_factory/scripts/build_encoded_frame_plan.py --project '{root}'",
            }
        return {"release_id":release_id,"stage":"render","status":"ready_for_hbg_render","next_action":"execute HBG render and encoded QA","command":f"python book_video_factory/scripts/run_render_stage.py execute --project '{root}' --input '{root}/07_render/RENDER_INPUT.json'"}
    scene_approval=_json(root/"06_visual_production/SCENE_ASSET_APPROVAL.json")
    if scene_approval and scene_approval.get("human_approved") and scene_approval.get("next_stage_status")=="ready_for_render":
        render_input_path=root/"07_render/RENDER_INPUT.json"
        render_input=_json(render_input_path)
        if render_input and render_input.get("renderer") in {"streaming_ffmpeg", "static_streaming_ffmpeg"}:
            opening=render_input.get("opening") if isinstance(render_input.get("opening"),dict) else {}
            preview=opening.get("preview_video")
            preview_path=root/str(preview) if isinstance(preview,str) and preview else None
            preview_manifest=root/"07_render/OPENING_PREVIEW_MANIFEST.json"
            if preview_path is None or not preview_path.is_file() or not preview_manifest.is_file():
                return {
                    "release_id":release_id,"stage":"opening_preview","status":"awaiting_opening_preview",
                    "next_action":"generate and HBG-validate the opening preview before streaming render",
                    "command":f"python book_video_factory/scripts/run_render_stage.py preview --project '{root}' --input '{render_input_path}'",
                }
            from book_video_factory.render_stage.mix_calibration import opening_mix_status
            mix_status, calibration_path = opening_mix_status(root, render_input_path)
            if mix_status == "awaiting_opening_mix_calibration":
                return {
                    "release_id":release_id,"stage":"opening_mix","status":mix_status,
                    "next_action":"probe the BGM and record a fixed-gain 15-20 second opening mix candidate",
                    "command":f"python book_video_factory/scripts/calibrate_opening_mix.py --project '{root}' --input '{render_input_path}'",
                }
            # mix_status == "ready_for_render_preflight": machine calibration
            # passed; opening mix is machine validation, not a human gate.
        return {"release_id":release_id,"stage":"render_preflight","status":"awaiting_render_preflight","next_action":"prepare the locked workspace and run HBG style and long-render preflight","command":f"python book_video_factory/scripts/preflight_render.py --project '{root}' --input '{render_input_path}'"}
    review=_json(root/"06_visual_production/SCENE_REVIEW_REPORT.json")
    if review:
        status=str(review.get("next_stage_status","blocked_by_scene_review"))
        return {"release_id":release_id,"stage":"scene_visual_review","status":status,"next_action":"repair failed scene images and rebuild review" if status=="blocked_by_scene_review" else "scene vision QA passed; proceed to render preflight","command":None}
    scene_manifest=_json(root/"06_visual_production/SCENE_ASSET_MANIFEST.json")
    if scene_manifest:
        count=int(scene_manifest.get("registered_asset_count",0)); total=int(scene_manifest.get("task_count",0))
        return {"release_id":release_id,"stage":"scene_assets","status":scene_manifest.get("next_stage_status","awaiting_scene_assets"),"progress":{"registered":count,"total":total},"next_action":"register remaining real Host ImageGen outputs" if count<total else "write semantic/reality/identity decisions and build review","command":None}
    director=_json(root/"05_director/DIRECTOR_STAGE_MANIFEST.json")
    if director:
        return {"release_id":release_id,"stage":"director","status":"awaiting_scene_assets","next_action":"run Host ImageGen from 05_director/IMAGE_TASKS.jsonl and register each real PNG","command":None}
    try:
        visual=visual_stage_next_status(root,release_id)
    except Exception:
        visual="blocked_by_visual_stage"
    try:
        workflow = project_workflow(root)
        vf_policy = workflow["visual_foundation_policy"]
    except Exception:
        workflow = {}
        vf_policy = None
    narration_policy = workflow.get("narration_provider_policy", "legacy_edge")
    if visual in {"ready_for_edge_tts", "ready_for_narration"}:
        if vf_policy == "required":
            from book_video_factory.visual_foundation.approval import visual_foundation_status
            vf_status = visual_foundation_status(root, release_id)
            if vf_status != "visual_foundation_approved":
                return {
                    "release_id": release_id,
                    "stage": "visual_foundation",
                    "status": vf_status,
                    "next_action": "build and human-approve the visual foundation before formal narration or scene generation",
                    "command": (
                        "python book_video_factory/scripts/build_visual_foundation.py "
                        f"--project '{root}' --foundation-input '{root}/03_images_生成图片/VISUAL_FOUNDATION_INPUT.json'"
                    ),
                }
        if narration_policy == "minimax_required":
            voice_foundation = root / "04_audio/VOICE_FOUNDATION.json"
            if not voice_foundation.is_file():
                return {
                    "release_id": release_id,
                    "stage": "minimax_voice_foundation",
                    "status": "awaiting_minimax_voice_foundation",
                    "next_action": "select and verify a professional MiniMax narration voice before creating any expressive audio",
                    "command": (
                        "python book_video_factory/scripts/build_voice_foundation.py "
                        f"--release-id '{release_id}' --strategy system_voice "
                        "--voice-id '<MINIMAX_SYSTEM_VOICE_ID>' --verify "
                        f"--out '{voice_foundation}'"
                    ),
                }
    try:
        audio=audio_stage_status(root,release_id)
    except Exception:
        audio=None
    if audio=="ready_for_image_task_planning":
        from book_video_factory.semantic_alignment.scene_continuity import (
            SceneContinuityError,
            load_current_scene_continuity_document,
        )
        try:
            load_current_scene_continuity_document(root)
        except SceneContinuityError:
            return {
                "release_id": release_id,
                "stage": "scene_continuity",
                "status": "awaiting_scene_continuity_spans",
                "next_action": "build the current semantic scene spans before compiling the director timeline",
                "command": (
                    "python book_video_factory/scripts/build_scene_continuity_spans.py "
                    f"--project '{root}'"
                ),
            }
        return {"release_id":release_id,"stage":"director","status":"ready_for_director","next_action":"compile real-audio director timeline and production image tasks","command":f"python book_video_factory/scripts/build_director_stage.py --project '{root}'"}
    if audio and audio not in {"blocked_by_visual_approval","ready_for_edge_tts","ready_for_narration"}:
        return {"release_id":release_id,"stage":"audio","status":audio,"next_action":"complete or repair Phase 4 provider audio/VTT evidence","command":f"python book_video_factory/scripts/run_audio_stage.py status --project '{root}' --release-id '{release_id}'"}
    if visual in {"ready_for_edge_tts", "ready_for_narration"}:
        provider = "minimax" if narration_policy == "minimax_required" else "edge-tts"
        status = "ready_for_narration" if provider == "minimax" else "ready_for_edge_tts"
        next_action = (
            "generate MiniMax narration with provider timestamps and auditable evidence"
            if provider == "minimax"
            else "generate continuous legacy Edge TTS and VTT"
        )
        return {"release_id":release_id,"stage":"audio","status":status,"next_action":next_action,"command":f"python book_video_factory/scripts/run_audio_stage.py generate --project '{root}' --input '{root}/04_audio/AUDIO_STAGE_INPUT.json' --lexicon '{root}/04_audio/PRONUNCIATION_LEXICON.json' --provider {provider}"}
    if (root/"03_images_生成图片/VISUAL_STAGE_MANIFEST.json").is_file():
        return {"release_id":release_id,"stage":"visual_anchor_lookdev","status":visual,"next_action":"complete real anchors, LookDev, contact sheet and human visual approval","command":None}
    if (root/"02_story_script_故事脚本/HBG_BRIDGE_MANIFEST.json").is_file():
        return {"release_id":release_id,"stage":"visual_anchor_lookdev","status":"ready_for_visual_stage","next_action":"author VISUAL_STAGE_INPUT.draft.json and compile visual tasks","command":f"python book_video_factory/scripts/build_visual_stage.py --project '{root}' --visual-input '{root}/03_images_生成图片/VISUAL_STAGE_INPUT.draft.json'"}
    if (root/"02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json").is_file():
        script_approval = current_approvals(root, release_id).get("script")
        if script_approval is None:
            subjects = (
                "02_story_script_故事脚本/SCRIPT_RELEASE.md",
                "02_story_script_故事脚本/SCRIPT_AUDIT.md",
                "02_story_script_故事脚本/SCRIPT_METRICS.json",
                "02_story_script_故事脚本/SCRIPT_LOCK.json",
                "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json",
            )
            subject_args = " ".join(f"--subject '{relative}'" for relative in subjects)
            return {
                "release_id": release_id,
                "stage": "script_approval",
                "status": "awaiting_script_approval",
                "next_action": "review the exact frozen release and audit evidence, then approve all five hash-bound subjects",
                "command": (
                    "python book_video_factory/scripts/workflow.py approve "
                    f"--project '{root}' --release-id '{release_id}' --gate script "
                    "--decision approved --reviewer '<HUMAN>' "
                    f"{subject_args} --note '<APPROVAL_NOTE>'"
                ),
            }
        return {
            "release_id": release_id,
            "stage": "hbg_bridge",
            "status": "ready_for_hbg_bridge",
            "next_action": "build the HBG bridge from the current script approval",
            "command": (
                "python book_video_factory/scripts/build_hbg_bridge.py "
                f"--project '{root}' --bridge-input "
                f"'{root / '02_story_script_故事脚本/HBG_BRIDGE_INPUT.json'}'"
            ),
        }
    return {"release_id":release_id,"stage":"content","status":"awaiting_content_package","next_action":"complete Level-A research and build the locked content package","command":None}


def _generation_projection(root: Path) -> dict[str, Any]:
    plan = _json(root / "06_visual_production/GENERATION_PLAN.json")
    run = _json(root / "06_visual_production/GENERATION_RUN_MANIFEST.json")
    if plan is None or run is None:
        return {
            "ready_generation_wave": [],
            "concurrency_limit": {"configured": 5, "wave": 5, "hard_max": 10},
            "retry_jobs": [],
        }
    configured = int(plan.get("concurrency", 5))
    hard_max = int(plan.get("max_concurrency", 10))
    retry_jobs = [
        str(item["job_id"])
        for item in run.get("jobs", [])
        if isinstance(item, dict)
        and item.get("status") == "failed"
        and isinstance(item.get("job_id"), str)
    ]
    try:
        from book_video_factory.production_visuals.scheduler import next_generation_wave

        wave = next_generation_wave(root)
        ready = [
            {
                "job_id": str(job["job_id"]),
                "generation_mode": str(job["generation_mode"]),
                "task_ids": list(job["task_ids"]),
                "dependencies": list(job["dependencies"]),
            }
            for job in wave.jobs
        ]
    except Exception:
        ready = []
    return {
        "ready_generation_wave": ready,
        "concurrency_limit": {
            "configured": configured,
            "wave": min(configured, 5),
            "hard_max": hard_max,
        },
        "retry_jobs": retry_jobs,
    }


def _enhance_status(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    generation = _generation_projection(root)
    result.update(generation)
    stage = str(result.get("stage", "unknown"))
    status = str(result.get("status", "unknown"))
    plan_exists = (root / "06_visual_production/GENERATION_PLAN.json").is_file()
    if stage in {"director", "scene_assets"}:
        if not plan_exists:
            result.update({
                "stage": "generation_plan",
                "status": "awaiting_generation_plan",
                "next_action": "build the deterministic generation DAG and bounded execution plan",
                "command": (
                    "python book_video_factory/scripts/plan_generation_run.py "
                    f"--project '{root}' --concurrency 5"
                ),
            })
        elif generation["ready_generation_wave"]:
            result.update({
                "stage": "generation_wave",
                "status": (
                    "retry_generation_jobs"
                    if generation["retry_jobs"]
                    else "ready_generation_wave"
                ),
                "next_action": "execute only the reported ready generation wave",
                "command": (
                    "python book_video_factory/scripts/next_generation_wave.py "
                    f"--project '{root}'"
                ),
            })
        stage = str(result["stage"])
        status = str(result["status"])

    # Canonical production state pauses for exactly THREE human gates:
    #   1. SCRIPT_APPROVAL              (awaiting_script_approval)
    #   2. VISUAL_DIRECTION_APPROVAL    (blocked_by_visual_approval)
    #   3. FINAL_MASTER_APPROVAL        (awaiting_final_master_approval)
    # Every other check (scene assets, encoded QA, mix, visual foundation,
    # voice audition, source audit, timeline) is machine validation: it may
    # block with a blocked_* status but never pauses for a human approval.
    human_review_required = any(
        token in status
        for token in ("awaiting_script_approval", "blocked_by_visual_approval", "awaiting_final_master_approval")
    )
    preflight = _json(root / "07_render/RENDER_PREFLIGHT.json") or {}
    mix = _json(root / "07_render/MIX_CALIBRATION.json") or {}
    rights_state = source_rights_state(root)
    approvals = current_approvals(root, str(result.get("release_id", "r1")))
    bgm_event = approvals.get("bgm_rights")
    bgm_rights_approved = bool(bgm_event and bgm_event.get("decision") == "approved")
    result.update({
        "current_gate": stage,
        "unique_next_action": result.get("next_action"),
        "exact_cli": result.get("command"),
        "human_review_required": human_review_required,
        "blockers": {
            "preflight": {
                "blocked": status == "blocked_by_render_preflight" or preflight.get("status") == "blocked",
                "status": preflight.get("status", "not_run"),
                "free_disk_gib": preflight.get("free_disk_gib"),
                "required_disk_gib": preflight.get("required_disk_gib"),
                "recovery_commands": list(preflight.get("recovery_commands", [])),
            },
            "mix_calibration": {
                "blocked": stage == "opening_mix",
                "status": status if stage == "opening_mix" else mix.get("next_stage_status", "not_required_yet"),
            },
            "rights": {
                "blocked": status == "blocked_by_release_rights",
                "source_state": rights_state,
                "source_blocks_public_release": rights_state != "cleared",
                "bgm_rights_approved": bgm_rights_approved,
                "bgm_blocks_public_release": not bgm_rights_approved,
            },
        },
    })
    return result


def pipeline_status(project: Path) -> dict[str, Any]:
    root = project.expanduser().resolve()
    return _enhance_status(root, _raw_pipeline_status(root))
