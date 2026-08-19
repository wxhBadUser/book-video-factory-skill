from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from phase1_fixture_factory import build_phase1_inputs, materialize_phase1_source


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_bridge_input(
    *,
    package_digest: str = "a" * 64,
    release_text_sha256: str | None = None,
) -> dict[str, Any]:
    phase1 = build_phase1_inputs()
    script = phase1["script"]
    sections = script["release_version"]["sections"]
    release_text = script["release_version"]["text"]
    release_hash = release_text_sha256 or sha256_bytes(release_text.encode("utf-8"))
    chapter_defs = [
        ("CH01", "失败与再次出海", ["S01", "S02", "S03"]),
        ("CH02", "大鱼咬钩", ["S04", "S05", "S06"]),
        ("CH03", "胜利被夺走", ["S07", "S08"]),
        ("CH04", "回到岸上", ["S09", "S10"]),
    ]
    section_text = {item["section_id"]: item["text"] for item in sections}
    beats: list[dict[str, Any]] = []
    for index, item in enumerate(sections, start=1):
        sid = item["section_id"]
        chapter_id = next(cid for cid, _, sids in chapter_defs if sid in sids)
        cue = section_text[sid][:20]
        high_risk = index in {4, 5, 7}
        beats.append({
            "beat_id": f"B{index:03d}",
            "section_id": sid,
            "chapter_id": chapter_id,
            "cue": cue,
            "description": f"第{index}个语义镜头，严格对应{sid}中的人物动作与环境",
            "caption_intent": section_text[sid][:14],
            "required_entities": ["圣地亚哥", "海面" if index > 1 else "港口"],
            "forbidden_entities": ["现代游艇"],
            "risk_flags": ["water_action"] if high_risk else [],
            "generation_mode": "single" if high_risk else "2x2",
            "anchor_refs": ["C001"],
            "participants": {"count": 1, "allowed": ["C001"]},
        })
    return {
        "schema_version": "hbg-bridge-input.v1",
        "content_package_digest": package_digest,
        "release_text_sha256": release_hash,
        "release_id": "r1",
        "orientation": "landscape",
        "brand": {
            "series_name": "一生值得读的世界名著",
            "episode_number": 1,
            "lead_text": "名著值得读，但很多人读不进去。",
            "lead_display_text": "一生值得读的世界名著",
            "reveal_text": "《老人与海》，欧内斯特·海明威。",
        },
        "narration": {
            "provider": "edge-tts",
            "voice": "zh-CN-YunjianNeural",
            "body_rate": "+0%",
            "lead_rate": "+0%",
            "reveal_rate": "+0%",
            "pitch": "+0Hz",
        },
        "chapters": [
            {"chapter_id": cid, "title": title, "section_ids": sids}
            for cid, title, sids in chapter_defs
        ],
        "characters": [{
            "character_id": "C001",
            "name": "圣地亚哥",
            "role": "主角",
            "life_stage": "老年",
            "immutable_traits": ["瘦削脸型", "深陷眼窝", "晒黑且布满皱纹的皮肤"],
            "changeable_traits": ["表情", "姿势", "轻微衣物磨损"],
            "wardrobe": ["褪色浅色衬衫", "旧长裤"],
            "relationships": ["与少年马诺林关系亲密"],
            "anchor_status": "pending",
        }],
        "storyboard_beats": beats,
    }


def clone(value: Any) -> Any:
    return deepcopy(value)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_phase2_project(
    base: Path, *, approve: bool = True, orientation: str = "landscape"
):
    from book_video_factory.content_package import compile_content_package
    from book_video_factory.manifests import record_approval
    from book_video_factory.project import initialize_project

    project = initialize_project(
        base / "warehouse",
        "old-man-and-the-sea",
        "老人与海",
        "欧内斯特·海明威",
        orientation=orientation,
        visual_foundation_policy="legacy",
    )
    materialize_phase1_source(project)
    inputs = build_phase1_inputs()
    result = compile_content_package(project, **inputs, release_id="r1")
    if approve:
        subject_relatives = [
            "02_story_script_故事脚本/SCRIPT_RELEASE.md",
            "02_story_script_故事脚本/SCRIPT_AUDIT.md",
            "02_story_script_故事脚本/SCRIPT_METRICS.json",
            "02_story_script_故事脚本/SCRIPT_LOCK.json",
            "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json",
        ]
        record_approval(
            project,
            release_id="r1",
            gate="script",
            decision="approved",
            reviewer="phase2-fixture-reviewer",
            subjects=[project / relative for relative in subject_relatives],
            evidence_refs=["fixture"],
            note="Phase 2 fixture script approval",
            event_id="phase2-script-approval",
            reviewed_at="2026-08-01T12:00:00+00:00",
        )
    bridge_input = build_bridge_input(
        package_digest=result.package_digest,
        release_text_sha256=inputs["originality"]["candidate_sha256"],
    )
    bridge_input["orientation"] = orientation
    return project, inputs, result, bridge_input
