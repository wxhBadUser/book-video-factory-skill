from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image

from book_video_factory.director_stage.compiler import DirectorStageError, compile_director_stage
from book_video_factory.hbg_bridge.provenance import _verify_vendor
from book_video_factory.hbg_bridge.runner import repository_root
from book_video_factory.hbg_bridge.shell import bash_executable, path_for_bash
from book_video_factory.manifests import record_approval, safe_project_output, sha256_file
from .registry import SceneAssetError, _load, _manifest, _tasks


class SceneReviewError(RuntimeError):
    """Production scene review or approval evidence is incomplete or stale."""


@dataclass(frozen=True)
class SceneReviewResult:
    status: str
    report_path: Path
    contact_sheet_path: Path
    next_stage_status: str


ContactSheetRunner = Callable[[Path, list[Path]], None]


def _pretty(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _default_contact_sheet(output: Path, images: list[Path]) -> None:
    root = repository_root()
    try:
        _verify_vendor(root)
    except RuntimeError as error:
        raise SceneReviewError(f"HBG vendor integrity failed: {error}") from error
    script = root / "vendor/hbg-life-simulation/scripts/make_contact_sheet.sh"
    bash = bash_executable()
    arguments = [
        path_for_bash(script), path_for_bash(output), "5",
        *(path_for_bash(image) for image in images),
    ]
    command = [bash, *arguments]
    if os.name == "nt" and Path(bash).name.lower() == "bash.exe":
        command = [
            bash,
            "-c",
            'pwd() { builtin pwd -W; }; export -f pwd; exec "$@"',
            "hbg-contact-sheet",
            *arguments,
        ]
    completed = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise SceneReviewError(
            f"HBG contact sheet failed: {completed.stderr.strip() or completed.stdout.strip()}"
        )


def _decision(path: Path, *, release_id: str, director_sha: str, asset_sha: str, task_ids: list[str]) -> dict[str, Any]:
    value = _load(path, "scene review decision")
    expected = {
        "schema_version", "release_id", "director_stage_manifest_sha256",
        "scene_asset_manifest_sha256", "reviewer", "decisions",
    }
    if set(value) != expected or value.get("schema_version") != "scene-review-decision.v1":
        raise SceneReviewError("scene review decision fields are invalid")
    if value.get("release_id") != release_id or value.get("director_stage_manifest_sha256") != director_sha:
        raise SceneReviewError("scene review decision is bound to different director evidence")
    if value.get("scene_asset_manifest_sha256") != asset_sha:
        raise SceneReviewError("scene review decision is bound to a stale asset manifest")
    reviewer = value.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip() or reviewer != reviewer.strip():
        raise SceneReviewError("scene review decision reviewer is invalid")
    decisions = value.get("decisions")
    if not isinstance(decisions, list) or len(decisions) != len(task_ids):
        raise SceneReviewError("scene review decision must cover every task exactly once")
    normalized: dict[str, dict[str, Any]] = {}
    allowed_fields = {
        "task_id", "semantic_review_status", "reality_review_status",
        "identity_review_status", "note",
    }
    for item in decisions:
        if not isinstance(item, dict) or set(item) != allowed_fields:
            raise SceneReviewError("scene review decision item fields are invalid")
        task_id = item.get("task_id")
        if task_id not in task_ids or task_id in normalized:
            raise SceneReviewError("scene review decision task coverage is invalid")
        for field in ("semantic_review_status", "reality_review_status", "identity_review_status"):
            if item.get(field) not in {"pass", "fail"}:
                raise SceneReviewError(f"{field} must be pass or fail")
        note = item.get("note")
        if not isinstance(note, str) or note != note.strip():
            raise SceneReviewError("scene review note must be a trimmed string")
        normalized[task_id] = dict(item)
    if set(normalized) != set(task_ids):
        raise SceneReviewError("scene review decision task coverage is incomplete")
    value["decisions"] = [normalized[task_id] for task_id in task_ids]
    return value


def _verify_contact_sheet(path: Path) -> None:
    if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
        raise SceneReviewError("scene contact sheet was not created")
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            if image.width < 1000 or image.height < 200:
                raise SceneReviewError("scene contact sheet dimensions are implausible")
    except OSError as error:
        raise SceneReviewError("scene contact sheet is not a decodable image") from error


def _current_assets(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str, dict[str, Any]]:
    try:
        director = compile_director_stage(root)
    except DirectorStageError as error:
        raise SceneReviewError(f"director stage is not current: {error}") from error
    tasks = _tasks(root)
    manifest_path = root / "06_visual_production/SCENE_ASSET_MANIFEST.json"
    if not manifest_path.is_file():
        raise SceneReviewError("scene asset manifest is missing")
    manifest = _manifest(root, _load(director.manifest_path, "director manifest")["release_id"], sha256_file(director.manifest_path), len(tasks))
    if len(manifest["assets"]) != len(tasks) or manifest.get("next_stage_status") != "awaiting_scene_review":
        raise SceneReviewError("not every production scene task has a registered real image")
    by_task = {item["task_id"]: item for item in manifest["assets"]}
    if set(by_task) != set(tasks):
        raise SceneReviewError("scene asset manifest task coverage differs from director tasks")
    return tasks, by_task, sha256_file(director.manifest_path), manifest


def build_scene_asset_review(
    project: Path,
    decision_path: Path,
    *,
    contact_sheet_runner: ContactSheetRunner | None = None,
) -> SceneReviewResult:
    root = project.expanduser().resolve()
    tasks, by_task, director_sha, manifest = _current_assets(root)
    manifest_path = root / "06_visual_production/SCENE_ASSET_MANIFEST.json"
    asset_sha = sha256_file(manifest_path)
    decision = _decision(
        decision_path.expanduser().resolve(),
        release_id=manifest["release_id"], director_sha=director_sha,
        asset_sha=asset_sha, task_ids=list(tasks),
    )
    output = safe_project_output(root, Path("06_visual_production/SCENE_CONTACT_SHEET.jpg"))
    report_path = safe_project_output(root, Path("06_visual_production/SCENE_REVIEW_REPORT.json"))
    stage_relative = "manifests/stages/scene_visual_review/scene-visual-review.json"
    stage_path = safe_project_output(root, Path(stage_relative))
    if report_path.exists() or output.exists() or stage_path.exists():
        if not (report_path.is_file() and output.is_file() and stage_path.is_file()):
            raise SceneReviewError("scene review transaction is incomplete")
        existing = _load(report_path, "scene review report")
        expected_assets = {task_id: by_task[task_id]["sha256"] for task_id in tasks}
        if (existing.get("schema_version") != "scene-review-report.v1"
                or existing.get("release_id") != manifest["release_id"]
                or existing.get("director_stage_manifest_sha256") != director_sha
                or existing.get("scene_asset_manifest_sha256") != asset_sha
                or existing.get("decision_sha256") != sha256_file(decision_path.expanduser().resolve())
                or existing.get("asset_hashes") != expected_assets
                or existing.get("decisions") != decision["decisions"]
                or existing.get("contact_sheet_sha256") != sha256_file(output)):
            raise SceneReviewError("existing scene review evidence is stale or modified")
        stage = _load(stage_path, "scene visual stage manifest")
        outputs = {item.get("path"): item.get("sha256") for item in stage.get("outputs", []) if isinstance(item, dict)}
        if (stage.get("stage") != "scene_visual_review"
                or outputs.get("06_visual_production/SCENE_CONTACT_SHEET.jpg") != sha256_file(output)
                or outputs.get("06_visual_production/SCENE_REVIEW_REPORT.json") != sha256_file(report_path)):
            raise SceneReviewError("existing scene review stage manifest is stale")
        return SceneReviewResult("unchanged", report_path, output, existing["next_stage_status"])
    images = [root / by_task[task_id]["path"] for task_id in tasks]
    runner = contact_sheet_runner or _default_contact_sheet
    with tempfile.TemporaryDirectory(prefix="scene-review-", dir=root.parent) as temp:
        staged_sheet = Path(temp) / "contact-sheet.jpg"
        runner(staged_sheet, images)
        _verify_contact_sheet(staged_sheet)
        decisions = {item["task_id"]: item for item in decision["decisions"]}
        all_pass = all(
            decisions[task_id][field] == "pass"
            for task_id in tasks
            for field in ("semantic_review_status", "reality_review_status", "identity_review_status")
        )
        contact_sha = sha256_file(staged_sheet)
        report = {
            "schema_version": "scene-review-report.v1",
            "release_id": manifest["release_id"],
            "director_stage_manifest_sha256": director_sha,
            "scene_asset_manifest_sha256": asset_sha,
            "decision_sha256": sha256_file(decision_path.expanduser().resolve()),
            "reviewer": decision["reviewer"],
            "task_count": len(tasks),
            "contact_sheet_path": "06_visual_production/SCENE_CONTACT_SHEET.jpg",
            "contact_sheet_sha256": contact_sha,
            "asset_hashes": {task_id: by_task[task_id]["sha256"] for task_id in tasks},
            "decisions": decision["decisions"],
            "machine_review_passed": all_pass,
            "human_approved": False,
            "next_stage_status": "awaiting_scene_visual_approval" if all_pass else "blocked_by_scene_review",
        }
        report_bytes = _pretty(report)
        stage = {
            "schema_version": "1.0",
            "manifest_id": "scene-visual-review",
            "project_id": root.name,
            "stage": "scene_visual_review",
            "release_id": manifest["release_id"],
            "producer": {"tool": "book-video-factory-production-visual-review"},
            "status": "success" if all_pass else "failed",
            "inputs": {
                "director_stage_manifest_sha256": director_sha,
                "scene_asset_manifest_sha256": asset_sha,
                "decision_sha256": report["decision_sha256"],
            },
            "outputs": [
                {"path": "06_visual_production/SCENE_CONTACT_SHEET.jpg", "bytes": staged_sheet.stat().st_size, "sha256": contact_sha},
                {"path": "06_visual_production/SCENE_REVIEW_REPORT.json", "bytes": len(report_bytes), "sha256": __import__("hashlib").sha256(report_bytes).hexdigest()},
            ],
            "checks": [
                {"id": "all_scene_assets_registered", "result": "pass", "severity": "error"},
                {"id": "semantic_reality_identity_review", "result": "pass" if all_pass else "fail", "severity": "error"},
                {"id": "hbg_contact_sheet", "result": "pass", "severity": "error"},
            ],
        }
        staged_report = Path(temp) / "report.json"; staged_report.write_bytes(report_bytes)
        staged_stage = Path(temp) / "stage.json"; staged_stage.write_bytes(_pretty(stage))
        output.parent.mkdir(parents=True, exist_ok=True); report_path.parent.mkdir(parents=True, exist_ok=True); stage_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged_sheet, output); os.replace(staged_report, report_path); os.replace(staged_stage, stage_path)
    return SceneReviewResult("created", report_path, output, report["next_stage_status"])




