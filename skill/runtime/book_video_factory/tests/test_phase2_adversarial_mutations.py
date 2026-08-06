from __future__ import annotations

import json
import shutil
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from phase1_fixture_factory import build_phase1_inputs
from phase2_fixture_factory import build_bridge_input, build_phase2_project, write_json
from book_video_factory.gates import current_approvals
from book_video_factory.hbg_bridge.compiler import (
    HbgBridgeCompileError,
    HbgBridgeConflict,
    compile_hbg_bridge,
)
from book_video_factory.hbg_bridge.contracts import HbgBridgeContractError, validate_bridge_input
from book_video_factory.hbg_bridge.provenance import HbgBridgeProvenanceError, verify_phase1_handoff
from book_video_factory.hbg_bridge.runner import HbgRunnerError
from book_video_factory.manifests import record_approval, sha256_file


REQUIRED_SUBJECTS = [
    "02_story_script_故事脚本/SCRIPT_RELEASE.md",
    "02_story_script_故事脚本/SCRIPT_AUDIT.md",
    "02_story_script_故事脚本/SCRIPT_METRICS.json",
    "02_story_script_故事脚本/SCRIPT_LOCK.json",
    "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json",
]


def input_path(base: Path, payload: dict) -> Path:
    path = base / "bridge-input.json"
    write_json(path, payload)
    return path


def approve(project: Path, *, release_id: str = "r1", subjects: list[str] | None = None, event_id: str = "mutation-approval") -> None:
    record_approval(
        project,
        release_id=release_id,
        gate="script",
        decision="approved",
        reviewer="adversarial-reviewer",
        subjects=[project / relative for relative in (subjects or REQUIRED_SUBJECTS)],
        evidence_refs=["mutation"],
        note="adversarial fixture",
        event_id=event_id,
        reviewed_at="2026-08-01T13:00:00+00:00",
    )


def rewrite_content_output_hash(project: Path, relative: str) -> None:
    manifest_path = project / "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output_hashes"][relative] = sha256_file(project / relative)
    write_json(manifest_path, manifest)


def validate_contract(payload: dict) -> None:
    script = build_phase1_inputs()["script"]
    valid = build_bridge_input(package_digest="a" * 64)
    validate_bridge_input(
        payload,
        script=script,
        package_digest="a" * 64,
        release_text_sha256=valid["release_text_sha256"],
    )


# 1–9: Phase 1 provenance and approval attacks.
def test_attack_01_missing_script_approval() -> None:
    with tempfile.TemporaryDirectory() as temp:
        project, _, _, _ = build_phase2_project(Path(temp), approve=False)
        with pytest.raises(HbgBridgeProvenanceError):
            verify_phase1_handoff(project, "r1")


def test_attack_02_approval_for_wrong_release() -> None:
    with tempfile.TemporaryDirectory() as temp:
        project, _, _, _ = build_phase2_project(Path(temp), approve=False)
        approve(project, release_id="wrong", event_id="wrong-release")
        with pytest.raises(HbgBridgeProvenanceError):
            verify_phase1_handoff(project, "r1")


def test_attack_03_approval_missing_required_subject() -> None:
    with tempfile.TemporaryDirectory() as temp:
        project, _, _, _ = build_phase2_project(Path(temp), approve=False)
        approve(project, subjects=REQUIRED_SUBJECTS[:-1], event_id="partial")
        with pytest.raises(HbgBridgeProvenanceError):
            verify_phase1_handoff(project, "r1")


def test_attack_04_stale_approval_subject_hash() -> None:
    with tempfile.TemporaryDirectory() as temp:
        project, _, _, _ = build_phase2_project(Path(temp), approve=True)
        target = project / REQUIRED_SUBJECTS[0]
        target.write_text(target.read_text(encoding="utf-8") + "篡改", encoding="utf-8")
        with pytest.raises(HbgBridgeProvenanceError):
            verify_phase1_handoff(project, "r1")


def test_attack_05_tampered_content_package_output() -> None:
    with tempfile.TemporaryDirectory() as temp:
        project, _, _, _ = build_phase2_project(Path(temp), approve=True)
        target = project / "01_research_资料搜集/FACT_LEDGER.json"
        target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with pytest.raises(HbgBridgeProvenanceError):
            verify_phase1_handoff(project, "r1")


