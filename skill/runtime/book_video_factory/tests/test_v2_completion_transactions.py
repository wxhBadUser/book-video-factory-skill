from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from book_video_factory.host_orchestration import (
    complete_host_action,
    prepare_host_completion,
    read_ledgers,
    reconcile_prepared_completions,
    register_action,
)
from book_video_factory.visual_covenant import (
    covenant_canonical_sha,
    record_visual_covenant_approval,
    verify_visual_covenant_approval,
)


def _sha(value: str | bytes) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _locked_project(base: Path, *, name: str = "pilot") -> Path:
    project = base / "warehouse" / "projects" / name
    _write_json(project / "01_research_资料搜集/SOURCE_MANIFEST.json", {
        "schema_version": "1.0",
        "rights_status": "cleared",
        "public_release_allowed": True,
        "source_dir": "source",
        "files": [],
    })
    _write_json(project / "01_research_资料搜集/SOURCE_SANITIZATION_REPORT.json", {
        "schema_version": "source-sanitization-report.v1",
        "rights_status": "cleared",
        "public_release_allowed": True,
        "detected_source_declarations": [],
    })
    script_text = "locked narration for transaction tests"
    script = project / "02_story_script_故事脚本/SCRIPT_RELEASE.md"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(script_text, encoding="utf-8")
    _write_json(project / "02_story_script_故事脚本/LOCKED_SCRIPT.v2.json", {
        "schema_version": "locked-script.v2",
        "release_id": "release-1",
        "project_id": name,
        "language": "zh",
        "script_path": "02_story_script_故事脚本/SCRIPT_RELEASE.md",
        "script_sha256": _sha(script_text),
        "rights_state": "cleared",
        "lock_status": "locked",
    })
    return project


def _action(project: Path, suffix: str) -> dict:
    return {
        "schema_version": "host-agent-action.v2",
        "action_id": f"ACT_{suffix}",
        "release_id": "release-1",
        "project_id": project.name,
        "action_type": "generate_image",
        "idempotency_key": f"release-1:TASK_{suffix}:attempt-1",
        "attempt": 1,
        "inputs": [{"kind": "visual_paragraph", "sha256": _sha(f"paragraph-{suffix}")}],
        "expected_output": {"asset_id": f"ASSET_{suffix}", "asset_sha256": _sha(f"asset-{suffix}")},
        "max_runtime_seconds": 900,
    }


def _event(project: Path, action: dict, suffix: str) -> dict:
    output = project / f"host-output/{suffix}.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(f"host-output-{suffix}".encode("utf-8"))
    return {
        "schema_version": "host-agent-event.v2",
        "action_id": action["action_id"],
        "event_type": action["action_type"],
        "status": "succeeded",
        "idempotency_key": action["idempotency_key"],
        "provider": "test-host",
        "tool_call_id": f"call-{suffix}",
        "output_path": output.relative_to(project).as_posix(),
        "output_sha256": _sha(output.read_bytes()),
        "completion_artifacts": [{
            "path": f"manifests/production/{suffix}.json",
            "content": {"asset_id": f"ASSET_{suffix}", "bytes": f"canonical-{suffix}"},
        }],
    }


def test_concurrent_completions_keep_both_events_and_assets(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    actions = [_action(project, "001"), _action(project, "002")]
    for action in actions:
        register_action(project, action)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda item: complete_host_action(project, _event(project, item[0], item[1])), zip(actions, ("001", "002"))))

    assert {result["status"] for result in results} == {"committed"}
    ledgers = read_ledgers(project)
    assert {event["action_id"] for event in ledgers["events"]} == {"ACT_001", "ACT_002"}
    assert (project / "manifests/production/001.json").is_file()
    assert (project / "manifests/production/002.json").is_file()


def test_prepared_completion_is_reconciled_before_authoritative_consumption(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    action = _action(project, "003")
    register_action(project, action)
    event = _event(project, action, "003")

    prepared = prepare_host_completion(project, event)
    assert prepared["status"] == "prepared"
    assert not read_ledgers(project)["events"]

    reconciled = reconcile_prepared_completions(project)
    assert reconciled["committed_count"] == 1
    assert read_ledgers(project)["events"][0]["action_id"] == "ACT_003"
    assert complete_host_action(project, event)["status"] == "unchanged"


def test_completion_recovery_reuses_id_hashes_and_target_bytes(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    action = _action(project, "004")
    register_action(project, action)
    event = _event(project, action, "004")

    prepared = prepare_host_completion(project, event)
    journal = project / prepared["journal_path"]
    before = journal.read_bytes()
    completion_id = prepared["completion_id"]
    target = project / "manifests/production/004.json"
    target_bytes = target.read_bytes()

    recovered = reconcile_prepared_completions(project)
    retried = complete_host_action(project, event)

    assert recovered["completion_ids"] == [completion_id]
    assert retried["completion_id"] == completion_id
    assert journal.read_bytes() == before.replace(b'"prepared"', b'"committed"')
    assert target.read_bytes() == target_bytes
    assert _sha(target.read_bytes()) == prepared["artifact_sha256s"][0]


def test_covenant_reapproval_appends_immutable_event(tmp_path: Path) -> None:
    project = _locked_project(tmp_path)
    covenant_path = project / "04_visual_covenant_视觉契约/VISUAL_COVENANT.v2.json"
    covenant = {
        "schema_version": "visual-covenant.v2",
        "release_id": "release-1",
        "project_id": project.name,
        "assets": [],
    }
    covenant["visual_covenant_sha256"] = covenant_canonical_sha(covenant)
    _write_json(covenant_path, covenant)

    first = record_visual_covenant_approval(project, reviewer="reviewer", approved_at="2026-08-22T00:00:00+08:00")
    first_event = project / "04_visual_covenant_视觉契约/approval_events" / f"{first['visual_covenant_approval_sha256']}.json"
    first_bytes = first_event.read_bytes()

    covenant["mutation_marker"] = "world-v2"
    covenant["visual_covenant_sha256"] = covenant_canonical_sha(covenant)
    _write_json(covenant_path, covenant)
    second = record_visual_covenant_approval(project, reviewer="reviewer", approved_at="2026-08-22T00:01:00+08:00")

    assert second["visual_covenant_approval_sha256"] != first["visual_covenant_approval_sha256"]
    assert first_event.read_bytes() == first_bytes
    assert len(list((project / "04_visual_covenant_视觉契约/approval_events").glob("*.json"))) == 2
    assert verify_visual_covenant_approval(project)["visual_covenant_approval_sha256"] == second["visual_covenant_approval_sha256"]
