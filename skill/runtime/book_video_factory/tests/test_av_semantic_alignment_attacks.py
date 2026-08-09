"""18 production-entry-point attack tests for the A/V semantic-alignment gates.

Every test here is ADVERSARIAL: it tries to push a tampered, stale, forged, or
missing artifact past a gate that is supposed to fail closed. A passing test
means the attack was BLOCKED. These are the production entry points where a
"reasonable but non-corresponding" frame could otherwise slip through:

  * T01-T04  Caption Visual Contract integrity (tamper / default / missing input)
  * T05-T09  Render preflight Caption-Visual-Contract currency (fail closed)
  * T10-T13  Vision review never auto-passes (legacy / forged / null / lane)
  * T14-T15  Vision evidence staleness (edited caption / swapped image)
  * T16      Prompt binding contract currency
  * T17-T18  Scene-review decision gate (missing / tampered decision)
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from book_video_factory.semantic_alignment.caption_contract import (
    CaptionContractError,
    CaptionEntityEvidence,
    CaptionVisualContract,
    build_caption_visual_contract_document,
    build_caption_visual_contract_from_project,
    enrich_captions_to_contracts,
)
from book_video_factory.semantic_alignment.caption_grouping import build_caption_grouping_from_project
from book_video_factory.semantic_alignment.models import VisualProposition
from book_video_factory.semantic_alignment.prompting import (
    PromptBindingError,
    compute_prompt_binding,
    verify_prompt_binding,
)
from book_video_factory.render_stage.preflight import (
    RenderPreflightError,
    _contract_currency_blockers,
    _scene_review_vision_blockers,
)
from book_video_factory.semantic_alignment.vision_review import (
    MissingVisionEvidenceError,
    VisionReviewError,
    validate_review_decision,
)
from book_video_factory.semantic_alignment.vision_review.contracts import (
    PROVIDER_VERIFICATION_KEYS,
    VisionEvidence,
    _evidence_signature,
)
from book_video_factory.semantic_alignment.vision_review.evidence import (
    StaleVisionEvidenceError,
    verify_evidence_current,
)
from book_video_factory.semantic_alignment.vision_review.provider import (
    NullVisionProvider,
    UntrustedProviderError,
    VisionProviderError,
    validate_vision_provider,
)


class _StubProposition:
    """Duck-typed VisualProposition: verify/compute only need content_sha256()."""

    def content_sha256(self) -> str:  # noqa: D401
        return "a" * 64


def _tmp_project(tmp_path: Path, *, with_storyboard: bool = True) -> Path:
    root = tmp_path / "proj"
    (root / "02_story_script_故事脚本").mkdir(parents=True)
    (root / "04_audio").mkdir(parents=True)
    (root / "03_images_生成图片").mkdir(parents=True)
    (root / "02_story_script_故事脚本" / "SCRIPT_PACKAGE.json").write_text(
        json.dumps({
            "performance_version": {
                "sections": [
                    {"section_id": "S1", "narrative_function": "plot", "text": "福贵牵着老牛走过田埂"}
                ]
            }
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "04_audio" / "CAPTION_BINDINGS.json").write_text(
        json.dumps({
            "release_id": "r1",
            "captions": {
                "caption-1": {
                    "caption_id": "caption-1", "text": "福贵牵着老牛走过田埂",
                    "text_sha256": hashlib.sha256("福贵牵着老牛走过田埂".encode()).hexdigest(),
                }
            },
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / "03_images_生成图片" / "BOOK_VISUAL_PROFILE.json").write_text(
        json.dumps({"character_anchors": [], "object_anchors": [], "scene_anchors": []}),
        encoding="utf-8",
    )
    if with_storyboard:
        (root / "STORYBOARD_BASE.json").write_text(
            json.dumps([
                {
                    "beatId": "B001", "sectionId": "S1", "cue": "福贵牵着老牛走过田埂",
                    "description": "福贵与牛", "requiredEntities": ["C002", "C010"],
                    "forbiddenEntities": [], "narrative_function": "plot",
                }
            ]),
            encoding="utf-8",
        )
    return root


def _write_contract(root: Path, release_id: str, caption_ids, *, narrative_function: str = "plot") -> Path:
    contracts = []
    for cid in caption_ids:
        text = f"caption {cid}"
        contracts.append(CaptionVisualContract(
                caption_id=cid,
                caption_text=text,
                caption_text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                section_id="S1",
                source_beat_ids=("B1",),
                narrative_function=narrative_function,
                subjects=(text,),
                story_objects=(),
                scene_state={
                    "visible_character_ids": [],
                    "location_id": "",
                    "time_context": "",
                    "action_state": text,
                    "continuity_state": {"pronoun_resolutions": []},
                    "action_semantics": {
                        "action_key": "same_scene_sequence",
                        "incompatible_action_keys": [],
                        "hard_split_event": "none",
                        "event_instance_id": "sequence:B1",
                        "source_evidence": {
                            "beat": {"beat_id": "B1", "risk_flags": [], "high_risk": False, "generation_mode": "2x2"},
                            "caption": {"caption_id": cid, "shot_ids": [], "semantic_rationale": "test evidence"},
                        },
                    },
                },
                must_show=(CaptionEntityEvidence(
                    entity_id="C1",
                    natural_language=text,
                    reason="caption_named_entity",
                    evidence={"text_evidence": text},
                ),),
        ))
    doc = build_caption_visual_contract_document(release_id=release_id, contracts=contracts)
    path = root / "04_audio" / "CAPTION_VISUAL_CONTRACT.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_tasks(root: Path, tasks: list[dict]) -> None:
    d = root / "05_director"
    d.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(t, ensure_ascii=False) for t in tasks]
    (d / "IMAGE_TASKS.jsonl").write_text("\n".join(lines), encoding="utf-8")


def _write_current_contract_group_and_task(root: Path) -> tuple[Path, dict]:
    """Mint a complete persisted v2 Contract/Group fixture for Render attacks."""
    bindings_path = root / "04_audio" / "CAPTION_BINDINGS.json"
    bindings = json.loads(bindings_path.read_text(encoding="utf-8"))
    caption = bindings["captions"]["caption-1"]
    caption.update({"start": 0.0, "end": 3.0})
    bindings_path.write_text(json.dumps(bindings, ensure_ascii=False), encoding="utf-8")
    contract_path = build_caption_visual_contract_from_project(root, release_id="r1")
    build_caption_grouping_from_project(root)
    grouping = json.loads((root / "04_audio" / "CAPTION_GROUPING_AUDIT.json").read_text(encoding="utf-8"))
    group = grouping["groups"][0]
    proposition = VisualProposition(
        mode="Abstract", subject="命运", action="压迫", environment="空旷田野",
        mood="克制", lighting="L1", palette="P1", rationale_text="测试命题",
    )
    prompt = "PROMPT"
    contract_sha = hashlib.sha256(
        "|".join(item["content_sha256"] for item in group["contract_bindings"]).encode("utf-8")
    ).hexdigest()
    binding = compute_prompt_binding(
        caption_ids=group["caption_ids"], caption_text=caption["text"], proposition=proposition,
        prompt=prompt, scene_id="S1", beat_ids=["B1"], shot_id="SHOT_S1",
        caption_visual_contract_sha256=contract_sha, group_id=group["group_id"],
        caption_contract_bindings=group["contract_bindings"],
        caption_group_sha256=group["caption_group_sha256"],
    )
    return contract_path, {
        "schema_version": "production-image-task.v1", "task_id": "SCENE_1", "scene_id": "S1",
        "shot_id": "SHOT_S1", "source_beat_ids": ["B1"],
        "caption_ids": group["caption_ids"], "caption_text": caption["text"], "prompt": prompt,
        "visual_proposition": proposition.to_dict(), "prompt_binding": binding,
        "caption_visual_contract_sha256": contract_sha,
    }


def _valid_evidence(shot_id: str, image_sha: str, caption_text: str, prompt_text: str,
                    provider: str = "local-vision-stub") -> VisionEvidence:
    cap_sha = hashlib.sha256(caption_text.encode("utf-8")).hexdigest()
    pr_sha = hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
    call_id = "call-" + shot_id
    reasoning = "x" * 24
    verdict = "match"
    key = PROVIDER_VERIFICATION_KEYS[provider]
    sig = _evidence_signature(
        provider=provider, call_id=call_id, image_sha256=image_sha,
        caption_sha256=cap_sha, prompt_sha256=pr_sha, parity_verdict=verdict,
        parity_reasoning=reasoning, reviewed_pixels=True, key=key,
    )
    return VisionEvidence(
        shot_id=shot_id, vision_provider=provider, call_id=call_id,
        image_sha256=image_sha, caption_sha256=cap_sha, prompt_sha256=pr_sha,
        parity_verdict=verdict, parity_reasoning=reasoning,
        reviewed_pixels=True, provider_signature=sig,
    )


# --- T01-T04: Caption Visual Contract integrity ---------------------------------


def test_T01_tampered_caption_text_sha_is_rejected():
    """A contract whose caption_text_sha256 does not match its text cannot be loaded."""
    raw = {
        "caption_id": "C1", "caption_text": "hello",
        "caption_text_sha256": "0" * 64,  # valid hex but does NOT match "hello"
        "narrative_function": "plot", "must_show": ["hello"],
    }
    with pytest.raises(CaptionContractError):
        CaptionVisualContract.from_mapping(raw)


def test_T02_invalid_narrative_function_is_rejected():
    """A contract with a non-canonical narrative_function cannot be loaded."""
    raw = {
        "caption_id": "C1", "caption_text": "hello",
        "caption_text_sha256": hashlib.sha256(b"hello").hexdigest(),
        "narrative_function": "bogus_default",
    }
    with pytest.raises(CaptionContractError):
        CaptionVisualContract.from_mapping(raw)


def test_T03_unbindable_caption_raises_no_silent_default():
    """A caption that cannot bind to any locked beat/section must be rejected,
    never silently defaulted to narrative_function='plot' with empty subjects."""
    sections = [{"section_id": "S1", "narrative_function": "plot", "text": "unrelated section body"}]
    with pytest.raises(CaptionContractError):
        enrich_captions_to_contracts(
            script_sections=sections,
            beats=[],  # no beats -> caption cannot bind
            captions=[{"caption_id": "X", "text": "zzz unrelated caption"}],
        )


def test_T04_missing_storyboard_input_raises():
    """build_caption_visual_contract_from_project fails closed when a required
    locked input (STORYBOARD_BASE.json) is missing."""
    from pathlib import Path as _P
    import tempfile
    root = _P(tempfile.mkdtemp())
    # provide the other inputs but omit STORYBOARD_BASE.json
    (_P(root) / "02_story_script_故事脚本").mkdir(parents=True)
    (_P(root) / "04_audio").mkdir(parents=True)
    (_P(root) / "03_images_生成图片").mkdir(parents=True)
    (_P(root) / "02_story_script_故事脚本" / "SCRIPT_PACKAGE.json").write_text(
        json.dumps({"performance_version": {"sections": []}}), encoding="utf-8")
    (_P(root) / "04_audio" / "CAPTION_BINDINGS.json").write_text(
        json.dumps({"release_id": "r1", "captions": {}}), encoding="utf-8")
    (_P(root) / "03_images_生成图片" / "BOOK_VISUAL_PROFILE.json").write_text(
        json.dumps({}), encoding="utf-8")
    with pytest.raises(CaptionContractError):
        build_caption_visual_contract_from_project(root, release_id="r1")


# --- T05-T09: Render preflight contract currency -------------------------------


def _write_min_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_T05_missing_contract_file_blocks_render(tmp_path):
    root = _write_min_root(tmp_path)
    missing = root / "04_audio" / "CAPTION_VISUAL_CONTRACT.json"
    with pytest.raises(RenderPreflightError):
        _contract_currency_blockers(missing, root, "r1")


def test_T06_contract_release_mismatch_blocks(tmp_path):
    root = _write_min_root(tmp_path)
    _write_tasks(root, [{"schema_version": "production-image-task.v1", "task_id": "SCENE_1",
                         "caption_ids": ["C1"], "caption_visual_contract_sha256": "0" * 64}])
    contract_path = _write_contract(root, release_id="A", caption_ids=["C1"])
    with pytest.raises(RenderPreflightError):
        _contract_currency_blockers(contract_path, root, "B")  # render wants release B


def test_T07_task_without_contract_binding_is_blocked(tmp_path):
    root = _tmp_project(tmp_path)
    contract_path, task = _write_current_contract_group_and_task(root)
    task.pop("caption_visual_contract_sha256")
    _write_tasks(root, [task])
    blockers = _contract_currency_blockers(contract_path, root, "r1")
    assert blockers == ["SCENE_1"]


def test_T08_task_with_stale_contract_binding_is_blocked(tmp_path):
    root = _tmp_project(tmp_path)
    contract_path, task = _write_current_contract_group_and_task(root)
    task["caption_visual_contract_sha256"] = "0" * 64
    _write_tasks(root, [task])
    blockers = _contract_currency_blockers(contract_path, root, "r1")
    assert blockers == ["SCENE_1"]


def test_T09_task_with_current_contract_binding_passes(tmp_path):
    root = _tmp_project(tmp_path)
    contract_path, task = _write_current_contract_group_and_task(root)
    _write_tasks(root, [task])
    blockers = _contract_currency_blockers(contract_path, root, "r1")
    assert blockers == []  # correct bind -> no blocker


# --- T10-T13: Vision review never auto-passes ----------------------------------


def test_T10_legacy_pass_without_evidence_is_rejected():
    decision = {"shot_id": "S1", "task_id": "S1", "legacy_pass": True}  # no vision_evidence
    with pytest.raises(MissingVisionEvidenceError):
        validate_review_decision(decision, require_pass=True)


def test_T11_forged_vision_signature_is_rejected():
    ev = _valid_evidence("S1", "0" * 64, "caption", "prompt")
    forged = VisionEvidence(**{**ev.to_dict(), "provider_signature": "0" * 64})
    decision = {"shot_id": "S1", "task_id": "S1", "vision_evidence": forged.to_dict()}
    with pytest.raises(VisionReviewError):
        validate_review_decision(decision, require_pass=True)


def test_T12_null_vision_provider_never_fabricates_verdict():
    with pytest.raises(VisionProviderError):
        NullVisionProvider().review(image_bytes=b"imagedata", caption_text="c", prompt_text="p")


def test_T13_image_generation_lane_cannot_review_its_own_output():
    for lane in ("host-imagegen", "gemini-web", "flow-web"):
        with pytest.raises(UntrustedProviderError):
            validate_vision_provider(lane)


# --- T14-T15: Vision evidence staleness ----------------------------------------


def test_T14_edited_caption_invalidates_approval(tmp_path):
    img = tmp_path / "img.bin"
    img.write_bytes(b"original-frame-bytes-1234567890")
    img_sha = hashlib.sha256(img.read_bytes()).hexdigest()
    ev = _valid_evidence("S1", img_sha, "original caption", "prompt")
    with pytest.raises(StaleVisionEvidenceError):
        verify_evidence_current(ev, image_path=img, caption_text="edited caption now", prompt_text="prompt")


def test_T15_swapped_image_invalidates_approval(tmp_path):
    img = tmp_path / "img.bin"
    img.write_bytes(b"original-frame-bytes-1234567890")
    img_sha = hashlib.sha256(img.read_bytes()).hexdigest()
    ev = _valid_evidence("S1", img_sha, "caption", "prompt")
    img.write_bytes(b"different-frame-bytes-!!!!!!!!!!")  # swapped after approval
    with pytest.raises(StaleVisionEvidenceError):
        verify_evidence_current(ev, image_path=img, caption_text="caption", prompt_text="prompt")


# --- T16: Prompt binding contract currency -------------------------------------


def test_T16_stale_contract_sha_fails_prompt_binding():
    binding = compute_prompt_binding(
        caption_ids=["C1"], caption_text="t", proposition=_StubProposition(),
        prompt="p", scene_id="s1", beat_ids=["b1"], shot_id="sh1",
        caption_visual_contract_sha256="0" * 64,
    )
    with pytest.raises(PromptBindingError):
        verify_prompt_binding(
            binding, caption_text="t", proposition=_StubProposition(), prompt="p",
            caption_visual_contract_sha256="1" * 64,  # edited contract
        )


# --- T17-T18: Scene-review decision gate ---------------------------------------


def test_T17_missing_decision_file_blocks_render(tmp_path):
    missing = tmp_path / "06_visual_production" / "SCENE_REVIEW_DECISION.json"
    with pytest.raises(RenderPreflightError):
        _scene_review_vision_blockers(missing)


def test_T18_tampered_decision_item_is_rejected(tmp_path):
    path = tmp_path / "SCENE_REVIEW_DECISION.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": "scene-review-decision.v1",
        "decisions": [{"task_id": "S1", "semantic_review_status": "na"}],  # missing required fields
    }), encoding="utf-8")
    with pytest.raises(RenderPreflightError):
        _scene_review_vision_blockers(path)