def test_attack_06_tampered_phase1_stage_manifest() -> None:
    with tempfile.TemporaryDirectory() as temp:
        project, _, _, _ = build_phase2_project(Path(temp), approve=True)
        manifest = json.loads((project / "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
        stage = project / manifest["stage_manifest_path"]
        stage.write_text(stage.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with pytest.raises(HbgBridgeProvenanceError):
            verify_phase1_handoff(project, "r1")


def test_attack_07_tampered_script_lock() -> None:
    with tempfile.TemporaryDirectory() as temp:
        project, _, _, _ = build_phase2_project(Path(temp), approve=True)
        target = project / "02_story_script_故事脚本/SCRIPT_LOCK.json"
        value = json.loads(target.read_text(encoding="utf-8"))
        value["package_digest"] = "0" * 64
        write_json(target, value)
        with pytest.raises(HbgBridgeProvenanceError):
            verify_phase1_handoff(project, "r1")


def test_attack_08_release_text_differs_from_lock_even_after_manifest_rehash() -> None:
    with tempfile.TemporaryDirectory() as temp:
        project, _, _, _ = build_phase2_project(Path(temp), approve=False)
        relative = "02_story_script_故事脚本/SCRIPT_PACKAGE.json"
        target = project / relative
        package = json.loads(target.read_text(encoding="utf-8"))
        package["script"]["release_version"]["text"] += "改变"
        write_json(target, package)
        rewrite_content_output_hash(project, relative)
        approve(project, event_id="post-mutation")
        with pytest.raises(HbgBridgeProvenanceError):
            verify_phase1_handoff(project, "r1")


def test_attack_09_hbg_locked_vendor_file_changed() -> None:
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        project, _, _, _ = build_phase2_project(base, approve=True)
        fake_repo = base / "repo"
        shutil.copytree(REPO / "vendor", fake_repo / "vendor")
        target = fake_repo / "vendor/hbg-life-simulation/scripts/build_storyboard_base.mjs"
        target.write_text(target.read_text(encoding="utf-8") + "\n// mutation\n", encoding="utf-8")
        with pytest.raises(HbgBridgeProvenanceError):
            verify_phase1_handoff(project, "r1", repository_root=fake_repo)


# 10–25: Bridge input contract attacks.
@pytest.mark.parametrize(
    ("case_id", "mutate"),
    [
        (10, lambda p: p.__setitem__("content_package_digest", "b" * 64)),
        (11, lambda p: p.__setitem__("release_text_sha256", "b" * 64)),
        (12, lambda p: p["narration"].__setitem__("provider", "other-tts")),
        (13, lambda p: p["brand"].__setitem__("series_name", "../../escape")),
        (14, lambda p: p["chapters"][1].__setitem__("chapter_id", p["chapters"][0]["chapter_id"])),
        (15, lambda p: p["chapters"][1]["section_ids"].append("S01")),
        (16, lambda p: p["chapters"][-1]["section_ids"].remove("S10")),
        (17, lambda p: p["chapters"][0].__setitem__("section_ids", ["S02", "S01", "S03"])),
        (18, lambda p: p["characters"].append(deepcopy(p["characters"][0]))),
        (19, lambda p: p["characters"][0].__setitem__("immutable_traits", ["待定"])),
        (20, lambda p: p["storyboard_beats"][0].__setitem__("anchor_refs", ["C999"])),
        (21, lambda p: (p["storyboard_beats"][0].__setitem__("risk_flags", ["hands"]), p["storyboard_beats"][0].__setitem__("generation_mode", "2x2"))),
        (22, lambda p: p["storyboard_beats"][0].__setitem__("forbidden_entities", ["圣地亚哥"])),
        (23, lambda p: p["storyboard_beats"][1].__setitem__("beat_id", p["storyboard_beats"][0]["beat_id"])),
        (24, lambda p: p["storyboard_beats"][0].__setitem__("cue", "冻结稿里不存在的句子")),
        (25, lambda p: p.__setitem__("storyboard_beats", [p["storyboard_beats"][1], p["storyboard_beats"][0], *p["storyboard_beats"][2:]])),
    ],
)
def test_contract_mutation_attacks(case_id: int, mutate) -> None:
    payload = build_bridge_input(package_digest="a" * 64)
    mutate(payload)
    with pytest.raises(HbgBridgeContractError):
        validate_contract(payload)


# 26–34: Compiler transaction/idempotency attacks.
def test_attack_26_preexisting_user_modified_script() -> None:
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        project, _, _, payload = build_phase2_project(base, approve=True)
        (project / "SCRIPT.md").write_text("用户锁定内容", encoding="utf-8")
        with pytest.raises(HbgBridgeConflict):
            compile_hbg_bridge(project, input_path(base, payload))


def test_attack_27_preexisting_user_modified_project_spec() -> None:
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        project, _, _, payload = build_phase2_project(base, approve=True)
        (project / "PROJECT_SPEC.json").write_text('{"user": true}\n', encoding="utf-8")
        with pytest.raises(HbgBridgeConflict):
            compile_hbg_bridge(project, input_path(base, payload))


def test_attack_28_post_lock_output_tamper() -> None:
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        project, _, _, payload = build_phase2_project(base, approve=True)
        path = input_path(base, payload)
        compile_hbg_bridge(project, path)
        (project / "SCRIPT.md").write_text("tampered", encoding="utf-8")
        with pytest.raises(HbgBridgeConflict):
            compile_hbg_bridge(project, path)


def test_attack_29_bridge_input_changed_after_publish() -> None:
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        project, _, _, payload = build_phase2_project(base, approve=True)
        compile_hbg_bridge(project, input_path(base, payload))
        changed = deepcopy(payload)
        changed["brand"]["episode_number"] = 99
        with pytest.raises(HbgBridgeConflict):
            compile_hbg_bridge(project, input_path(base, changed))


def test_attack_30_hbg_style_output_changed() -> None:
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        project, _, _, payload = build_phase2_project(base, approve=True)
        path = input_path(base, payload)
        compile_hbg_bridge(project, path)
        style = project / "HBG_STYLE.json"
        style.write_text(style.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with pytest.raises(HbgBridgeConflict):
            compile_hbg_bridge(project, path)


def test_attack_31_staging_validator_failure_leaves_no_outputs() -> None:
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        project, _, _, payload = build_phase2_project(base, approve=True)
        originals = {name: (project / name).read_bytes() for name in ("SCRIPT.md", "PROJECT_SPEC.json", "STORYBOARD_BASE.json")}
        with mock.patch("book_video_factory.hbg_bridge.compiler.validate_storyboard", side_effect=HbgRunnerError("injected")):
            with pytest.raises(HbgBridgeCompileError):
                compile_hbg_bridge(project, input_path(base, payload))
        assert {name: (project / name).read_bytes() for name in originals} == originals
        assert not (project / "HBG_STYLE.json").exists()


def test_attack_32_stage_manifest_failure_restores_all_outputs() -> None:
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        project, _, _, payload = build_phase2_project(base, approve=True)
        tracked = ("SCRIPT_SOURCE.md", "SCRIPT.md", "CHARACTERS.md", "PROJECT_SPEC.json", "STORYBOARD_BASE.json")
        originals = {name: (project / name).read_bytes() for name in tracked}
        with mock.patch("book_video_factory.hbg_bridge.compiler.write_stage_manifest", side_effect=RuntimeError("injected")):
            with pytest.raises(RuntimeError):
                compile_hbg_bridge(project, input_path(base, payload))
        assert {name: (project / name).read_bytes() for name in tracked} == originals
        assert not (project / "HBG_STYLE.json").exists()


def test_attack_33_output_and_self_reported_manifest_hash_changed_together() -> None:
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        project, _, _, payload = build_phase2_project(base, approve=True)
        path = input_path(base, payload)
        compile_hbg_bridge(project, path)
        output_relative = "SCRIPT.md"
        output = project / output_relative
        output.write_text("coordinated tamper", encoding="utf-8")
        manifest_path = project / "02_story_script_故事脚本/HBG_BRIDGE_MANIFEST.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["output_hashes"][output_relative] = sha256_file(output)
        write_json(manifest_path, manifest)
        with pytest.raises(HbgBridgeConflict):
            compile_hbg_bridge(project, path)


def test_attack_34_bridge_manifest_stage_path_traversal() -> None:
    with tempfile.TemporaryDirectory() as temp:
        base = Path(temp)
        project, _, _, payload = build_phase2_project(base, approve=True)
        path = input_path(base, payload)
        compile_hbg_bridge(project, path)
        manifest_path = project / "02_story_script_故事脚本/HBG_BRIDGE_MANIFEST.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["stage_manifest_path"] = "../../escape.json"
        write_json(manifest_path, manifest)
        with pytest.raises(HbgBridgeConflict):
            compile_hbg_bridge(project, path)


# 35–37: Architecture reintroduction attacks.
def _load_scanner():
    import importlib.util
    path = REPO / "scripts/verify_phase2_hbg_bridge.py"
    spec = importlib.util.spec_from_file_location("phase2_adversarial_scanner", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_attack_35_parallel_progress_file_reintroduced() -> None:
    scanner = _load_scanner()
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        path = root / "book_video_factory/src/book_video_factory/hbg_bridge/progress.py"
        path.parent.mkdir(parents=True)
        path.write_text('STATE = "progress.json"\n', encoding="utf-8")
        assert any(item["check_id"] == "second_state_authority" for item in scanner.scan_second_state_authority(root))


def test_attack_36_copied_hbg_narration_reintroduced() -> None:
    scanner = _load_scanner()
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        path = root / "book_video_factory/src/book_video_factory/hbg_bridge/build_narration.py"
        path.parent.mkdir(parents=True)
        path.write_text('subprocess.run(["edge-tts", "--write-media", "out.mp3"])\n', encoding="utf-8")
        assert any(item["check_id"] == "phase2_media_implementation" for item in scanner.scan_media_or_copied_hbg(root))


def test_attack_37_documentation_claims_media_completion() -> None:
    scanner = _load_scanner()
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        path = root / "README.md"
        path.write_text("Phase 2 已生成音频、图片和最终视频。\n", encoding="utf-8")
        assert any(item["check_id"] == "phase2_scope_claim" for item in scanner.scan_scope_claims(root, [path]))
