from __future__ import annotations

import json
from pathlib import Path

from book_video_factory.approved_script_import import import_approved_content_package
from book_video_factory.content_package import compile_content_package
from book_video_factory.gates import current_approvals
from book_video_factory.manifests import record_approval
from book_video_factory.project import initialize_project
from phase1_fixture_factory import build_phase1_inputs, materialize_phase1_source


def _compile_approved_source(base: Path) -> Path:
    project = initialize_project(base / "warehouse", "approved-source", "老人与海", "海明威")
    materialize_phase1_source(project)
    compile_content_package(project, release_id="r1", **build_phase1_inputs())
    subjects = [
        "02_story_script_故事脚本/SCRIPT_RELEASE.md",
        "02_story_script_故事脚本/SCRIPT_AUDIT.md",
        "02_story_script_故事脚本/SCRIPT_METRICS.json",
        "02_story_script_故事脚本/SCRIPT_LOCK.json",
        "02_story_script_故事脚本/CONTENT_PACKAGE_MANIFEST.json",
    ]
    record_approval(
        project, release_id="r1", gate="script", decision="approved",
        reviewer="source-reviewer", subjects=[project / relative for relative in subjects],
        evidence_refs=["source-test"], note="Source script was reviewed.",
    )
    assert "script" in current_approvals(project, "r1")
    return project


def test_imported_approved_script_is_relocked_for_target_release(tmp_path: Path) -> None:
    """An imported script preserves evidence but cannot carry a source approval to r2."""

    source = _compile_approved_source(tmp_path / "source")
    target = initialize_project(tmp_path / "target", "target-release", "老人与海", "海明威")
    materialize_phase1_source(target)

    result = import_approved_content_package(
        source, target, source_release_id="r1", target_release_id="r2"
    )

    target_lock = json.loads((target / "02_story_script_故事脚本/SCRIPT_LOCK.json").read_text(encoding="utf-8"))
    target_script = json.loads((target / "02_story_script_故事脚本/SCRIPT_PACKAGE.json").read_text(encoding="utf-8"))
    import_evidence = json.loads((target / "02_story_script_故事脚本/APPROVED_SCRIPT_IMPORT.json").read_text(encoding="utf-8"))
    assert result.status == "created"
    assert target_lock["release_id"] == "r2"
    assert target_script["release_id"] == "r2"
    assert target_script["script"]["release_id"] == "r2"
    assert target_lock["human_approved"] is False
    assert import_evidence["source_release_id"] == "r1"
    assert import_evidence["source_approval_transferred"] is False
    assert "script" not in current_approvals(target, "r2")