def _verify_existing_approval(
    root: Path, approval_path: Path, *, reviewer: str, note: str,
    tasks: Mapping[str, Any], by_task: Mapping[str, Mapping[str, Any]],
    director_sha: str, manifest_path: Path, report_path: Path, contact_path: Path,
) -> Path:
    approval = _load(approval_path, "scene asset approval")
    expected_fields = {
        "schema_version", "release_id", "reviewer", "note",
        "director_stage_manifest_sha256", "scene_asset_manifest_sha256",
        "scene_review_report_sha256", "contact_sheet_sha256",
        "approval_event_path", "approval_event_sha256", "asset_hashes",
        "human_approved", "next_stage_status",
    }
    if set(approval) != expected_fields or approval.get("schema_version") != "scene-asset-approval.v1":
        raise SceneReviewError("existing scene approval fields are invalid")
    if approval.get("reviewer") != reviewer or approval.get("note") != note:
        raise SceneReviewError("existing scene approval belongs to another decision")
    if approval.get("director_stage_manifest_sha256") != director_sha:
        raise SceneReviewError("existing scene approval director evidence is stale")
    if approval.get("scene_asset_manifest_sha256") != sha256_file(manifest_path):
        raise SceneReviewError("existing scene approval asset manifest is stale")
    if approval.get("scene_review_report_sha256") != sha256_file(report_path):
        raise SceneReviewError("existing scene approval review report is stale")
    if approval.get("contact_sheet_sha256") != sha256_file(contact_path):
        raise SceneReviewError("existing scene approval contact sheet is stale")
    expected_assets = {task_id: by_task[task_id]["sha256"] for task_id in tasks}
    if approval.get("asset_hashes") != expected_assets:
        raise SceneReviewError("existing scene approval asset hashes are stale")
    if approval.get("human_approved") is not True or approval.get("next_stage_status") != "ready_for_render":
        raise SceneReviewError("existing scene approval decision is invalid")
    event_relative = approval.get("approval_event_path")
    if not isinstance(event_relative, str):
        raise SceneReviewError("existing scene approval event path is invalid")
    event_path = safe_project_output(root, Path(event_relative))
    if event_path.is_symlink() or not event_path.is_file() or sha256_file(event_path) != approval.get("approval_event_sha256"):
        raise SceneReviewError("existing scene approval event is stale")
    event = _load(event_path, "scene approval event")
    if (event.get("gate") != "scene_visual" or event.get("decision") != "approved"
            or event.get("reviewer") != reviewer or event.get("note") != note):
        raise SceneReviewError("existing scene approval event does not match")
    return approval_path

