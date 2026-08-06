from __future__ import annotations

import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from book_video_factory.content_package import (
    ContentPackageConflict,
    ContentPackageError,
    compile_content_package,
)
from book_video_factory.project import initialize_project
from phase1_fixture_factory import (
    build_phase1_inputs,
    build_phase1_originality,
    materialize_phase1_source,
)


def _project(tmp: str) -> Path:
    project = initialize_project(Path(tmp) / "warehouse", "old-man-and-the-sea", "老人与海", "海明威")
    materialize_phase1_source(project)
    return project


def _compile(project: Path, inputs: dict):
    return compile_content_package(project, release_id="r1", **inputs)


def test_valid_fixture_compiles_expected_artifacts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        result = _compile(project, build_phase1_inputs())
        expected = [
            "01_research_资料搜集/SOURCE_MANIFEST.json",
            "01_research_资料搜集/BOOK_RESEARCH.json",
            "01_research_资料搜集/FACT_LEDGER.json",
            "01_research_资料搜集/CREATIVE_DECISION.json",
            "01_research_资料搜集/FATE_ANCHORS.json",
            "01_research_资料搜集/EVENT_CARDS.json",
            "02_story_script_故事脚本/SCRIPT_PACKAGE.json",
            "02_story_script_故事脚本/SCRIPT_PERFORMANCE.md",
            "02_story_script_故事脚本/SCRIPT_RELEASE.md",
            "02_story_script_故事脚本/SCRIPT_AUDIT.md",
            "02_story_script_故事脚本/SCRIPT_METRICS.json",
            "02_story_script_故事脚本/CONTENT_QUALITY_REPORT.json",
            "02_story_script_故事脚本/ORIGINALITY_REPORT.json",
            "02_story_script_故事脚本/BLIND_REVIEW.json",
            "02_story_script_故事脚本/SCRIPT_LOCK.json",
            "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json",
        ]
        assert result.status == "created"
        assert all((project / relative).is_file() for relative in expected)


def test_compile_is_atomic_when_one_artifact_is_invalid() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        inputs = build_phase1_inputs()
        inputs["event_cards"]["cards"][0]["hammer_line"] = "待定"
        with pytest.raises(ContentPackageError):
            _compile(project, inputs)
        assert not (project / "02_story_script_故事脚本/SCRIPT_LOCK.json").exists()
        assert not (project / "01_research_资料搜集/BOOK_RESEARCH.json").exists()


def test_existing_different_output_is_not_silently_overwritten() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        target = project / "02_story_script_故事脚本/SCRIPT_RELEASE.md"
        target.write_text("用户自己的不同内容", encoding="utf-8")
        with pytest.raises(ContentPackageConflict):
            _compile(project, build_phase1_inputs())
        assert target.read_text(encoding="utf-8") == "用户自己的不同内容"


def test_identical_recompile_is_idempotent() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        first = _compile(project, build_phase1_inputs())
        lock_before = (project / "02_story_script_故事脚本/SCRIPT_LOCK.json").read_bytes()
        second = _compile(project, build_phase1_inputs())
        assert second.status == "unchanged"
        assert second.package_digest == first.package_digest
        assert (project / "02_story_script_故事脚本/SCRIPT_LOCK.json").read_bytes() == lock_before


def test_script_lock_binds_release_text_and_all_evidence_hashes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        result = _compile(project, build_phase1_inputs())
        lock = json.loads((project / "02_story_script_故事脚本/SCRIPT_LOCK.json").read_text(encoding="utf-8"))
        assert lock["machine_locked"] is True
        assert lock["release_text_sha256"]
        assert set(lock["input_hashes"]) == {
            "source_manifest", "research", "creative_decision", "fate_anchors",
            "event_cards", "script", "quality", "originality", "blind_review",
        }
        assert lock["package_digest"] == result.package_digest


def test_machine_lock_does_not_claim_human_approval() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        _compile(project, build_phase1_inputs())
        lock = json.loads((project / "02_story_script_故事脚本/SCRIPT_LOCK.json").read_text(encoding="utf-8"))
        assert lock["human_approved"] is False
        assert lock["next_stage_status"] == "blocked_by_script_approval"


def test_input_mutation_changes_package_digest() -> None:
    with tempfile.TemporaryDirectory() as first_tmp, tempfile.TemporaryDirectory() as second_tmp:
        first_inputs = build_phase1_inputs()
        second_inputs = deepcopy(first_inputs)
        second_inputs["script"]["release_version"]["sections"][-1]["text"] += "他醒来时天已经亮了。"
        second_inputs["script"]["release_version"]["text"] += "他醒来时天已经亮了。"
        second_inputs["script"]["performance_version"]["sections"][-1]["text"] += "他醒来时天已经亮了。"
        second_inputs["script"]["performance_version"]["text"] += "他醒来时天已经亮了。"
        second_inputs["script"]["audit_version"]["sections"][-1]["text"] += "他醒来时天已经亮了。"
        second_inputs["script"]["audit_version"]["text"] += "他醒来时天已经亮了。"
        second_inputs["script"]["script_text"] += "他醒来时天已经亮了。"
        second_inputs["originality"] = build_phase1_originality(
            second_inputs["script"]["release_version"]["text"]
        )
        first = _compile(_project(first_tmp), first_inputs)
        second = _compile(_project(second_tmp), second_inputs)
        assert first.package_digest != second.package_digest


def test_package_manifest_records_every_input_and_output_hash() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        result = _compile(project, build_phase1_inputs())
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        assert len(manifest["input_hashes"]) == 9
        assert len(manifest["output_hashes"]) >= 15
        assert manifest["stage_manifest_path"]
        assert (project / manifest["stage_manifest_path"]).is_file()


def test_identical_recompile_rejects_tampered_output() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        inputs = build_phase1_inputs()
        _compile(project, inputs)
        target = project / "02_story_script_故事脚本/SCRIPT_RELEASE.md"
        target.write_text("tampered after machine lock", encoding="utf-8")
        with pytest.raises(ContentPackageConflict, match="hash mismatch"):
            _compile(project, inputs)


def test_formal_package_requires_real_source_file_with_matching_hash() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        (project / "source/full_text.txt").unlink()
        with pytest.raises(ContentPackageError, match="source file.*missing"):
            _compile(project, build_phase1_inputs())


def test_quality_total_must_equal_exact_fifteen_item_sum() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        inputs = build_phase1_inputs()
        inputs["quality"]["total"] += 5
        with pytest.raises(ContentPackageError, match="quality total"):
            _compile(project, inputs)


def test_blind_review_requires_two_actual_review_records() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        inputs = build_phase1_inputs()
        inputs["blind_review"]["reviews"] = []
        with pytest.raises(ContentPackageError, match="blind review.*records"):
            _compile(project, inputs)


def test_originality_report_must_bind_to_release_text() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        inputs = build_phase1_inputs()
        inputs["originality"]["candidate_sha256"] = "0" * 64
        with pytest.raises(ContentPackageError, match="originality.*release text"):
            _compile(project, inputs)


def test_identical_recompile_rejects_tampered_stage_manifest() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = _project(tmp)
        inputs = build_phase1_inputs()
        result = _compile(project, inputs)
        result.stage_manifest_path.write_text('{"tampered": true}\n', encoding="utf-8")
        with pytest.raises(ContentPackageConflict, match="stage manifest hash mismatch"):
            _compile(project, inputs)
