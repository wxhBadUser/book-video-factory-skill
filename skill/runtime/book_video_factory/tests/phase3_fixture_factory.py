from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from phase2_fixture_factory import build_phase2_project, write_json
from book_video_factory.hbg_bridge.compiler import compile_hbg_bridge


LOOKDEV_CATEGORIES = [
    "identity_portrait",
    "full_body",
    "relationship",
    "interior",
    "exterior",
    "object",
    "daylight",
    "low_light",
    "action",
    "emotional_closeup",
    "environment",
    "hero_composition",
]


def build_phase3_input(*, bridge_digest: str = "b" * 64, release_text_sha256: str = "c" * 64) -> dict[str, Any]:
    tasks: list[dict[str, Any]] = []
    for index, category in enumerate(LOOKDEV_CATEGORIES, start=1):
        tasks.append({
            "task_id": f"LD{index:02d}",
            "category": category,
            "subject": "圣地亚哥与他所处的古巴海岸世界" if category != "object" else "老人磨损严重的小木船与粗麻绳",
            "action": "在真实叙事动作中呈现人物、环境与物件的关系",
            "shot_size": "medium wide" if category not in {"identity_portrait", "emotional_closeup"} else "close portrait",
            "lens": "50mm" if category not in {"environment", "exterior"} else "28mm",
            "camera_angle": "eye level",
            "composition": "克制的文学电影构图，主体清晰，底部字幕安全区留空",
            "depth": "foreground, subject plane, readable environment",
            "emotion": "克制、疲惫但有尊严",
            "palette_id": "SEA_DAY",
            "lighting_id": "SUN_WORN",
            "required_entities": ["圣地亚哥", "海面"] if category != "object" else ["旧木船", "粗麻绳"],
            "forbidden_entities": ["现代游艇", "旅游海报文字"],
            "anchor_refs": ["CHAR_C001"] if category != "object" else ["OBJ_BOAT"],
            "risk_flags": ["hero_shot"] if category == "hero_composition" else [],
            "generation_mode": "single",
            "style_reference_ids": ["REF_JANE_EYRE", "REF_MOON_SIXPENCE"],
            "wardrobe_state": "褪色浅色衬衫与旧长裤" if category != "object" else "not_applicable_anchor_object_or_scene",
            "hat_state": "旧草帽始终佩戴" if category != "object" else "not_applicable_anchor_object_or_scene",
        })
    return {
        "schema_version": "visual-stage-input.v1",
        "release_id": "r1",
        "hbg_bridge_digest": bridge_digest,
        "release_text_sha256": release_text_sha256,
        "orientation": "landscape",
        "global_kernel_id": "literary-cinematic-realism-v1",
        "style_reference_ids": ["REF_JANE_EYRE", "REF_MOON_SIXPENCE"],
        "book_look": {
            "period": "20世纪中叶",
            "geography": "古巴哈瓦那近海与墨西哥湾流",
            "visual_world": "海明威式克制的文学电影现实主义，广阔海面与孤独人物形成强负空间",
            "render_balance": "写实人物与轻度绘画表面平衡，不做塑料CGI或旅游广告",
            "emotional_temperature": "孤独、盐蚀、坚忍，不煽情",
            "composition_language": ["广角海面负空间", "人物与小船形成脆弱比例", "关键动作使用近景"],
            "portrait_language": ["自然风化皮肤", "非模特化脸部", "疲惫眼神与克制表情"],
            "environment_language": ["强海面反光", "旧木船", "清晨与黄昏的真实天气"],
        },
        "palette_profiles": [{
            "palette_id": "SEA_DAY",
            "name": "盐白与晒褪蓝绿",
            "colors": ["盐白", "晒褪蓝绿", "赭石", "旧木棕"],
            "use_cases": ["海上白昼", "港口清晨", "老人近景"],
            "diagnostic_envelope": {
                "median_luma": [45, 125],
                "dark_pixel_share_percent": [8, 70],
                "mean_saturation": [35, 165],
                "warm_pixel_share_percent": [20, 90],
            },
        }],
        "lighting_profiles": [{
            "lighting_id": "SUN_WORN",
            "name": "强日照与海面反光",
            "key": "来自海面上方的自然强光",
            "fill": "海水反光形成冷色低强度补光",
            "shadow": "深但可读，保留皱纹与衣物纹理",
            "allowed_times": ["清晨", "白昼", "黄昏"],
        }],
        "material_profiles": [{
            "material_id": "SEA_WORN_MATERIALS",
            "name": "海上风化材质",
            "materials": ["盐渍皮肤", "粗麻绳", "旧木船", "褪色棉布", "湿润鱼鳞"],
        }],
        "composition_rules": ["不连续使用正面半身肖像", "人物不能遮挡底部字幕安全区", "海景必须服务动作而非壁纸"],
        "repeated_motifs": ["狮子梦", "粗麻绳", "手上的伤", "小船与大海的比例"],
        "forbidden_traits": ["现代游艇", "加勒比旅游海报", "卡通老人", "塑料皮肤", "蓝橙商业调色", "生成文字"],
        "character_anchors": [{
            "anchor_id": "CHAR_C001",
            "anchor_type": "character_identity",
            "character_id": "C001",
            "name": "圣地亚哥",
            "prompt_subject": "古巴老渔夫圣地亚哥，瘦削、晒黑、皱纹深、眼神疲惫但坚毅",
            "required_views": ["front", "three_quarter", "full_body", "expression_range", "wardrobe"],
            "invariants": ["瘦削脸型", "深陷眼窝", "晒黑且布满皱纹的皮肤"],
            "allowed_changes": ["表情", "姿势", "轻微衣物磨损"],
            "forbidden_changes": ["年龄改变", "脸型改变", "肤色漂白", "现代发型"],
            "wardrobe": ["褪色浅色衬衫", "旧长裤"],
            "wardrobe_state": "褪色浅色衬衫与旧长裤",
            "hat_state": "旧草帽始终佩戴",
        }],
        "scene_anchors": [{
            "anchor_id": "SCENE_HARBOR",
            "name": "哈瓦那渔港",
            "prompt_subject": "20世纪中叶古巴渔港，旧木船、渔具与朴素岸边建筑",
            "invariants": ["旧木船", "朴素岸边建筑", "无现代游艇"],
        }],
        "object_anchors": [{
            "anchor_id": "OBJ_BOAT",
            "name": "老人的小木船",
            "prompt_subject": "磨损严重、结构可信的古巴小木渔船与粗麻绳",
            "invariants": ["旧木结构", "尺寸适合单人捕鱼", "无发动机豪华装置"],
        }],
        "lookdev_tasks": tasks,
    }


def build_phase3_project(base: Path):
    project, phase1_inputs, content_result, bridge_input = build_phase2_project(base, approve=True)
    bridge_path = base / "bridge-input.json"
    write_json(bridge_path, bridge_input)
    bridge_result = compile_hbg_bridge(project, bridge_path)
    bridge_manifest = json.loads(bridge_result.manifest_path.read_text(encoding="utf-8"))
    visual_input = build_phase3_input(
        bridge_digest=bridge_manifest["bridge_digest"],
        release_text_sha256=bridge_manifest["release_text_sha256"],
    )
    return project, visual_input, bridge_result, phase1_inputs, content_result