def approve_scene_assets(
    project: Path,
    *,
    reviewer: str,
    note: str,
    contact_sheet_runner: ContactSheetRunner | None = None,
) -> Path:
    root = project.expanduser().resolve()
    if not isinstance(reviewer, str) or not reviewer.strip() or reviewer != reviewer.strip():
        raise SceneReviewError("approval reviewer is required")
    if not isinstance(note, str) or not note.strip() or note != note.strip():
        raise SceneReviewError("approval note is required")
    tasks, by_task, director_sha, manifest = _current_assets(root)
    report_path = root / "06_visual_production/SCENE_REVIEW_REPORT.json"
    report = _load(report_path, "scene review report")
    if report.get("schema_version") != "scene-review-report.v1" or not report.get("machine_review_passed"):
        raise SceneReviewError("scene review has not passed")
    manifest_path = root / "06_visual_production/SCENE_ASSET_MANIFEST.json"
    if report.get("director_stage_manifest_sha256") != director_sha or report.get("scene_asset_manifest_sha256") != sha256_file(manifest_path):
        raise SceneReviewError("scene review evidence is stale")
    if report.get("asset_hashes") != {task_id: by_task[task_id]["sha256"] for task_id in tasks}:
        raise SceneReviewError("scene review asset hashes are stale")
    contact_path = root / report.get("contact_sheet_path", "")
    if contact_path.is_symlink() or not contact_path.is_file() or sha256_file(contact_path) != report.get("contact_sheet_sha256"):
        raise SceneReviewError("scene contact sheet is stale")
    with tempfile.TemporaryDirectory(prefix="scene-approval-") as temp:
        fresh = Path(temp) / "contact-sheet.jpg"
        (contact_sheet_runner or _default_contact_sheet)(fresh, [root / by_task[task_id]["path"] for task_id in tasks])
        _verify_contact_sheet(fresh)
        if sha256_file(fresh) != report["contact_sheet_sha256"]:
            raise SceneReviewError("fresh HBG contact sheet differs from reviewed evidence")
    approval_path = safe_project_output(root, Path("06_visual_production/SCENE_ASSET_APPROVAL.json"))
    if approval_path.exists():
        return _verify_existing_approval(
            root, approval_path, reviewer=reviewer, note=note, tasks=tasks, by_task=by_task,
            director_sha=director_sha, manifest_path=manifest_path, report_path=report_path, contact_path=contact_path,
        )
    subjects = [report_path, manifest_path, contact_path, root / "05_director/DIRECTOR_STAGE_MANIFEST.json"]
    subjects.extend(root / by_task[task_id]["path"] for task_id in tasks)
    event_path = record_approval(
        root,
        release_id=manifest["release_id"],
        gate="scene_visual",
        decision="approved",
        reviewer=reviewer,
        subjects=subjects,
        evidence_refs=[str(report_path.relative_to(root)), str(contact_path.relative_to(root))],
        note=note,
    )
    approval = {
        "schema_version": "scene-asset-approval.v1",
        "release_id": manifest["release_id"],
        "reviewer": reviewer,
        "note": note,
        "director_stage_manifest_sha256": director_sha,
        "scene_asset_manifest_sha256": sha256_file(manifest_path),
        "scene_review_report_sha256": sha256_file(report_path),
        "contact_sheet_sha256": sha256_file(contact_path),
        "approval_event_path": event_path.relative_to(root).as_posix(),
        "approval_event_sha256": sha256_file(event_path),
        "asset_hashes": {task_id: by_task[task_id]["sha256"] for task_id in tasks},
        "human_approved": True,
        "next_stage_status": "ready_for_render",
    }
    approval_path.parent.mkdir(parents=True, exist_ok=True)
    approval_path.write_bytes(_pretty(approval))
    return approval_path
